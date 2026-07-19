"""mangaka_render_episode_page — render a single page of the
ghost-hacker arc 0-1 manga via the universal page renderer.

Wraps scripts/render-arc01-page.py's workflow builder behind a typed
LangGraph node so Studio can trigger per-page renders from the UI.

Pregel: plan -> build -> submit -> poll -> END

Inputs:
    page_num         int    0..45
    comfy_url        str    optional override
    timeout_seconds  int    default 1800 (30 min ceiling)

Output:
    status         "ok" | "error" | "timeout"
    prompt_id      str
    page_filename  str   ComfyUI saved filename
    image_b64      str   base64 PNG of the final composite
    elapsed_ms     int
    n_panels       int   number of panels on this page
    n_nodes        int   workflow size
    error          str | None
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from lg_mangaka import comfy_runner as _runner


# Load the universal renderer module dynamically so we can reuse its
# build_workflow function without restructuring the script.
_renderer_path = (
    Path(__file__).parent.parent.parent  # lg/
    / "scripts" / "render-arc01-page.py"
)


def _load_renderer():
    spec = importlib.util.spec_from_file_location(
        "_arc01_renderer", _renderer_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load renderer at {_renderer_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_arc01_renderer"] = mod
    spec.loader.exec_module(mod)
    return mod


class _State(TypedDict, total=False):
    page_num: int
    comfy_url: str
    timeout_seconds: int
    poll_interval_ms: int

    workflow: dict
    n_panels: int
    n_nodes: int

    prompt_id: str
    started_at_ms: int
    status: str
    images: list
    elapsed_ms: int
    error: str | None
    page_filename: str
    image_b64: str


async def _plan(state: _State) -> dict[str, Any]:
    pn = state.get("page_num")
    if pn is None or not (0 <= int(pn) <= 45):
        return {"status": "error",
                "error": f"page_num must be 0..45 (got {pn!r})"}
    return {"page_num": int(pn)}


async def _build(state: _State) -> dict[str, Any]:
    if state.get("status") == "error":
        return {}
    renderer = _load_renderer()
    wf, n_panels = renderer.build_workflow(state["page_num"])
    return {"workflow": wf, "n_panels": n_panels, "n_nodes": len(wf)}


async def _submit(state: _State) -> dict[str, Any]:
    if state.get("status") == "error":
        return {}
    comfy_url = state.get("comfy_url") or _runner.DEFAULT_URL
    return await _runner.submit_workflow(state["workflow"], comfy_url=comfy_url)


async def _poll(state: _State) -> dict[str, Any]:
    if state.get("status") == "error" or not state.get("prompt_id"):
        return {}
    comfy_url = state.get("comfy_url") or _runner.DEFAULT_URL
    r = await _runner.poll_outputs(
        state["prompt_id"],
        comfy_url=comfy_url,
        started_at_ms=int(state.get("started_at_ms") or 0),
        timeout_seconds=int(state.get("timeout_seconds") or 1800),
        poll_interval_ms=int(state.get("poll_interval_ms") or 5000),
    )
    # Last (highest-numbered) image = final composite.
    images = r.get("images") or []
    if images:
        last = max(images, key=lambda im: int(im.get("node", 0)))
        r["page_filename"] = last["filename"]
        r["image_b64"] = last["imageInlineB64"]
    return r


def _build_graph() -> StateGraph:
    g: StateGraph = StateGraph(_State)
    g.add_node("plan",   _plan, retry_policy=RetryPolicy(max_attempts=2))
    g.add_node("build",  _build)
    g.add_node("submit", _submit, retry_policy=RetryPolicy(max_attempts=2))
    g.add_node("poll",   _poll)
    g.add_edge(START, "plan")
    g.add_edge("plan", "build")
    g.add_edge("build", "submit")
    g.add_edge("submit", "poll")
    g.add_edge("poll", END)
    return g


GRAPH = _build_graph().compile(name="mangaka_render_episode_page")
