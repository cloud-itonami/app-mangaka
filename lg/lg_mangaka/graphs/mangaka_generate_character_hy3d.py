"""mangaka_generate_character_hy3d — Hunyuan3D-2 textured-mesh generator.

Higher-quality replacement for mangaka_generate_character_3d (TripoSR).
Uses the Hy3D ComfyUI node family (already installed) — first run pulls
~6 GB of weights from tencent/Hunyuan3D-2 via the
DownloadAndLoadHy3DDelightModel node.

Output is a textured GLB; rename to .usdz at the OS layer (or use the
ComfyUI SaveGLB-with-usdz-extension trick) for USD pipelines.

Pregel: upload → build → submit → poll → END
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
    bake_size: int
    mesh_simplify: int
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
    saved_files: list
    elapsed_ms: int
    error: str | None


async def _upload(state: _State) -> dict[str, Any]:
    if not state.get("reference_image_b64"):
        return {"status": "error", "error": "reference_image_b64 required"}
    r = await _runner.upload_image_b64(
        state["reference_image_b64"],
        comfy_url=state.get("comfy_url") or _runner.DEFAULT_URL,
        filename_hint=f"mangaka-hy3d-{state.get('name') or 'character'}",
        image_mime=state.get("reference_image_mime") or "image/png",
    )
    if r.get("error"):
        return {"status": "error", "error": r["error"]}
    return {"uploaded_filename": r["filename"]}


async def _build(state: _State) -> dict[str, Any]:
    if state.get("status") == "error":
        return {}
    wf = _wf.character_3d_hy3d_workflow(
        name=state.get("name") or "character",
        input_image_filename=state["uploaded_filename"],
        bake_size=int(state.get("bake_size") or 1024),
        mesh_simplify=int(state.get("mesh_simplify") or 50000),
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
        timeout_seconds=int(state.get("timeout_seconds") or 900),  # mesh gen is slow
        poll_interval_ms=int(state.get("poll_interval_ms") or 3000),
    )
    saved: list[str] = []
    history = res.get("raw_history") or {}
    for nid, node_out in (history.get("outputs") or {}).items():
        for key in ("3d", "models", "gltf", "glb", "files", "meshes"):
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


GRAPH = _build_graph().compile(name="mangaka_generate_character_hy3d")
