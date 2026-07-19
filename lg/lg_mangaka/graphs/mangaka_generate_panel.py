"""mangaka_generate_panel — typed per-panel 2-pass ComfyUI workflow.

Per-panel generator that maps `com.etzhayyim.mangaka.panel` records (framing,
charactersAppearing, environment, mood) onto a 2-pass SDXL workflow
(composition → inked refine, denoise=0.4). Each invocation produces TWO
images, one for each pass, with filenames keyed by the panel rkey so
multi-panel runs don't collide.

Pregel:  build → submit → poll → END

This is the typed wrapper around `comfy_workflows.panel_workflow`. The
older `cine_generate_panel` graph still works for batch (page-level)
runs — this one is the single-panel direct path the artist drives from
Studio per page-element.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from lg_mangaka import comfy_runner as _runner
from lg_mangaka import comfy_workflows as _wf


class _State(TypedDict, total=False):
    # input — mirrors com.etzhayyim.mangaka.panel + supplemental scene context
    panel_rkey: str
    framing: str                  # wide|medium|closeup|low-angle|high-angle|ots
    characters: list              # e.g. ["lone hacker", "shadow figure"]
    environment: str              # description of background
    mood: str
    action: str

    # diffusion knobs
    seed: int
    base_steps: int
    refine_steps: int
    base_cfg: float
    refine_cfg: float
    refine_denoise: float
    width: int
    height: int
    ckpt: str

    # runner knobs
    comfy_url: str
    timeout_seconds: int
    poll_interval_ms: int

    # outputs
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
    wf = _wf.panel_workflow(
        panel_rkey=state.get("panel_rkey") or "panel",
        framing=state.get("framing") or "medium",
        characters=list(state.get("characters") or []),
        environment=state.get("environment") or "",
        mood=state.get("mood") or "",
        action=state.get("action") or "",
        base_steps=int(state.get("base_steps") or 30),
        refine_steps=int(state.get("refine_steps") or 20),
        base_cfg=float(state.get("base_cfg") or 8.0),
        refine_cfg=float(state.get("refine_cfg") or 7.0),
        refine_denoise=float(state.get("refine_denoise") or 0.45),
        seed=int(state.get("seed") or 0),
        width=int(state.get("width") or 1024),
        height=int(state.get("height") or 1536),
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


GRAPH = _build_graph().compile(name="mangaka_generate_panel")
