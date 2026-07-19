"""mangaka_generate_character — typed character design-sheet generator.

Wraps a single-pass SDXL character workflow (built by comfy_workflows.
character_workflow) behind the same submit + poll loop the raw comfy_run
graph uses. Input is the same shape as the com.etzhayyim.mangaka.character
record (name, description, optional pose/style hints).

Pregel:  build → submit → poll → END
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from lg_mangaka import comfy_runner as _runner
from lg_mangaka import comfy_workflows as _wf


class _State(TypedDict, total=False):
    # input — mirrors com.etzhayyim.mangaka.character
    name: str
    description: str
    style: str
    pose_hint: str
    seed: int
    steps: int
    cfg: float
    width: int
    height: int
    batch: int
    ckpt: str
    comfy_url: str
    timeout_seconds: int
    poll_interval_ms: int

    # workflow output (also returned to caller so they can re-run the same JSON)
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
    wf = _wf.character_workflow(
        name=state["name"], description=state["description"],
        style=state.get("style") or "anime, manga, ink, screentone",
        pose_hint=state.get("pose_hint") or "character reference sheet, multiple views",
        seed=int(state.get("seed") or 0),
        steps=int(state.get("steps") or 32),
        cfg=float(state.get("cfg") or 8.0),
        width=int(state.get("width") or 1024),
        height=int(state.get("height") or 1536),
        batch=int(state.get("batch") or 2),
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


GRAPH = _build_graph().compile(name="mangaka_generate_character")
