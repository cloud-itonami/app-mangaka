"""mangaka_generate_character_3d — character image → TripoSR → GLB asset.

Takes a character reference image (base64) — typically a single clean view
from a `mangaka_generate_character` design sheet — uploads it to ComfyUI,
runs TripoSR (single-image → 3D mesh), and exports the result as a GLB
file. The GLB lives in ComfyUI's output dir and can be converted to USD /
USDZ downstream (Blender → USD plugin, Pixar USD tools, etc.) for the
mangaka.etzhayyim.com asset library.

Pregel: upload → build → submit → poll → END

Inputs:
    name                  str   — used in saved filename
    reference_image_b64   str   — base64 PNG/JPG of the character ref
    reference_image_mime  str?  — default image/png
    geometry_resolution   int?  — TripoSR grid resolution (default 256)
    threshold             float?— TripoSR isosurface threshold (default 0.0)
    triposr_ckpt          str?  — default triposr-model.ckpt

Output:
    same as comfy_run: { status, images (preview), raw_history,
      submitted prompt_id, etc. } plus saved_files (GLB filenames).
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from lg_mangaka import comfy_runner as _runner
from lg_mangaka import comfy_workflows as _wf


class _State(TypedDict, total=False):
    name: str
    reference_image_b64: str
    reference_image_mime: str
    geometry_resolution: int
    threshold: float
    triposr_ckpt: str
    comfy_url: str
    timeout_seconds: int
    poll_interval_ms: int

    # upload result
    uploaded_filename: str
    uploaded_subfolder: str
    workflow: dict
    prompt_id: str
    number: int
    submit_response: dict
    started_at_ms: int
    status: str
    images: list
    raw_history: dict
    saved_files: list           # filenames produced (e.g. mangaka-character-3d-yuki_0001.glb)
    elapsed_ms: int
    error: str | None


async def _upload(state: _State) -> dict[str, Any]:
    if not state.get("reference_image_b64"):
        return {"status": "error", "error": "reference_image_b64 required"}
    r = await _runner.upload_image_b64(
        state["reference_image_b64"],
        comfy_url=state.get("comfy_url") or _runner.DEFAULT_URL,
        filename_hint=f"mangaka-3d-{state.get('name') or 'character'}",
        image_mime=state.get("reference_image_mime") or "image/png",
    )
    if r.get("error"):
        return {"status": "error", "error": r["error"]}
    return {
        "uploaded_filename": r["filename"],
        "uploaded_subfolder": r.get("subfolder", ""),
    }


async def _build(state: _State) -> dict[str, Any]:
    if state.get("status") == "error":
        return {}
    wf = _wf.character_3d_workflow(
        name=state.get("name") or "character",
        input_image_filename=state["uploaded_filename"],
        geometry_resolution=int(state.get("geometry_resolution") or 256),
        threshold=float(state.get("threshold") or 0.0),
        triposr_ckpt=state.get("triposr_ckpt") or _wf.TRIPOSR_CKPT,
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
    res = await _runner.poll_outputs(
        state["prompt_id"],
        comfy_url=state.get("comfy_url") or _runner.DEFAULT_URL,
        started_at_ms=int(state.get("started_at_ms") or 0),
        timeout_seconds=int(state.get("timeout_seconds") or 600),
        poll_interval_ms=int(state.get("poll_interval_ms") or 2000),
    )
    # Surface GLB filenames separately. SaveGLB writes the file as part of
    # the history `outputs` block — depending on the ComfyUI version it
    # may not appear under 'images' but under '3d_models' / 'gltf' / etc.
    saved: list[str] = []
    history = res.get("raw_history") or {}
    for nid, node_out in (history.get("outputs") or {}).items():
        for key in ("3d", "models", "gltf", "glb", "files"):
            for entry in (node_out.get(key) or []):
                if isinstance(entry, dict) and entry.get("filename"):
                    saved.append(entry["filename"])
    res["saved_files"] = saved
    return res


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


GRAPH = _build_graph().compile(name="mangaka_generate_character_3d")
