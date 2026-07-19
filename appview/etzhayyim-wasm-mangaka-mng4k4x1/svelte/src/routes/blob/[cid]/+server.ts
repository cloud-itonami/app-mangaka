// GET /blob/:cid?did=anonymous — direct SigV4 fetch from B2.
// PDS uploadBlob writes blobs at `blobs/{repo}/{cid}` in the etzhayyim-cdn bucket.
// PDS sync.getBlob is currently unavailable (522), so we read B2 directly.
// 3x exponential-backoff retry + edge cache via tee() pass-through.

import { AwsClient } from "aws4fetch";

interface Env {
  B2_ENDPOINT?: string;
  B2_BUCKET?: string;
  B2_REGION?: string;
  B2_KEY_ID?: string | { get(): Promise<string> };
  B2_APP_KEY?: string | { get(): Promise<string> };
}

async function readSecret(binding: string | { get(): Promise<string> } | undefined): Promise<string> {
  if (!binding) return "";
  return typeof binding === "string" ? binding : await binding.get();
}

async function fetchBlobFromB2(env: Env, did: string, cid: string): Promise<Response> {
  const endpoint = env.B2_ENDPOINT || "s3.us-west-004.backblazeb2.com";
  const bucket = env.B2_BUCKET || "etzhayyim-cdn";
  const region = env.B2_REGION || "us-west-004";
  const keyId = await readSecret(env.B2_KEY_ID);
  const appKey = await readSecret(env.B2_APP_KEY);
  if (!keyId || !appKey) return new Response("B2 credentials not configured", { status: 503 });
  const aws = new AwsClient({ accessKeyId: keyId, secretAccessKey: appKey, service: "s3", region });
  const encode = (k: string) => k.split("/").map(encodeURIComponent).join("/");
  const candidates = [`blobs/${did}/${cid}`, `blobs/${cid}`];
  async function tryFetch(key: string): Promise<Response | null> {
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        const upstream = await aws.fetch(`https://${endpoint}/${bucket}/${encode(key)}`, { method: "GET" });
        if (upstream.status === 404 || upstream.status === 403) return null;
        if (upstream.ok) return upstream;
        if (attempt === 2) return upstream;
      } catch (e) { if (attempt === 2) throw e; }
      await new Promise(r => setTimeout(r, 100 * (attempt + 1)));
    }
    return null;
  }
  for (const key of candidates) {
    let upstream: Response | null = null;
    try { upstream = await tryFetch(key); }
    catch (e) { return new Response(`b2 fetch threw: ${String(e).slice(0,80)}`, { status: 502, headers: { "access-control-allow-origin": "*" } }); }
    if (!upstream) continue;
    if (!upstream.ok) return new Response(`b2 origin ${upstream.status}`, { status: 502, headers: { "access-control-allow-origin": "*" } });
    const headers = new Headers();
    headers.set("content-type", upstream.headers.get("content-type") || "application/octet-stream");
    headers.set("cache-control", "public, max-age=31536000, immutable");
    headers.set("access-control-allow-origin", "*");
    const etag = upstream.headers.get("etag");
    if (etag) headers.set("etag", etag);
    return new Response(upstream.body, { status: 200, headers });
  }
  return new Response("not found", { status: 404, headers: { "access-control-allow-origin": "*" } });
}

export async function GET({ params, url, platform }: { params: { cid: string }; url: URL; platform: { env: Env; ctx: { waitUntil(p: Promise<unknown>): void } } }): Promise<Response> {
  const cid = params.cid;
  const did = (url.searchParams.get("did") || "anonymous").trim();
  if (!cid) return new Response("cid required", { status: 400 });
  const env = platform.env;
  // CF edge cache lookup first (per-colo).
  const caches = (globalThis as any).caches as { default: { match(req: Request): Promise<Response | undefined>; put(req: Request, res: Response): Promise<void> } };
  const cache = caches?.default;
  const cacheKey = new Request(`https://mangaka.etzhayyim.com/blob/${cid}?did=${did}`, { method: "GET" });
  if (cache) {
    const hit = await cache.match(cacheKey);
    if (hit) {
      const h = new Headers(hit.headers); h.set("x-cache", "HIT");
      return new Response(hit.body, { status: hit.status, headers: h });
    }
  }
  const fresh = await fetchBlobFromB2(env, did, cid);
  if (fresh.ok && fresh.body && cache && platform.ctx?.waitUntil) {
    const [forClient, forCache] = fresh.body.tee();
    const h = new Headers(fresh.headers); h.set("x-cache", "MISS");
    platform.ctx.waitUntil(cache.put(cacheKey, new Response(forCache, { status: 200, headers: fresh.headers })));
    return new Response(forClient, { status: 200, headers: h });
  }
  return fresh;
}

export const HEAD = GET;
