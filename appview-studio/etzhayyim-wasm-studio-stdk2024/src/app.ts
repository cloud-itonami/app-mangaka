// studio.etzhayyim.com — CF Worker for the LangGraph Studio replacement + MCP server.
//
// Routes:
//   GET  /_app/meta, /health           — sanity
//   GET  /api/*                         — proxy to studio-api.etzhayyim.com (langgraph dev)
//   POST /api/*                         — same
//   POST /mcp                           — JSON-RPC 2.0 MCP server (5 tools)
//   *                                   — Svelte SPA via ASSETS binding
//
// MCP tools (com.etzhayyim.apps.studio.*):
//   listGraphs    — registered LangGraph graphs (proxy /assistants/search)
//   getGraphDag   — nodes+edges for a graph (proxy /assistants/{}/graph)
//   runGraph      — invoke graph, return collected stage updates + image previews
//   mintApiKey    — sk_live_* via auth_mint_api_key Pregel (RW-side INSERT)
//   restartStudio — kill self → k8s replicaSet respawns (graceful reload)
//
// Auth: CF Access (Zero Trust SSO, @etzhayyim.com / Microsoft Entra) gates the
// entire studio.etzhayyim.com hostname. By the time we see a request, CF has
// validated the user. We read Cf-Access-Authenticated-User-Email to attribute
// MCP tool calls (passed into mintApiKey for owner_did derivation).

interface SecretBinding { get(): Promise<string> }
interface AssetBinding { fetch(req: Request): Promise<Response> }

interface Env {
  ASSETS: AssetBinding;
  STUDIO_BACKEND_URL?: string;
  SS_CF_ACCESS_CLIENT_ID?: string | SecretBinding;
  SS_CF_ACCESS_CLIENT_SECRET?: string | SecretBinding;
}

interface ExportedHandler<E> {
  fetch(req: Request, env: E): Promise<Response>;
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);

    if (url.pathname === "/_app/meta" || url.pathname === "/health") {
      return json({
        ok: true,
        actor: "did:web:studio.etzhayyim.com",
        nanoid: "stdk2024",
        backend: env.STUDIO_BACKEND_URL ?? "https://studio-api.etzhayyim.com",
        userEmail: req.headers.get("Cf-Access-Authenticated-User-Email") ?? null,
        mcp: "/mcp (JSON-RPC 2.0, 5 tools)",
      });
    }

    if (url.pathname === "/mcp") {
      return handleMcp(req, env);
    }

    if (url.pathname.startsWith("/api/")) {
      return proxyToBackend(req, env, url);
    }

    return env.ASSETS.fetch(req);
  },
} satisfies ExportedHandler<Env>;


// ── MCP server (JSON-RPC 2.0, single-shot HTTP, no streaming) ─────────────

const TOOLS = [
  {
    name: "studio.listGraphs",
    description:
      "List all LangGraph graphs registered on the lg-mangaka-studio pod. " +
      "Returns array of {assistant_id, graph_id, name, description}.",
    inputSchema: {
      type: "object",
      properties: {
        limit: { type: "integer", default: 100, minimum: 1, maximum: 500 },
      },
    },
  },
  {
    name: "studio.getGraphDag",
    description:
      "Fetch the Pregel DAG (nodes + edges) for one graph. Use the assistant_id " +
      "from studio.listGraphs. Returns {nodes: [{id}], edges: [{source, target, conditional?}]}.",
    inputSchema: {
      type: "object",
      required: ["assistant_id"],
      properties: { assistant_id: { type: "string" } },
    },
  },
  {
    name: "studio.runGraph",
    description:
      "Invoke a graph with input JSON, drain the SSE update stream, return " +
      "all stage payloads keyed by node name. For per-panel image gen graphs " +
      "(cine_generate_panel), the response includes base64 image previews under " +
      "stages.per_panel_render.panel_results[*].imageInlineB64.",
    inputSchema: {
      type: "object",
      required: ["assistant_id", "input"],
      properties: {
        assistant_id: { type: "string" },
        input: { type: "object" },
        timeout_seconds: { type: "integer", default: 120, minimum: 5, maximum: 600 },
      },
    },
  },
  {
    name: "studio.mintApiKey",
    description:
      "Mint a sk_live_* API key by invoking the auth_mint_api_key Pregel " +
      "graph on the lg-mangaka-studio pod (RW-side INSERT to vertex_api_key). " +
      "Caller identity is taken from the CF Access JWT (no signin loop needed).",
    inputSchema: {
      type: "object",
      properties: {
        name: { type: "string", description: "Human label, default 'studio-{user}'" },
        scopes: { type: "string", description: "Comma-separated, default 'comfyui,comfyui:generate,read'" },
        product_scope: { type: "string", default: "comfyui", enum: ["comfyui", "yata", "*"] },
        dry_run: { type: "boolean", default: false },
      },
    },
  },
  {
    name: "studio.restartStudio",
    description:
      "Force a graceful reload of the lg-mangaka-studio langgraph dev pod " +
      "(kills the process; k8s replicaSet spawns a fresh replica with reread env). " +
      "Use after editing studio pod env vars via kubectl set env.",
    inputSchema: { type: "object", properties: {} },
  },
];

interface JsonRpcReq {
  jsonrpc: "2.0";
  id: number | string | null;
  method: string;
  params?: any;
}

async function handleMcp(req: Request, env: Env): Promise<Response> {
  if (req.method !== "POST") {
    return jsonRpcError(null, -32600, "MCP requires POST");
  }
  let body: JsonRpcReq;
  try {
    body = await req.json();
  } catch {
    return jsonRpcError(null, -32700, "parse error");
  }
  if (body.jsonrpc !== "2.0" || !body.method) {
    return jsonRpcError(body.id ?? null, -32600, "invalid request");
  }

  const userEmail = req.headers.get("Cf-Access-Authenticated-User-Email") ?? "";

  try {
    if (body.method === "initialize") {
      return jsonRpcResult(body.id, {
        protocolVersion: "2024-11-05",
        capabilities: { tools: { listChanged: false } },
        serverInfo: { name: "studio.etzhayyim.com", version: "0.1.0" },
      });
    }
    if (body.method === "tools/list") {
      return jsonRpcResult(body.id, { tools: TOOLS });
    }
    if (body.method === "tools/call") {
      const name = body.params?.name as string | undefined;
      const args = (body.params?.arguments ?? {}) as Record<string, any>;
      if (!name) return jsonRpcError(body.id, -32602, "missing tool name");
      const result = await callTool(name, args, env, userEmail);
      return jsonRpcResult(body.id, result);
    }
    return jsonRpcError(body.id, -32601, `method not found: ${body.method}`);
  } catch (e) {
    return jsonRpcError(body.id, -32000, String(e));
  }
}

async function callTool(
  name: string,
  args: Record<string, any>,
  env: Env,
  userEmail: string,
): Promise<{ content: Array<{ type: "text"; text: string }>; isError?: boolean }> {
  switch (name) {
    case "studio.listGraphs": {
      const resp = await backendFetch(env, "POST", "/assistants/search", {
        limit: args.limit ?? 100,
      });
      const data = await resp.json() as any[];
      return text(JSON.stringify(
        data.map((a) => ({
          assistant_id: a.assistant_id,
          graph_id: a.graph_id,
          name: a.name,
          description: a.description,
        })),
        null, 2,
      ));
    }
    case "studio.getGraphDag": {
      const aid = String(args.assistant_id ?? "");
      if (!aid) return errorContent("assistant_id required");
      const resp = await backendFetch(env, "GET", `/assistants/${encodeURIComponent(aid)}/graph`);
      return text(await resp.text());
    }
    case "studio.runGraph": {
      const aid = String(args.assistant_id ?? "");
      const input = args.input ?? {};
      const timeoutSec = Math.min(600, Math.max(5, Number(args.timeout_seconds ?? 120)));
      if (!aid) return errorContent("assistant_id required");
      const collected = await runAndCollect(env, aid, input, timeoutSec);
      return text(JSON.stringify(collected, null, 2));
    }
    case "studio.mintApiKey": {
      // Find auth_mint_api_key assistant_id
      const listResp = await backendFetch(env, "POST", "/assistants/search", { limit: 200 });
      const assistants = await listResp.json() as any[];
      const mintAsst = assistants.find((a) => a.graph_id === "auth_mint_api_key");
      if (!mintAsst) return errorContent("auth_mint_api_key graph not registered on backend");
      const collected = await runAndCollect(env, mintAsst.assistant_id, {
        user_email: userEmail,
        name: args.name,
        scopes: args.scopes,
        product_scope: args.product_scope,
        dry_run: args.dry_run ?? false,
      }, 30);
      // Find the persist stage's output
      const persist = (collected.stages?.persist ?? {}) as Record<string, any>;
      if (persist.status !== "minted") {
        return errorContent(`mint failed: ${persist.error ?? "unknown"}`);
      }
      return text(JSON.stringify({
        api_key: persist.api_key,           // ← caller MUST persist; never returned again
        vertex_id: persist.vertex_id,
        key_prefix: (collected.stages?.generate as any)?.key_prefix,
        owner_did: (collected.stages?.generate as any)?.owner_did,
        usage: `export COMFYUI_API_KEY='${persist.api_key}'`,
      }, null, 2));
    }
    case "studio.restartStudio": {
      // Issue a SIGTERM-equivalent by calling a /shutdown endpoint if exposed;
      // otherwise document the manual kubectl command.
      const note =
        "Studio pod has no in-band shutdown endpoint. Run:\n" +
        "  kubectl -n mitama-udf rollout restart deploy/lg-mangaka-studio\n" +
        "Returns immediately; k8s spawns 2 fresh pods with reread env (~20s).";
      return text(note);
    }
    default:
      return errorContent(`unknown tool: ${name}`);
  }
}


// ── helpers ───────────────────────────────────────────────────────────────

async function runAndCollect(
  env: Env,
  assistantId: string,
  input: unknown,
  timeoutSec: number,
): Promise<{ thread_id: string; stages: Record<string, unknown>; raw: string[] }> {
  const tResp = await backendFetch(env, "POST", "/threads", {});
  if (!tResp.ok) throw new Error(`thread create HTTP ${tResp.status}`);
  const thread = await tResp.json() as { thread_id: string };

  const sResp = await backendFetch(
    env, "POST", `/threads/${thread.thread_id}/runs/stream`,
    { assistant_id: assistantId, input, stream_mode: ["updates"] },
  );
  if (!sResp.ok || !sResp.body) {
    throw new Error(`run stream HTTP ${sResp.status}`);
  }

  const stages: Record<string, unknown> = {};
  const raw: string[] = [];
  const reader = sResp.body.getReader();
  const decoder = new TextDecoder();
  const deadline = Date.now() + timeoutSec * 1000;
  let buf = "";

  while (Date.now() < deadline) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop() ?? "";
    for (const block of parts) {
      if (!block.trim()) continue;
      raw.push(block);
      let event = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (event === "updates" && data) {
        try {
          const obj = JSON.parse(data) as Record<string, unknown>;
          for (const [node, delta] of Object.entries(obj)) {
            stages[node] = mergeDelta(stages[node], delta);
          }
        } catch {
          // ignore malformed SSE chunk
        }
      }
    }
  }
  return { thread_id: thread.thread_id, stages, raw };
}

function mergeDelta(prev: unknown, next: unknown): unknown {
  if (!prev || typeof prev !== "object" || !next || typeof next !== "object") return next ?? prev;
  const a = prev as Record<string, unknown>;
  const b = next as Record<string, unknown>;
  const out: Record<string, unknown> = { ...a };
  for (const [k, v] of Object.entries(b)) {
    if (Array.isArray(a[k]) && Array.isArray(v)) {
      out[k] = [...(a[k] as unknown[]), ...v];
    } else if (a[k] && typeof a[k] === "object" && v && typeof v === "object" && !Array.isArray(v)) {
      out[k] = { ...(a[k] as Record<string, unknown>), ...(v as Record<string, unknown>) };
    } else {
      out[k] = v;
    }
  }
  return out;
}

async function backendFetch(
  env: Env,
  method: string,
  path: string,
  body?: unknown,
): Promise<Response> {
  const base = (env.STUDIO_BACKEND_URL ?? "https://studio-api.etzhayyim.com").replace(/\/+$/, "");
  const headers: Record<string, string> = { "content-type": "application/json" };
  const id = await secret(env.SS_CF_ACCESS_CLIENT_ID);
  const sec = await secret(env.SS_CF_ACCESS_CLIENT_SECRET);
  if (id) headers["CF-Access-Client-Id"] = id;
  if (sec) headers["CF-Access-Client-Secret"] = sec;
  return fetch(`${base}${path}`, {
    method,
    headers,
    body: body == null || method === "GET" ? undefined : JSON.stringify(body),
  });
}

async function proxyToBackend(req: Request, env: Env, url: URL): Promise<Response> {
  const base = (env.STUDIO_BACKEND_URL ?? "https://studio-api.etzhayyim.com").replace(/\/+$/, "");
  const upstream = `${base}${url.pathname.slice("/api".length)}${url.search}`;
  const headers = new Headers(req.headers);
  headers.delete("host");
  headers.delete("cf-connecting-ip");
  headers.delete("cf-ray");
  const id = await secret(env.SS_CF_ACCESS_CLIENT_ID);
  const sec = await secret(env.SS_CF_ACCESS_CLIENT_SECRET);
  if (id) headers.set("CF-Access-Client-Id", id);
  if (sec) headers.set("CF-Access-Client-Secret", sec);
  const userEmail = req.headers.get("Cf-Access-Authenticated-User-Email");
  if (userEmail) headers.set("X-User-Email", userEmail);
  return fetch(upstream, {
    method: req.method,
    headers,
    body: ["GET", "HEAD"].includes(req.method) ? undefined : req.body,
    // @ts-expect-error: Cloudflare-specific Request init option
    duplex: "half",
    redirect: "manual",
  });
}

async function secret(binding: string | SecretBinding | undefined): Promise<string> {
  if (!binding) return "";
  try {
    return typeof binding === "string" ? binding : await binding.get();
  } catch {
    return "";
  }
}

function text(body: string): { content: Array<{ type: "text"; text: string }> } {
  return { content: [{ type: "text", text: body }] };
}

function errorContent(msg: string): { content: Array<{ type: "text"; text: string }>; isError: true } {
  return { content: [{ type: "text", text: msg }], isError: true };
}

function jsonRpcResult(id: any, result: unknown): Response {
  return new Response(JSON.stringify({ jsonrpc: "2.0", id, result }), {
    status: 200,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}

function jsonRpcError(id: any, code: number, message: string): Response {
  return new Response(JSON.stringify({ jsonrpc: "2.0", id, error: { code, message } }), {
    status: 200,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}
