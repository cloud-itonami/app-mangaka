"""mangaka_generate_scene — typed environment / scene generator.

Wraps comfy_workflows.scene_workflow (landscape 1216x832 establishing shot,
no characters by default) behind submit + poll. Input mirrors
com.etzhayyim.mangaka.environment.

Pregel:  build → submit → poll → END
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from lg_mangaka import comfy_runner as _runner
from lg_mangaka import comfy_workflows as _wf


class _State(TypedDict, total=False):
    # input — mirrors com.etzhayyim.mangaka.environment
    name: str
    description: str
    style: str
    time_of_day: str
    weather: str
    seed: int
    steps: int
    cfg: float
    width: int
    height: int
    ckpt: str
    comfy_url: str
    timeout_seconds: int
    poll_interval_ms: int

    workflow: dict
    prompt_id: str
    number: int
    submit_response: dict
    started_at_ms: int
    status: str
    images: list
    raw_history: dict
    elapsed_ms: int
    error: str | None


async def _build(state: _State) -> dict[str, Any]:
    if not state.get("name") or not state.get("description"):
        return {"status": "error", "error": "name + description required"}
    wf = _wf.scene_workflow(
        name=state["name"], description=state["description"],
        style=state.get("style") or "anime, manga, ink, screentone, detailed",
        time_of_day=state.get("time_of_day") or "",
        weather=state.get("weather") or "",
        seed=int(state.get("seed") or 0),
        steps=int(state.get("steps") or 32),
        cfg=float(state.get("cfg") or 8.0),
        width=int(state.get("width") or 1536),
        height=int(state.get("height") or 1024),
        ckpt=state.get("ckpt") or _wf.DEFAULT_CKPT,
    )
    return {"workflow": wf}


async def _submit(state: _State) -> dict[str, Any]:
    if state.get("status") == "error":
        return {}
    return await _runner.submit_workflow(
        state["workflow"],
        comfy_url=state.get("comfy_url") or _runner.DEFAULT_URL,
    )


async def _poll(state: _State) -> dict[str, Any]:
    if state.get("status") == "error" or not state.get("prompt_id"):
        return {}
    return await _runner.poll_outputs(
        state["prompt_id"],
        comfy_url=state.get("comfy_url") or _runner.DEFAULT_URL,
        started_at_ms=int(state.get("started_at_ms") or 0),
        timeout_seconds=int(state.get("timeout_seconds") or 300),
        poll_interval_ms=int(state.get("poll_interval_ms") or 1500),
    )


def _build_graph() -> StateGraph:
    g: StateGraph = StateGraph(_State)
    g.add_node("build",  _build)
    g.add_node("submit", _submit, retry_policy=RetryPolicy(max_attempts=2))
    g.add_node("poll",   _poll)
    g.add_edge(START, "build")
    g.add_edge("build", "submit")
    g.add_edge("submit", "poll")
    g.add_edge("poll", END)
    return g


GRAPH = _build_graph().compile(name="mangaka_generate_scene")
