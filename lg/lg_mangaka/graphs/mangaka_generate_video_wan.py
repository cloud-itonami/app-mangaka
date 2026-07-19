"""mangaka_generate_video_wan — Wan 2.2 TI2V 5B image+text → video.

Wraps the Wan 2.2 5B TI2V model (already installed at the studio host)
with the typed mangaka graph convention. Accepts an optional start
image (uploaded via /upload/image) + text prompt and produces an
animated WebP at the requested fps.

Pregel: upload -> build -> submit -> poll -> END
  - upload step is skipped automatically when start_image_b64 is empty
    (pure text-to-video, slower but no ref required).

Inputs:
    video_rkey               str
    start_image_b64          str  optional (base64 PNG) — pure t2v if empty
    start_image_mime         str
    prompt                   str
    negative_prompt          str
    width / height           int  default 832 / 1216 (manga panel aspect)
    length                   int  number of frames (default 33 = ~2s @ 16fps)
    fps                      int  default 16
    steps                    int  default 20
    cfg                      float default 5.0
    shift                    float ModelSamplingSD3 shift (Wan rec 8)
    seed                     int
    sampler / scheduler      str

Output (matches mangaka_generate_video shape):
    status                   "ok" | "error" | "timeout"
    images                   list[{node, filename, type, subfolder, imageInlineB64, imageMime, byteLen}]
                             — the WebP shows up here as image_mime=image/webp
    elapsed_ms               int
    error                    str | None

Time budget: Wan 2.2 5B fp16 on AMD Radeon 8060S ROCm 7.2 is ~6-10 min
for 33 frames @ 832x1216 (steps=20). Increase length / steps with care.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from lg_mangaka import comfy_runner as _runner
from lg_mangaka import comfy_workflows as _wf


class _State(TypedDict, total=False):
    video_rkey: str
    start_image_b64: str
    start_image_mime: str
    prompt: str
    negative_prompt: str
    width: int
    height: int
    length: int
    fps: int
    steps: int
    cfg: float
    shift: float
    seed: int
    sampler: str
    scheduler: str

    comfy_url: str
    timeout_seconds: int
    poll_interval_ms: int

    uploaded_filename: str | None
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
    if not state.get("start_image_b64"):
        # Pure text-to-video — no upload needed.
        return {"uploaded_filename": None}
    r = await _runner.upload_image_b64(
        state["start_image_b64"],
        comfy_url=state.get("comfy_url") or _runner.DEFAULT_URL,
        filename_hint=f"mangaka-wan-{state.get('video_rkey') or 'video'}",
        image_mime=state.get("start_image_mime") or "image/png",
    )
    if r.get("error"):
        return {"status": "error", "error": r["error"]}
    return {"uploaded_filename": r["filename"]}


async def _build(state: _State) -> dict[str, Any]:
    if state.get("status") == "error":
        return {}
    wf = _wf.video_wan_workflow(
        video_rkey=state.get("video_rkey") or "video",
        start_image_filename=state.get("uploaded_filename"),
        prompt=state.get("prompt") or "",
        negative_prompt=state.get("negative_prompt") or
            "blurry, low quality, watermark, distorted, oversaturated",
        width=int(state.get("width") or 832),
        height=int(state.get("height") or 1216),
        length=int(state.get("length") or 33),
        fps=int(state.get("fps") or 16),
        steps=int(state.get("steps") or 20),
        cfg=float(state.get("cfg") or 5.0),
        shift=float(state.get("shift") or 8.0),
        seed=int(state.get("seed") or 0),
        sampler=state.get("sampler") or "uni_pc",
        scheduler=state.get("scheduler") or "simple",
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
        # Wan video is slow — generous deadline.
        timeout_seconds=int(state.get("timeout_seconds") or 1200),
        poll_interval_ms=int(state.get("poll_interval_ms") or 5000),
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


GRAPH = _build_graph().compile(name="mangaka_generate_video_wan")
