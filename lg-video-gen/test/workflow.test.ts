import assert from "node:assert/strict";
import { test } from "node:test";
import { compileScenePrompt, normalizeCamera } from "../src/prompt.js";
import {
  buildManifestEdn, resolveModel, validateVideoRequest, waitForTask,
  type MiniMaxTransport, type TaskState, type VideoRequest,
} from "../src/minimax.js";

const request: VideoRequest = {
  model: "MiniMax-Hailuo-2.3",
  prompt: "A scene",
  duration: 6,
  resolution: "768P",
  prompt_optimizer: false,
};

test("h3 resolves to the official MiniMax model name", () => {
  assert.equal(resolveModel("h3"), "MiniMax-Hailuo-2.3");
  assert.throws(() => resolveModel("invented"));
});

test("scene prompt carries eye anatomy, motion, and official camera command", () => {
  const prompt = compileScenePrompt({
    prompt: "Ren notices the hidden process and slowly looks toward Nei.",
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
  assert.doesNotMatch(prompt, /Realization 0\.72/); // provenance is not a visual-intensity instruction
  assert.ok(prompt.length <= 2000);
});

test("camera and model constraints fail closed", () => {
  assert.deepEqual(normalizeCamera(["[Static shot]"]), ["Static shot"]);
  assert.throws(() => normalizeCamera(["Orbit wildly"]));
  assert.throws(() => normalizeCamera(["Pan left", "Tilt up", "Zoom in", "Shake"]));
  assert.throws(() => validateVideoRequest({ ...request, duration: 10, resolution: "1080P" }));
});

test("async workflow can resume and reaches a downloadable file", async () => {
  const states: TaskState[] = [
    { taskId: "t1", status: "Queueing" },
    { taskId: "t1", status: "Processing" },
    { taskId: "t1", status: "Success", fileId: "f1", width: 1366, height: 768 },
  ];
  const transport: MiniMaxTransport = {
    create: async () => "t1",
    query: async () => states.shift()!,
    retrieve: async () => ({ downloadUrl: "https://example.invalid/video.mp4" }),
    download: async () => new Uint8Array([0, 1, 2]),
  };
  const observed: string[] = [];
  const state = await waitForTask(transport, "t1", { intervalMs: 0, timeoutMs: 1000, onState: (s) => observed.push(s.status) });
  assert.equal(state.fileId, "f1");
  assert.deepEqual(observed, ["Queueing", "Processing", "Success"]);
});

test("manifest keeps the engine-independent beat/frame contract", () => {
  const edn = buildManifestEdn({
    request, promptSource: "scene.json", taskId: "t1", fileId: "f1",
    video: "scene.mp4", frames: ["t0.00.png", "t0.35.png"],
  });
  assert.match(edn, /:engine "minimax-h3"/);
  assert.match(edn, /:model "MiniMax-Hailuo-2.3"/);
  assert.match(edn, /:beats \[\{:beat\/id "scene"/);
  assert.match(edn, /"t0.35.png"/);
});
