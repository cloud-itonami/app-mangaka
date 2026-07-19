// /xrpc/:nsid — proxy com.etzhayyim.mangaka.* requests to bpmn-dispatcher.
// Restored after the SvelteKit-Cloudflare adapter migration overwrote the
// custom worker (src/app.ts) that previously handled this routing.

const NSID_PREFIX = "com.etzhayyim.mangaka.";

interface Env {
  DISPATCHER_URL?: string;
  DISPATCHER_INTERNAL_SECRET?: string | { get(): Promise<string> };
}

async function readSecret(binding: string | { get(): Promise<string> } | undefined): Promise<string> {
  if (!binding) return "";
  try {
    return typeof binding === "string" ? binding : await binding.get();
  } catch {
    return "";
  }
}

async function readBodyWithQuery(request: Request, url: URL): Promise<Record<string, unknown> | { __invalid: true }> {
  let body: Record<string, unknown> = {};
  if (request.method === "POST") {
    try {
      const text = await request.text();
      body = text ? (JSON.parse(text) as Record<string, unknown>) : {};
    } catch {
      return { __invalid: true };
    }
  }
  for (const [k, v] of url.searchParams) if (!(k in body)) body[k] = v;
  return body;
}

async function proxy(request: Request, url: URL, params: { nsid: string }, env: Env): Promise<Response> {
  const nsid = params.nsid || "";
  if (!nsid.startsWith(NSID_PREFIX)) {
    return new Response(JSON.stringify({ error: "UnknownNSID", nsid }), {
      status: 404,
      headers: { "content-type": "application/json" },
    });
  }
  const body = await readBodyWithQuery(request, url);
  if ((body as any).__invalid) {
    return new Response(JSON.stringify({ error: "InvalidJson" }), {
      status: 400,
      headers: { "content-type": "application/json" },
    });
  }
  const base = (env.DISPATCHER_URL ?? "https://dispatcher.etzhayyim.com").replace(/\/+$/, "");
  const headers: Record<string, string> = { "content-type": "application/json" };
  const trust = await readSecret(env.DISPATCHER_INTERNAL_SECRET);
  if (trust) headers["x-internal-trust"] = trust;
  const upstream = await fetch(`${base}/xrpc/${nsid}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  const text = await upstream.text();
  return new Response(text, {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "application/json",
      "cache-control": "no-store",
    },
  });
}

export async function POST({ request, url, params, platform }: any) {
  return proxy(request, url, params, platform.env);
}
export async function GET({ request, url, params, platform }: any) {
  return proxy(request, url, params, platform.env);
}
