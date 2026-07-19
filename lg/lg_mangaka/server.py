"""OSS FastAPI server for lg-mangaka (mirrors lg-shinshi pattern).

Provides the same minimal HTTP surface:
  POST /runs              → invoke a graph synchronously
  POST /runs/stream       → stream graph events as SSE
  POST /xrpc/{nsid}       → XRPC-compat shim (NSID → assistant_id mapping)
  GET  /threads/{tid}/state → fetch latest checkpoint
  GET  /ok / /health      → liveness / readiness

Auth: optional `LG_API_KEY` env enforces `x-api-key` on /runs paths.
The /xrpc/{nsid} surface is unauthenticated (trust at the cloudflared
tunnel layer, same pattern as lg-shinshi).

Mangaka NSID namespace covers ~13 task types; the initial scaffold
ports `health`, `listWorks`, `chat`. Other NSIDs return 404 until
their corresponding graph is ported (P3+).
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse

from lg_mangaka.checkpointer import build_checkpointer
from lg_mangaka.cron import start_cron, stop_cron
from lg_mangaka.graphs.agent_chat import GRAPH as AGENT_CHAT
from lg_mangaka.graphs.analyze_character_graph import GRAPH as ANALYZE_CHARACTER_GRAPH
from lg_mangaka.graphs.backfill_mangaka_edges import GRAPH as BACKFILL_MANGAKA_EDGES
from lg_mangaka.graphs.compose_scene_3d import GRAPH as COMPOSE_SCENE_3D
from lg_mangaka.graphs.debug_canvas_state import GRAPH as DEBUG_CANVAS_STATE
from lg_mangaka.graphs.derive_chapter_incidents import GRAPH as DERIVE_CHAPTER_INCIDENTS
from lg_mangaka.graphs.detect_faces import GRAPH as DETECT_FACES
from lg_mangaka.graphs.score_emotion import GRAPH as SCORE_EMOTION
from lg_mangaka.graphs.enrich_characters import GRAPH as ENRICH_CHARACTERS
from lg_mangaka.graphs.enrich_environments import GRAPH as ENRICH_ENVIRONMENTS
from lg_mangaka.graphs.enrich_organizations import GRAPH as ENRICH_ORGANIZATIONS
from lg_mangaka.graphs.health import GRAPH as HEALTH
from lg_mangaka.graphs.import_chat_history import GRAPH as IMPORT_CHAT_HISTORY
from lg_mangaka.graphs.import_ghosthacker import GRAPH as IMPORT_GHOSTHACKER
from lg_mangaka.graphs.list_documents import GRAPH as LIST_DOCUMENTS
from lg_mangaka.graphs.load_document import GRAPH as LOAD_DOCUMENT
from lg_mangaka.graphs.record_op_log import GRAPH as RECORD_OP_LOG
from lg_mangaka.graphs.save_document import GRAPH as SAVE_DOCUMENT

_log = logging.getLogger(__name__)

GRAPHS: dict[str, Any] = {
    "health":                    HEALTH,
    "agent_chat":                AGENT_CHAT,
    "save_document":             SAVE_DOCUMENT,
    "load_document":             LOAD_DOCUMENT,
    "list_documents":            LIST_DOCUMENTS,
    "import_ghosthacker":        IMPORT_GHOSTHACKER,
    "analyze_character_graph":   ANALYZE_CHARACTER_GRAPH,
    "enrich_characters":         ENRICH_CHARACTERS,
    "enrich_organizations":      ENRICH_ORGANIZATIONS,
    "enrich_environments":       ENRICH_ENVIRONMENTS,
    "derive_chapter_incidents":  DERIVE_CHAPTER_INCIDENTS,
    "import_chat_history":       IMPORT_CHAT_HISTORY,
    "backfill_mangaka_edges":    BACKFILL_MANGAKA_EDGES,
    "record_op_log":             RECORD_OP_LOG,
    "compose_scene_3d":          COMPOSE_SCENE_3D,
    "debug_canvas_state":        DEBUG_CANVAS_STATE,
    "detect_faces":              DETECT_FACES,
    "score_emotion":             SCORE_EMOTION,
}

_API_KEY = os.environ.get("LG_API_KEY", "").strip()


async def _b2_blob_fetcher(key: str) -> bytes | None:
    """Async wrapper for `lg_mangaka.blob.get` (sync stdlib urllib).

    Passed to `kotodama.langgraph_loader.load_active_graphs` so the
    `kind='llm_vision'` resolver can materialise B2 blob keys (e.g.
    `blobs/anonymous/{sha256hex}`) into PNG bytes at call time.
    `kotodama` itself stays storage-agnostic — this closure is the only
    place mangaka's B2 layout leaks into the resolver.
    """
    import asyncio

    from lg_mangaka import blob as _blob

    if not key or not _blob.is_configured():
        return None
    return await asyncio.to_thread(_blob.get, key)


async def _maybe_load_topology_graphs(
    cp: Any, bound: dict[str, Any]
) -> None:
    """Activate Phase C (vertex_langgraph_deployment topology rows) when
    `MANGAKA_USE_TOPOLOGY=1`. The default stays on the Phase A in-image
    GRAPHS so the pod boots cleanly without RW connectivity — the
    topology row is still seeded by migration r_20260514170000 either way
    (audit / lineage / governance use it regardless of the runtime flag).
    """
    if os.environ.get("MANGAKA_USE_TOPOLOGY", "0") != "1":
        return
    try:
        from kotodama.langgraph_loader import load_active_graphs
    except Exception as exc:  # pragma: no cover — kotodama may not be installed in dev envs
        _log.warning("topology loader unavailable: %s", exc)
        return

    def _register(name: str, graph: Any) -> None:
        try:
            bound[name] = graph.with_config({"checkpointer": cp})
        except Exception:
            bound[name] = graph

    try:
        result = await load_active_graphs(
            register=_register,
            already_registered=lambda nsid: nsid in bound,
            blob_fetcher=_b2_blob_fetcher,
        )
        _log.info("load_active_graphs (topology): %s", result)
    except Exception as exc:  # noqa: BLE001
        _log.warning("load_active_graphs failed: %s", exc)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    cp_ctx = build_checkpointer()
    cp = await cp_ctx.__aenter__()
    app.state.checkpointer = cp
    app.state.cp_ctx = cp_ctx

    bound: dict[str, Any] = {}
    for name, g in GRAPHS.items():
        try:
            bound[name] = g.with_config({"checkpointer": cp})
        except Exception:  # noqa: BLE001
            try:
                g.checkpointer = cp
            except Exception:
                pass
            bound[name] = g

    # P10 — opt-in Phase C topology load. See _maybe_load_topology_graphs.
    await _maybe_load_topology_graphs(cp, bound)

    app.state.graphs = bound

    scheduler = start_cron(bound)
    app.state.scheduler = scheduler

    _log.info("lg-mangaka server up: graphs=%s crons=%s",
              list(bound), bool(scheduler))
    try:
        yield
    finally:
        await stop_cron(scheduler)
        await cp_ctx.__aexit__(None, None, None)


app = FastAPI(title="lg-mangaka OSS server", version="0.1.0", lifespan=_lifespan)


def _require_api_key(x_api_key: str | None = Header(default=None, alias="x-api-key")) -> None:
    if _API_KEY and x_api_key != _API_KEY:
        raise HTTPException(status_code=401, detail="invalid x-api-key")


@app.get("/ok")
async def ok() -> dict[str, Any]:
    return {"ok": True, "graphs": list(GRAPHS.keys()), "version": "0.1.0"}


@app.get("/health")
async def health() -> dict[str, Any]:
    cp_ok = hasattr(app.state, "checkpointer") and app.state.checkpointer is not None
    # P11 — surface the kami_mangaka_scene wheel status so ops can tell
    # whether the pod is rendering real PNGs (`available: true`) or
    # falling back to placeholder blob keys (`available: false`).
    from lg_mangaka.tools import kami_wheel_status

    return {
        "ok": cp_ok,
        "checkpointer": cp_ok,
        "kamiMangakaScene": kami_wheel_status(),
    }


@app.post("/runs", dependencies=[Depends(_require_api_key)])
async def create_run(body: dict[str, Any]) -> dict[str, Any]:
    assistant_id = str(body.get("assistant_id") or "")
    if assistant_id not in app.state.graphs:
        raise HTTPException(status_code=404, detail=f"unknown graph: {assistant_id}")
    graph = app.state.graphs[assistant_id]
    input_data = body.get("input") or {}
    config = body.get("config") or {}

    started = time.monotonic()
    try:
        result = await graph.ainvoke(input_data, config=config)
        return {
            "ok": True, "result": result, "assistantId": assistant_id,
            "latencyMs": int((time.monotonic() - started) * 1000),
        }
    except Exception as exc:  # noqa: BLE001
        _log.exception("graph %s failed", assistant_id)
        return {
            "ok": False, "error": str(exc)[:500],
            "errorType": type(exc).__name__, "assistantId": assistant_id,
            "latencyMs": int((time.monotonic() - started) * 1000),
        }


@app.post("/runs/stream", dependencies=[Depends(_require_api_key)])
async def stream_run(body: dict[str, Any]) -> StreamingResponse:
    assistant_id = str(body.get("assistant_id") or "")
    if assistant_id not in app.state.graphs:
        raise HTTPException(status_code=404, detail=f"unknown graph: {assistant_id}")
    graph = app.state.graphs[assistant_id]
    input_data = body.get("input") or {}
    config = body.get("config") or {}
    stream_mode = body.get("stream_mode") or "values"

    async def _gen() -> AsyncIterator[bytes]:
        try:
            async for chunk in graph.astream(input_data, config=config, stream_mode=stream_mode):
                yield f"data: {json.dumps({'event':'values','data':_safe_json(chunk)})}\n\n".encode()
        except Exception as exc:  # noqa: BLE001
            yield f"data: {json.dumps({'event':'error','data':str(exc)[:500]})}\n\n".encode()

    return StreamingResponse(_gen(), media_type="text/event-stream")


# ── XRPC-compat surface (NSID → assistant_id) ──────────────────────────

_NSID_TO_ASSISTANT: dict[str, str] = {
    "com.etzhayyim.mangaka.health":             "health",
    "com.etzhayyim.mangaka.chat":               "agent_chat",
    "com.etzhayyim.mangaka.pipelineChat":       "agent_chat",
    "com.etzhayyim.mangaka.projectChat":        "agent_chat",
    "com.etzhayyim.mangaka.saveDocument":       "save_document",
    "com.etzhayyim.mangaka.loadDocument":       "load_document",
    "com.etzhayyim.mangaka.listDocuments":      "list_documents",
    "com.etzhayyim.mangaka.importGhosthacker":       "import_ghosthacker",
    "com.etzhayyim.mangaka.analyzeCharacterGraph":   "analyze_character_graph",
    "com.etzhayyim.mangaka.enrichCharacters":        "enrich_characters",
    "com.etzhayyim.mangaka.enrichOrganizations":     "enrich_organizations",
    "com.etzhayyim.mangaka.enrichEnvironments":      "enrich_environments",
    "com.etzhayyim.mangaka.deriveChapterIncidents":  "derive_chapter_incidents",
    "com.etzhayyim.mangaka.importChatHistory":       "import_chat_history",
    "com.etzhayyim.mangaka.backfillMangakaEdges":    "backfill_mangaka_edges",
    "com.etzhayyim.mangaka.recordOpLog":             "record_op_log",
    "com.etzhayyim.mangaka.debugCanvasState":        "debug_canvas_state",
    "com.etzhayyim.mangaka.detectFaces":             "detect_faces",
    "com.etzhayyim.mangaka.scoreEmotion":            "score_emotion",
}


def _camel_to_snake(s: str) -> str:
    out: list[str] = []
    for i, ch in enumerate(s):
        if ch.isupper() and i > 0:
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def _snake_to_camel(s: str) -> str:
    """`scene_dag` → `sceneDag`. Used on tool output keys so the lexicon-
    declared wire shape (camelCase) is preserved across the snake_case
    Python boundary in `lg_mangaka.tools`."""
    parts = s.split("_")
    if not parts:
        return s
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:] if p)


def _xrpc_input_to_graph_input(_nsid: str, body: dict[str, Any]) -> dict[str, Any]:
    return {_camel_to_snake(k): v for k, v in (body or {}).items()}


# ── MCP tool dispatch (P9 of ADR-2605141200) ──────────────────────────
#
# `com.etzhayyim.mangaka.tools.<m>` NSIDs route directly to the pure
# functions in `lg_mangaka.tools` — these are not LangGraph graphs, they
# are the 6 MCP-resolvable bodies referenced by the topology spec. The
# MCP adapter at atproto.etzhayyim.com forwards `tools/call` envelopes here
# once `vertex_mcp_tool_def` (P8 seed) routes the NSID's `actor_host` to
# mangaka.etzhayyim.com. Worker side stays a thin pass-through — RW + B2 + GPU
# all live on the pod per ADR-2605111200.
#
# camelCase JSON body → snake_case kwargs adapter preserves the lexicon
# field naming (camelCase, AT Protocol standard) while matching the
# Python functions' kwarg-only signatures.
from lg_mangaka import tools as _tools  # noqa: E402

_TOOL_NSID_TO_HANDLER: dict[str, Any] = {
    "com.etzhayyim.mangaka.tools.loadPanelPlan":      _tools.tool_load_panel_plan,
    "com.etzhayyim.mangaka.tools.resolveAssets":      _tools.tool_resolve_assets,
    "com.etzhayyim.mangaka.tools.placeScene":         _tools.tool_place_scene,
    "com.etzhayyim.mangaka.tools.simulateCharacter":  _tools.tool_simulate_character,
    "com.etzhayyim.mangaka.tools.renderKeyframes":    _tools.tool_render_keyframes,
    "com.etzhayyim.mangaka.tools.persistScene3d":     _tools.tool_persist_scene_3d,
    # P10.2 — validator between cinematography (kind='llm', text) and
    # simulate_one fan-out. Sync, pure CPU.
    "com.etzhayyim.mangaka.tools.validateCameraPlan": _tools.tool_validate_camera_plan,
    # P10.2b — critique-side aggregator. Sync (B2 fetch + Hume scoring).
    "com.etzhayyim.mangaka.tools.aggregateCritique":  _tools.tool_aggregate_critique,
    # P13 — VRM ingestion: B2 upload + character vertex props patch.
    "com.etzhayyim.mangaka.tools.attachCharacterVrm": _tools.tool_attach_character_vrm,
}


async def _dispatch_mcp_tool(nsid: str, body: dict[str, Any]) -> dict[str, Any]:
    """Translate camelCase body → snake_case kwargs and invoke the tool.
    Sync tools (e.g. tool_place_scene) are wrapped to keep the caller
    uniformly async."""
    import asyncio
    import inspect

    handler = _TOOL_NSID_TO_HANDLER[nsid]
    kwargs = {_camel_to_snake(k): v for k, v in (body or {}).items()}

    started = time.monotonic()
    try:
        if inspect.iscoroutinefunction(handler):
            result = await handler(**kwargs)
        else:
            result = await asyncio.to_thread(handler, **kwargs)
    except TypeError as exc:
        # Unknown / extra kwargs surface here. Return 400-style payload so the
        # MCP adapter can pass it back to tools/call without 500ing.
        return {
            "error": "MCP tool input mismatch",
            "errorDetail": str(exc)[:300],
            "tool": nsid,
            "latencyMs": int((time.monotonic() - started) * 1000),
        }
    except Exception as exc:  # noqa: BLE001
        _log.exception("mcp tool %s raised", nsid)
        return {
            "error": f"mcp tool {type(exc).__name__}",
            "errorDetail": str(exc)[:300],
            "tool": nsid,
            "latencyMs": int((time.monotonic() - started) * 1000),
        }

    if not isinstance(result, dict):
        result = {"result": _safe_json(result)}
    # Convert snake_case keys → camelCase to match the lexicon-declared wire
    # shape (input/output schemas in `vertex_mcp_tool_def` use camelCase).
    # Only top-level keys are converted; nested payloads (panelPlan,
    # sceneDag, ...) are pass-through because they were authored as
    # JSON-LD with their own canonical key shapes (snake_case `scene_dag`
    # uses underscores inside the JSON-LD body — that's data, not a wire
    # field, and is preserved verbatim).
    out: dict[str, Any] = {_snake_to_camel(k): v for k, v in result.items()}
    out["tool"] = nsid
    out["latencyMs"] = int((time.monotonic() - started) * 1000)
    return out


# ── MCP envelope handler (com.etzhayyim.mcp.message, ADR-2605141200 P9 blocker #4)
#
# `kotodama.langgraph_node_resolvers.make_mcp_tool_node` POSTs a JSON-RPC
# 2.0 `tools/call` envelope at `/xrpc/com.etzhayyim.mcp.message`. Inside the
# Phase C topology this resolves to the pod itself when
# `MCP_NSID_OVERRIDE_ai_etzhayyim_apps_mangaka_tools=http://localhost:8000` is
# set (see `50-infra/vultr/lg-mangaka-pool/values.yaml`). External MCP
# clients reach the atproto.etzhayyim.com MCP adapter and end up here through
# the same envelope, so both paths converge.
#
# Envelope shape (JSON-RPC 2.0):
#   { "method": "tools/call",
#     "params": { "name": "<nsid>", "arguments": { ... } } }
#
# Returns the underlying tool's dict verbatim (camelCase keys + tool +
# latencyMs), matching the direct `/xrpc/{nsid}` shape so callers can
# treat them interchangeably.


@app.post("/xrpc/com.etzhayyim.mcp.message")
async def xrpc_mcp_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    method = envelope.get("method")
    if method != "tools/call":
        raise HTTPException(
            status_code=400,
            detail=f"unsupported MCP method {method!r}; only tools/call is implemented",
        )
    params = envelope.get("params") or {}
    nsid = params.get("name")
    if not nsid or not isinstance(nsid, str):
        raise HTTPException(
            status_code=400,
            detail="MCP tools/call: params.name (NSID) is required",
        )
    if nsid not in _TOOL_NSID_TO_HANDLER:
        raise HTTPException(
            status_code=404,
            detail=f"MCP tools/call: unknown tool NSID {nsid!r}",
        )
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise HTTPException(
            status_code=400,
            detail="MCP tools/call: params.arguments must be an object",
        )
    return await _dispatch_mcp_tool(nsid, arguments)


@app.post("/xrpc/{nsid}")
async def xrpc_compat(nsid: str, body: dict[str, Any]) -> dict[str, Any]:
    # 1. MCP tool path — direct dispatch to lg_mangaka.tools.*.
    if nsid in _TOOL_NSID_TO_HANDLER:
        return await _dispatch_mcp_tool(nsid, body or {})

    # 2. Pregel graph path (existing).
    assistant_id = _NSID_TO_ASSISTANT.get(nsid)
    if not assistant_id:
        raise HTTPException(status_code=404, detail=f"unknown NSID: {nsid}")
    if assistant_id not in app.state.graphs:
        raise HTTPException(status_code=503, detail=f"graph not loaded: {assistant_id}")

    graph = app.state.graphs[assistant_id]
    graph_input = _xrpc_input_to_graph_input(nsid, body)
    bucket = int(time.time()) // 1800
    thread_id = f"xrpc:{nsid.split('.')[-1]}:{bucket}"
    config = {"configurable": {"thread_id": thread_id}}

    started = time.monotonic()
    try:
        result = await graph.ainvoke(graph_input, config=config)
    except Exception as exc:  # noqa: BLE001
        _log.exception("xrpc graph %s failed", assistant_id)
        return {
            "error": f"lg-mangaka {type(exc).__name__}",
            "errorDetail": str(exc)[:300],
            "assistantId": assistant_id,
            "latencyMs": int((time.monotonic() - started) * 1000),
        }

    if isinstance(result, dict):
        out = dict(result)
    else:
        out = {"result": _safe_json(result)}
    out["latencyMs"] = int((time.monotonic() - started) * 1000)
    out["assistantId"] = assistant_id
    return out


@app.get("/threads/{thread_id}/state", dependencies=[Depends(_require_api_key)])
async def get_thread_state(thread_id: str, assistant_id: str) -> dict[str, Any]:
    if assistant_id not in app.state.graphs:
        raise HTTPException(status_code=404, detail=f"unknown graph: {assistant_id}")
    graph = app.state.graphs[assistant_id]
    snap = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    return {
        "values": _safe_json(snap.values),
        "next": list(snap.next),
        "tasks": [
            {"id": t.id, "name": t.name, "error": str(t.error) if t.error else None}
            for t in (snap.tasks or [])
        ],
    }


# ── helpers ────────────────────────────────────────────────────────────


def _safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _safe_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}B>"
    return repr(value)[:200]
