"""mangaka_generate_panel_flux — Flux.1 [dev] GGUF Q4 panel generator.

Single-pass text-to-image panel built on Flux.1 [dev] via city96's GGUF
quantized weights (Q4_K_S, ~6.5 GB VRAM). Quality jump vs SDXL is dramatic
(verified 2026-05-21: shrine-maiden / stone-steps / misty cedar forest
renders as clean monochrome manga ink in ~180s on AMD Radeon 8060S
ROCm 7.2). T5 text encoder lets the prompt be long natural-language
sentences, not SDXL tag soup.

Pregel: build -> submit -> poll -> END  (no upload step; text-only)

Inputs (mirror mangaka_generate_panel_hq for compatibility):
    panel_rkey         str
    framing            str   wide|medium|closeup|low-angle|high-angle|ots
    characters         list[str]
    environment        str
    mood               str
    action             str
    width              int   default 1024
    height             int   default 1536
    steps              int   default 22  (Flux converges fast)
    guidance           float default 3.5 (Flux-native; replaces CFG)
    seed               int
    style_prompt       str   override the default manga-ink anchor

Notes:
  - Flux is FP16/BF16 at heart. The GGUF Q4_K_S quantization works fine
    on ROCm but trades ~5% quality for 50% VRAM. Q8_0 (~12 GB) is the
    next step up; the host has the RAM budget.
  - For character identity preservation use mangaka_generate_panel_hq
    (SDXL + IPAdapter / PuLID) for now; the Flux IPAdapter / PuLID-Flux
    integration is a follow-on (PuLID Flux v0.9.1 weights, ~6 GB).
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from lg_mangaka import comfy_runner as _runner
from lg_mangaka import comfy_workflows as _wf


class _State(TypedDict, total=False):
    panel_rkey: str
    framing: str
    characters: list
    environment: str
    mood: str
    action: str

    width: int
    height: int
    steps: int
    guidance: float
    seed: int
    style_prompt: str
    unet: str
    t5: str
    clip_l: str
    vae: str
    sampler: str
    scheduler: str

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
    wf = _wf.panel_flux_workflow(
        panel_rkey=state.get("panel_rkey") or "panel",
        framing=state.get("framing") or "medium",
        characters=list(state.get("characters") or []),
        environment=state.get("environment") or "",
        mood=state.get("mood") or "",
        action=state.get("action") or "",
        width=int(state.get("width") or 1024),
        height=int(state.get("height") or 1536),
        steps=int(state.get("steps") or 22),
        guidance=float(state.get("guidance") or 3.5),
        seed=int(state.get("seed") or 0),
        unet=state.get("unet") or _wf.FLUX_UNET,
        t5=state.get("t5") or _wf.FLUX_T5,
        clip_l=state.get("clip_l") or _wf.FLUX_CLIP_L,
        vae=state.get("vae") or _wf.FLUX_VAE,
        sampler=state.get("sampler") or _wf.FLUX_SAMPLER,
        scheduler=state.get("scheduler") or _wf.FLUX_SCHEDULER,
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
        # Flux at Q4 on AMD ROCm needs ~3 min for 1024x1536, set a generous deadline.
        timeout_seconds=int(state.get("timeout_seconds") or 600),
        poll_interval_ms=int(state.get("poll_interval_ms") or 3000),
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


GRAPH = _build_graph().compile(name="mangaka_generate_panel_flux")
