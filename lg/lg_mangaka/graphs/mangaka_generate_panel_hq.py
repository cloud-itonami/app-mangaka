"""mangaka_generate_panel_hq — IPAdapter FaceID + ControlNet Union panel.

High-quality stable-character panel that drops the img2img reliance and
instead conditions a from-scratch generation on:

  - IPAdapter FaceID PlusV2 — face identity from the character reference
  - ControlNet Union (Canny) — line structure from the reference

The base latent is empty so the panel composition is driven by the prompt
+ controls, while the character's face + outfit stay tight against the
reference. Result is dramatically more stable than the denoise=0.5 img2img
panel_stable graph.

PREREQUISITES (run on the ComfyUI host once):

    .\\install-comfy-quality-pack.ps1 -Tier 1,2

  Installs:
    models/controlnet/controlnet-union-sdxl-promax.safetensors
    models/clip_vision/CLIP-ViT-bigG-14-laion2B-39B-b160k.bin
    models/ipadapter/ip-adapter-plus_sdxl_vit-h.safetensors
    models/ipadapter/ip-adapter-faceid-plusv2_sdxl.bin
    models/loras/ip-adapter-faceid-plusv2_sdxl_lora.safetensors
    models/insightface/models/antelopev2/*
    custom_nodes/ComfyUI_IPAdapter_plus

  Restart ComfyUI after install.
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

    seed: int
    width: int
    height: int
    steps: int
    cfg: float
    ckpt: str
    ipadapter_weight: float
    ipadapter_face_weight: float
    controlnet_strength: float

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
        filename_hint=f"mangaka-hq-{state.get('panel_rkey') or 'panel'}",
        image_mime=state.get("reference_image_mime") or "image/png",
    )
    if r.get("error"):
        return {"status": "error", "error": r["error"]}
    return {"uploaded_filename": r["filename"]}


async def _build(state: _State) -> dict[str, Any]:
    if state.get("status") == "error":
        return {}
    wf = _wf.panel_hq_workflow(
        panel_rkey=state.get("panel_rkey") or "panel",
        reference_image_filename=state["uploaded_filename"],
        framing=state.get("framing") or "medium",
        characters=list(state.get("characters") or []),
        environment=state.get("environment") or "",
        mood=state.get("mood") or "",
        action=state.get("action") or "",
        seed=int(state.get("seed") or 0),
        width=int(state.get("width") or 1024),
        height=int(state.get("height") or 1536),
        steps=int(state.get("steps") or 32),
        cfg=float(state.get("cfg") or 7.5),
        ckpt=state.get("ckpt") or _wf.DEFAULT_CKPT,
        ipadapter_weight=float(state.get("ipadapter_weight") or 0.75),
        ipadapter_face_weight=float(state.get("ipadapter_face_weight") or 0.85),
        controlnet_strength=float(state.get("controlnet_strength") or 0.55),
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


GRAPH = _build_graph().compile(name="mangaka_generate_panel_hq")
