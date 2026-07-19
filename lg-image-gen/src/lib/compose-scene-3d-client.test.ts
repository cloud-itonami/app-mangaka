/**
 * Pure-CPU unit tests for `compose-scene-3d-client.ts`. Runs via
 *
 *   node --test --import tsx/esm src/lib/compose-scene-3d-client.test.ts
 *
 * No network calls — the `fetch` global is stubbed where needed.
 */

import { test, beforeEach, afterEach } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import {
  composeScene3d,
  fetchBlob,
  fetchBestRenderToFile,
  type ComposeScene3dOutput,
} from "./compose-scene-3d-client.js";

const _origFetch = globalThis.fetch;
let _calls: { url: string; init?: RequestInit }[] = [];

function stubFetch(responder: (url: string, init?: RequestInit) => Response | Promise<Response>) {
  globalThis.fetch = (async (url: any, init?: any) => {
    _calls.push({ url: String(url), init });
    return responder(String(url), init);
  }) as typeof fetch;
}

beforeEach(() => {
  _calls = [];
});

afterEach(() => {
  globalThis.fetch = _origFetch;
});

test("composeScene3d posts JSON body to the pod's xrpc endpoint", async () => {
  stubFetch(async (url, init) => {
    assert.equal(url.endsWith("/xrpc/com.etzhayyim.mangaka.composeScene3d"), true);
    assert.equal((init?.headers as any)["Content-Type"], "application/json");
    assert.equal(init?.method, "POST");
    const body = JSON.parse(String(init?.body));
    assert.equal(body.panelRkey, "p-1");
    assert.equal(body.maxIter, 3);
    return new Response(
      JSON.stringify({ sceneRkey: "s-1", renders: [], iterations: 1 }),
      { status: 200 },
    );
  });
  const out = await composeScene3d({ panelRkey: "p-1", maxIter: 3 });
  assert.equal(out.sceneRkey, "s-1");
});

test("composeScene3d throws on HTTP 5xx", async () => {
  stubFetch(async () => new Response("upstream boom", { status: 500 }));
  await assert.rejects(
    () => composeScene3d({ panelRkey: "p-1" }),
    /composeScene3d HTTP 500/,
  );
});

test("composeScene3d surfaces tool-level error envelopes", async () => {
  stubFetch(async () =>
    new Response(
      JSON.stringify({
        sceneRkey: "",
        renders: [],
        iterations: 0,
        error: "panel p-1 not found",
      }),
      { status: 200 },
    ),
  );
  const out = await composeScene3d({ panelRkey: "p-1" });
  assert.equal(out.error, "panel p-1 not found");
});

test("composeScene3d omits optional fields when undefined", async () => {
  stubFetch(async (_url, init) => {
    const body = JSON.parse(String(init?.body));
    assert.equal("refineFromRkey" in body, false);
    assert.equal("maxIter" in body, false);
    assert.equal("simSeed" in body, false);
    return new Response(JSON.stringify({ sceneRkey: "s", renders: [], iterations: 0 }), { status: 200 });
  });
  await composeScene3d({ panelRkey: "p-1" });
});

test("fetchBlob requires B2_PUBLIC_BASE", async () => {
  delete process.env.B2_PUBLIC_BASE;
  await assert.rejects(() => fetchBlob("blobs/anonymous/abc"), /B2_PUBLIC_BASE/);
});

test("fetchBlob refuses pending-* blob keys", async () => {
  process.env.B2_PUBLIC_BASE = "https://blobs.example";
  await assert.rejects(() => fetchBlob("pending-foo-i1-a0"), /still pending/);
});

test("fetchBestRenderToFile returns null when all renders are placeholders", async () => {
  const out: ComposeScene3dOutput = {
    sceneRkey: "s",
    iterations: 1,
    renders: [
      { blobKey: "pending-x-i1-a0", score: 0 },
      { blobKey: "pending-x-i1-a1", score: 0 },
    ],
  };
  const r = await fetchBestRenderToFile(out);
  assert.equal(r, null);
});

test("fetchBestRenderToFile returns null when renders[] is empty", async () => {
  const out: ComposeScene3dOutput = { sceneRkey: "s", iterations: 0, renders: [] };
  const r = await fetchBestRenderToFile(out);
  assert.equal(r, null);
});

test("fetchBestRenderToFile picks the highest-scoring usable render and writes it", async () => {
  process.env.B2_PUBLIC_BASE = "https://blobs.example";
  const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00]);
  stubFetch(async (url) => {
    assert.equal(url, "https://blobs.example/blobs/anonymous/aaa");
    return new Response(png as any, { status: 200 });
  });
  const out: ComposeScene3dOutput = {
    sceneRkey: "s",
    iterations: 1,
    renders: [
      { blobKey: "pending-x-i1-a0", score: 0 },
      { blobKey: "blobs/anonymous/aaa", score: 0.81 },
      { blobKey: "blobs/anonymous/bbb", score: 0.43 },
    ],
  };
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "m3-3d-test-"));
  const filePath = await fetchBestRenderToFile(out, { tmpDir, filenameHint: "smoke" });
  assert.notEqual(filePath, null);
  assert.equal(fs.existsSync(filePath!), true);
  const written = fs.readFileSync(filePath!);
  assert.equal(written.byteLength, png.byteLength);
  fs.rmSync(tmpDir, { recursive: true, force: true });
});
