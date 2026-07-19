/**
 * Client for `com.etzhayyim.mangaka.composeScene3d` — calls the LangGraph
 * pod's `/xrpc/{nsid}` endpoint, fetches the chosen render's PNG from B2,
 * writes it to a local temp file so the existing `edit()` helper (which
 * takes file paths) can consume it.
 *
 * P14 of ADR-2605141200 — M3 hybrid pipeline. The 3D render becomes the
 * reference image for the gpt-image-2 / Gemini 3 Pro Image diffusion
 * finish. Lets us race M2+ref (pure 2D) against M3-3D (3D-ref → 2D
 * diffusion) on the same panel for the η-score amortisation experiment
 * called out in ADR-0057.
 *
 * Env:
 *   LG_MANGAKA_BASE   default `http://lg-mangaka.default.svc.cluster.local:8000`
 *                     — pod-internal address, falls back to a public host
 *                     when set (CI / local-dev).
 *   LG_API_KEY        optional shared secret enforced by the pod's
 *                     `/runs` and `/xrpc/*` routes.
 *   B2_PUBLIC_BASE    base URL for fetching blobs by key (e.g.
 *                     `https://blobs.etzhayyim.com` or the raw B2 endpoint).
 *                     Required when using `fetchRenderToFile`.
 */

import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";

// Env is read at call time so tests + ops can override per-invocation.
function _podBase(): string {
  return (process.env.LG_MANGAKA_BASE ?? "http://lg-mangaka.default.svc.cluster.local:8000").replace(/\/$/, "");
}
function _apiKey(): string {
  return process.env.LG_API_KEY ?? "";
}
function _b2PublicBase(): string {
  return (process.env.B2_PUBLIC_BASE ?? "").replace(/\/$/, "");
}

export interface ComposeScene3dInput {
  panelRkey: string;
  refineFromRkey?: string;
  maxIter?: number;
  simSeed?: number;
  renderAngles?: number;
}

export interface RenderEntry {
  blobKey: string;
  depthBlobKey?: string | null;
  outlineBlobKey?: string | null;
  score?: number;
  angle?: string;
}

export interface ComposeScene3dOutput {
  sceneRkey: string;
  panelRkey?: string;
  renders: RenderEntry[];
  iterations: number;
  tookMs?: number;
  /** Present when the pod returned an error envelope instead of success. */
  error?: string;
}

/**
 * POST `/xrpc/com.etzhayyim.mangaka.composeScene3d` against the lg-mangaka
 * pod. The pod's dispatcher routes the NSID through its `_TOOL_NSID_TO_HANDLER`
 * or its langgraph runs path, depending on what's wired up — the M3
 * client doesn't care which, only the response shape.
 *
 * @throws on transport or HTTP 5xx; tool-level errors are surfaced via
 *         `output.error` so callers can decide whether to retry or fall
 *         back to the M2 path.
 */
export async function composeScene3d(input: ComposeScene3dInput): Promise<ComposeScene3dOutput> {
  const url = `${_podBase()}/xrpc/com.etzhayyim.mangaka.composeScene3d`;
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const apiKey = _apiKey();
  if (apiKey) headers["x-api-key"] = apiKey;

  const body = {
    panelRkey: input.panelRkey,
    ...(input.refineFromRkey ? { refineFromRkey: input.refineFromRkey } : {}),
    ...(input.maxIter !== undefined ? { maxIter: input.maxIter } : {}),
    ...(input.simSeed !== undefined ? { simSeed: input.simSeed } : {}),
    ...(input.renderAngles !== undefined ? { renderAngles: input.renderAngles } : {}),
  };

  const r = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    throw new Error(`composeScene3d HTTP ${r.status}: ${(await r.text()).slice(0, 300)}`);
  }
  const j = (await r.json()) as ComposeScene3dOutput;
  return j;
}

/**
 * Fetch a render's PNG bytes by blob key. Uses `B2_PUBLIC_BASE` so the
 * caller doesn't need SigV4 — the bucket is public-read for renders by
 * convention (`blobs/anonymous/{sha256hex}` is content-addressed and
 * carries no PII).
 */
export async function fetchBlob(blobKey: string): Promise<Uint8Array> {
  const base = _b2PublicBase();
  if (!base) {
    throw new Error("fetchBlob: B2_PUBLIC_BASE not configured");
  }
  if (blobKey.startsWith("pending-")) {
    throw new Error(`fetchBlob: blob still pending (${blobKey}) — render not produced yet`);
  }
  const url = `${base}/${blobKey}`;
  const r = await fetch(url);
  if (!r.ok) {
    throw new Error(`fetchBlob HTTP ${r.status} for ${blobKey}: ${(await r.text()).slice(0, 200)}`);
  }
  const buf = new Uint8Array(await r.arrayBuffer());
  return buf;
}

/**
 * Convenience wrapper: pick the highest-scoring render from a
 * `composeScene3d` response, fetch its PNG, write it to a temp file, and
 * return the path so it can be passed to `edit()` directly.
 *
 * Returns `null` when every render is a placeholder (`pending-*`) — caller
 * should fall back to the M2+ref path.
 */
export async function fetchBestRenderToFile(
  out: ComposeScene3dOutput,
  opts: { tmpDir?: string; filenameHint?: string } = {},
): Promise<string | null> {
  if (!out.renders || out.renders.length === 0) return null;
  const usable = out.renders.filter((r) => !!r.blobKey && !r.blobKey.startsWith("pending-"));
  if (usable.length === 0) return null;
  const best = usable.reduce(
    (acc, r) => ((r.score ?? 0) > (acc.score ?? 0) ? r : acc),
    usable[0],
  );
  const bytes = await fetchBlob(best.blobKey);
  const tmpDir = opts.tmpDir ?? fs.mkdtempSync(path.join(os.tmpdir(), "lg-image-gen-m3-3d-"));
  const filename = `${opts.filenameHint ?? "scene3d"}_${path.basename(best.blobKey)}.png`;
  const outPath = path.join(tmpDir, filename);
  fs.writeFileSync(outPath, bytes);
  return outPath;
}
