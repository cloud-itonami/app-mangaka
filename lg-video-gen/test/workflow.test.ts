import assert from "node:assert/strict";
import { createServer } from "node:http";
import { test } from "node:test";
import { compileScenePrompt, normalizeCamera } from "../src/prompt.js";
import {
  MurakumoClient, buildManifestEdn, resolveModel, snapH3Frames, validateVideoRequest, waitForTask,
  type MurakumoTransport, type TaskState, type VideoRequest,
} from "../src/murakumo.js";
import { planStoryboard } from "../src/storyboard.js";

const request: VideoRequest = {
  type: "video",
  model: "minimax-h3",
  prompt: "A scene",
  input: { prompt: "A scene" },
  params: { duration_ms: 5000, width: 640, height: 640, frames: 124, steps: 20 },
  actor: "mangaka:test",
};

test("h3 resolves to Murakumo's verified self-hosted model", () => {
  assert.equal(resolveModel("h3"), "minimax-h3");
  assert.equal(snapH3Frames(5), 124);
  assert.equal(snapH3Frames(6), 158);
  assert.throws(() => resolveModel("invented"));
});

test("scene prompt carries eye anatomy, motion, and official camera command", () => {
  const prompt = compileScenePrompt({
    prompt: "Ren notices the hidden process and slowly looks toward Nei.",
    shots: [
      { at: 0, description: "wide room view" },
      { at: 3, description: "cut to Ren's eyes" },
    ],
    setting: "a dark incident response room before dawn",
    atmosphere: "monitor light, dry still air, dust visible in the beam",
    camera: ["Push in", "Pan right"],
    characters: [{
      name: "Ren",
      identity: "17-year-old Japanese boy, raven hair, charcoal hoodie",
      action: "his hand stops above the keyboard, then his gaze shifts",
      face: {
        rig: "wide-reveal", intensity: 0.7, mouth: "small-open",
        eyes: { open: 0.96, brow: "inner-raised", pupil: 0.82, gaze: "side", highlight: "double" },
        dimensions: [{ label: "Realization", confidence: 0.72 }],
      },
    }],
  });
  assert.match(prompt, /eye openness 0\.96/);
  assert.match(prompt, /pupil ratio 0\.82/);
  assert.match(prompt, /\[Push in,Pan right\]/);
  assert.match(prompt, /SHOT 2 at 3\.00s/);
  assert.match(prompt, /clearly readable shot changes/);
  assert.doesNotMatch(prompt, /one continuous/);
  assert.doesNotMatch(prompt, /Realization 0\.72/); // provenance is not a visual-intensity instruction
  assert.ok(prompt.length <= 2000);
});

test("camera and model constraints fail closed", () => {
  assert.deepEqual(normalizeCamera(["[Static shot]"]), ["Static shot"]);
  assert.throws(() => normalizeCamera(["Orbit wildly"]));
  assert.throws(() => normalizeCamera(["Pan left", "Tilt up", "Zoom in", "Shake"]));
  assert.throws(() => validateVideoRequest({ ...request, params: { ...request.params, frames: 123 } }));
  assert.throws(() => validateVideoRequest({ ...request, params: { ...request.params, width: 650 } }));
});

test("async workflow can resume and reaches a downloadable file", async () => {
  const states: TaskState[] = [
    { taskId: "t1", status: "queued" },
    { taskId: "t1", status: "running", progress: 50 },
    { taskId: "t1", status: "done", progress: 100, artifactUrl: "https://example.invalid/video.mp4" },
  ];
  const transport: MurakumoTransport = {
    create: async () => "t1",
    query: async () => states.shift()!,
    download: async () => new Uint8Array([0, 1, 2]),
  };
  const observed: string[] = [];
  const state = await waitForTask(transport, "t1", { intervalMs: 0, timeoutMs: 1000, onState: (s) => observed.push(s.status) });
  assert.equal(state.artifactUrl, "https://example.invalid/video.mp4");
  assert.deepEqual(observed, ["queued", "running", "done"]);
});

test("H3 client uses Murakumo generation endpoints and bearer auth", async () => {
  const paths: string[] = [];
  const auth: Array<string | undefined> = [];
  const bodies: any[] = [];
  const server = createServer((incoming, response) => {
    paths.push(`${incoming.method} ${incoming.url}`);
    auth.push(incoming.headers.authorization);
    const chunks: Buffer[] = [];
    incoming.on("data", (chunk) => chunks.push(chunk));
    incoming.on("end", () => {
      const body = Buffer.concat(chunks).toString();
      if (body) bodies.push(JSON.parse(body));
      if (incoming.url?.endsWith("/artifact")) {
        response.end(Buffer.from([0, 1, 2]));
      } else {
        response.setHeader("content-type", "application/json");
        if (incoming.method === "POST") response.end(JSON.stringify({ jobId: "h3-task", status: "queued" }));
        else response.end(JSON.stringify({
          jobId: "h3-task", status: "done", progress: 100,
          artifacts: [{ url: "https://other.murakumo.cloud/artifact", contentHash: "sha256:abc" }],
        }));
      }
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const address = server.address();
    assert.ok(address && typeof address !== "string");
    const client = new MurakumoClient("test-key", `http://127.0.0.1:${address.port}`);
    assert.equal(await client.create(request), "h3-task");
    const state = await client.query("h3-task");
    assert.equal(state.status, "done");
    assert.equal(state.contentHash, "sha256:abc");
    assert.deepEqual(await client.download("h3-task"), new Uint8Array([0, 1, 2]));
    assert.deepEqual(paths, [
      "POST /api/v1/generation",
      "GET /api/v1/generation/jobs/h3-task",
      "GET /api/v1/generation/jobs/h3-task/artifact",
    ]);
    assert.deepEqual(auth, ["Bearer test-key", "Bearer test-key", "Bearer test-key"]);
    assert.equal(bodies[0].model, "minimax-h3");
    assert.equal(bodies[0].params.frames, 124);
  } finally {
    await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  }
});

test("manifest keeps the engine-independent beat/frame contract", () => {
  const edn = buildManifestEdn({
    request, promptSource: "scene.json", taskId: "t1",
    video: "scene.mp4", frames: ["t0.00.png", "t0.35.png"],
  });
  assert.match(edn, /:provider "murakumo"/);
  assert.match(edn, /:engine "murakumo-h3"/);
  assert.match(edn, /:model "minimax-h3"/);
  assert.match(edn, /:frame-selection "motion-peaks-and-scene-transitions"/);
  assert.match(edn, /:beats \[\{:beat\/id "scene"/);
  assert.match(edn, /"t0.35.png"/);
});

test("storyboard selects each scene motion peak and brackets the cut", () => {
  const storyboard = planStoryboard(5, [
    { time: 0.1, score: 1 }, { time: 1.2, score: 30 }, { time: 2.3, score: 2 },
    { time: 2.7, score: 3 }, { time: 3.8, score: 90 }, { time: 4.9, score: 1 },
  ], [{ time: 2.5, score: 0.8 }, { time: 2.56, score: 0.4 }], 8);
  const peaks = storyboard.panels.filter((panel) => panel.role === "action-peak");
  assert.deepEqual(peaks.map((panel) => panel.time), [1.2, 3.8]);
  assert.equal(peaks.find((panel) => panel.time === 3.8)?.size, "splash");
  assert.deepEqual(
    storyboard.panels.filter((panel) => panel.role.startsWith("transition")).map((panel) => panel.role),
    ["transition-out", "transition-in"],
  );
  assert.equal(storyboard.readingDirection, "right-to-left-top-to-bottom");
  assert.equal(storyboard.cuts.length, 1, "adjacent cut detections should collapse into one transition");
  const transition = storyboard.panels.find((panel) => panel.role === "transition-out")!;
  const nextScene = storyboard.panels.find((panel) => panel.role === "transition-in")!;
  assert.ok(transition.rect!.x > nextScene.rect!.x, "cut-before panel should be on the right in Japanese reading order");
});
