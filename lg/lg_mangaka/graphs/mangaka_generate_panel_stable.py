"""mangaka_generate_panel_stable — character-stable per-panel generator.

img2img from a character reference image so the panel keeps the same
character look across the page. Two-pass (composition denoise=0.65 →
inked refine denoise=0.4) — the reference latent carries character
silhouette + palette through both passes, while the new prompt drives
framing / action / scene.

Pregel: upload → build → submit → poll → END

Inputs:
    panel_rkey            str
    reference_image_b64   str   — character reference image (base64)
    reference_image_mime  str?
    framing               str   — wide|medium|closeup|low-angle|high-angle|ots
    characters            list[str]
    environment           str
    mood                  str
    action                str
    base_denoise          float — default 0.65 (higher = more change from ref)
    refine_denoise        float — default 0.4
    seed                  int
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from lg_mangaka import comfy_runner as _runner
from lg_mangaka import comfy_workflows as _wf


class _State(TypedDict, total=False):
    panel_rkey: str
    reference_image_b64: str
    reference_image_mime: str
    framing: str
    characters: list
    environment: str
    mood: str
    action: str

    base_denoise: float
    refine_denoise: float
    base_steps: int
    refine_steps: int
    base_cfg: float
    refine_cfg: float
    seed: int
    ckpt: str

    comfy_url: str
    timeout_seconds: int
    poll_interval_ms: int

    uploaded_filename: str
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


async def _upload(state: _State) -> dict[str, Any]:
    if not state.get("reference_image_b64"):
        return {"status": "error", "error": "reference_image_b64 required"}
    r = await _runner.upload_image_b64(
        state["reference_image_b64"],
        comfy_url=state.get("comfy_url") or _runner.DEFAULT_URL,
        filename_hint=f"mangaka-ref-{state.get('panel_rkey') or 'panel'}",
        image_mime=state.get("reference_image_mime") or "image/png",
    )
    if r.get("error"):
        return {"status": "error", "error": r["error"]}
    return {"uploaded_filename": r["filename"]}


async def _build(state: _State) -> dict[str, Any]:
    if state.get("status") == "error":
        return {}
    wf = _wf.panel_stable_workflow(
        panel_rkey=state.get("panel_rkey") or "panel",
        reference_image_filename=state["uploaded_filename"],
        framing=state.get("framing") or "medium",
        characters=list(state.get("characters") or []),
        environment=state.get("environment") or "",
        mood=state.get("mood") or "",
        action=state.get("action") or "",
        base_denoise=float(state.get("base_denoise") or 0.5),
        refine_denoise=float(state.get("refine_denoise") or 0.35),
        base_steps=int(state.get("base_steps") or 28),
        refine_steps=int(state.get("refine_steps") or 18),
        base_cfg=float(state.get("base_cfg") or 7.5),
        refine_cfg=float(state.get("refine_cfg") or 6.5),
        seed=int(state.get("seed") or 0),
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
    g.add_node("upload", _upload, retry_policy=RetryPolicy(max_attempts=2))
    g.add_node("build",  _build)
    g.add_node("submit", _submit, retry_policy=RetryPolicy(max_attempts=2))
    g.add_node("poll",   _poll)
    g.add_edge(START, "upload")
    g.add_edge("upload", "build")
    g.add_edge("build", "submit")
    g.add_edge("submit", "poll")
    g.add_edge("poll", END)
    return g


GRAPH = _build_graph().compile(name="mangaka_generate_panel_stable")
