"""mangaka_generate_panel_flux_pulid — Flux + PuLID identity-preserving panel.

Combines:
  - Flux.1 [dev] Q4_K_S GGUF (text-to-image quality jump vs SDXL)
  - PuLID Flux v0.9.1 (face identity injection from reference image)

This is the "best of both" path — Flux's clean line work + reasoning
plus PuLID's identity preservation. Replaces panel_hq (SDXL + IPAdapter)
for production work once the dependencies (lldacing/ComfyUI_PuLID_Flux_ll
custom node + facenet-pytorch pip + pulid_flux_v0.9.1.safetensors
weights) are installed.

Pregel: upload -> build -> submit -> poll -> END
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

    width: int
    height: int
    steps: int
    guidance: float
    pulid_weight: float
    pulid_start_at: float
    pulid_end_at: float
    seed: int
    style_prompt: str

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
        filename_hint=f"mangaka-flux-pulid-{state.get('panel_rkey') or 'panel'}",
        image_mime=state.get("reference_image_mime") or "image/png",
    )
    if r.get("error"):
        return {"status": "error", "error": r["error"]}
    return {"uploaded_filename": r["filename"]}


async def _build(state: _State) -> dict[str, Any]:
    if state.get("status") == "error":
        return {}
    wf = _wf.panel_flux_pulid_workflow(
        panel_rkey=state.get("panel_rkey") or "panel",
        reference_image_filename=state["uploaded_filename"],
        framing=state.get("framing") or "medium",
        characters=list(state.get("characters") or []),
        environment=state.get("environment") or "",
        mood=state.get("mood") or "",
        action=state.get("action") or "",
        width=int(state.get("width") or 1024),
        height=int(state.get("height") or 1536),
        steps=int(state.get("steps") or 22),
        guidance=float(state.get("guidance") or 3.5),
        pulid_weight=float(state.get("pulid_weight") or 0.7),
        pulid_start_at=float(state.get("pulid_start_at") or 0.0),
        pulid_end_at=float(state.get("pulid_end_at") or 1.0),
        seed=int(state.get("seed") or 0),
        style_prompt=state.get("style_prompt") or (
            "manga inked panel, sharp black ink lines, screentone, hatching, "
            "high contrast monochrome, masterpiece, best quality, "
            "shonen-jump style"
        ),
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
        timeout_seconds=int(state.get("timeout_seconds") or 600),
        poll_interval_ms=int(state.get("poll_interval_ms") or 3000),
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


GRAPH = _build_graph().compile(name="mangaka_generate_panel_flux_pulid")
