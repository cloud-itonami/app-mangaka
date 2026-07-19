"""mangaka `cine_generate_video` — N-frame batch through ComfyUI + ffmpeg encode.

Lightweight video generator that exercises the full image-gen path against
the LAN ComfyUI (or comfyui.etzhayyim.com gateway) for the diffusion stage, then
muxes the resulting frames into an MP4 via ffmpeg. Designed for Studio UI
inspection — the output `videoInlineB64` lets the <video> tag play the clip
inline without a /blob proxy.

Pregel super-steps:

  ┌─ start
  │
  ├─ expand              — LLM-expand prompt into per-frame variation seeds
  │
  ├─ frame_dispatch      — Send(render_frame) × N
  ├─ render_frame (×N)   — one ComfyUI call per frame
  │
  ├─ aggregate           — collect frames in order
  ├─ encode              — ffmpeg: frames → mp4 (libx264 yuv420p)
  └─ END

Inputs:
    prompt              str    — required, natural-language scene description
    frame_count         int    — 4..64 (default 8)
    fps                 int    — output fps (default 8)
    size                str    — "WIDTHxHEIGHT" (default "832x1216")
    seed                int    — base seed; per-frame seeds = seed + i*1009
    sampler_steps       int    — default 12
    cfg_scale_x10       int    — default 70 (= 7.5)
    style               str    — optional style tag
    dry_run             bool   — when true, skip ffmpeg encode if no frames

Output:
    status                "video_ready" | "error"
    frame_count           int
    frames                list[{frameIndex, source, latencyMs, imageMime}]
    videoInlineB64        str   (data: base64 mp4, empty when no frames)
    videoMime             "video/mp4"
    fps                   int
    elapsedMs             int
    error                 str | None

Note: every frame is a fresh ComfyUI dispatch — there is no temporal
consistency model here. For real animation use AnimateDiff or a
SparseControl + ControlNet workflow; this graph is for the Studio
inspection path.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import shutil
import subprocess
import tempfile
import time
from typing import Annotated, Any, Dict, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy, Send

from lg_mangaka import comfy as _comfy

_log = logging.getLogger(__name__)
def _resolve_ffmpeg() -> str:
    """Resolve the ffmpeg binary path.

    Resolution order:
      1. shutil.which("ffmpeg") — PATH lookup (preferred in containers).
      2. FFMPEG_BIN env var — validated: absolute path + exists + executable.
         Invalid value is a hard error at import time (fail fast).
      3. bare "ffmpeg" sentinel — FileNotFoundError caught in _encode.
    """
    found = shutil.which("ffmpeg")
    if found:
        return found

    raw = os.environ.get("FFMPEG_BIN", "").strip()
    if raw:
        if not os.path.isabs(raw):
            raise ValueError(f"FFMPEG_BIN must be an absolute path, got: {raw!r}")
        if not os.path.isfile(raw):
            raise ValueError(f"FFMPEG_BIN path does not exist or is not a file: {raw!r}")
        if not os.access(raw, os.X_OK):
            raise ValueError(f"FFMPEG_BIN path is not executable: {raw!r}")
        return raw

    return "ffmpeg"


_FFMPEG = _resolve_ffmpeg()


def _merge_dict(a: Dict[str, Any] | None, b: Dict[str, Any] | None) -> Dict[str, Any]:
    out = dict(a or {})
    out.update(b or {})
    return out


def _merge_list(a: list | None, b: list | None) -> list:
    return list(a or []) + list(b or [])


# ── state ─────────────────────────────────────────────────────────────────

class _State(TypedDict, total=False):
    prompt: str
    frame_count: int
    fps: int
    size: str
    seed: int
    sampler_steps: int
    cfg_scale_x10: int
    style: str
    dry_run: bool

    # super-step outputs
    expanded_prompt: str
    frame_plan: list                                # [{i, seed, prompt}]
    # frame PNG bytes base64-encoded (state must be JSON-serializable).
    # Key is the frame index as a string ("0", "1", ...) — TypedDict keys
    # int → JSON object key str round-trip kept consistent.
    frame_b64: Annotated[Dict[str, str], _merge_dict]
    # [{frameIndex, source, latencyMs, imageMime, ...}] — concatenated across Send fan-out
    frames: Annotated[list, _merge_list]

    # output
    status: str
    error: str | None
    videoInlineB64: str
    videoMime: str
    fps: int
    elapsedMs: int


# ── expand ────────────────────────────────────────────────────────────────

async def _expand(state: _State) -> dict[str, Any]:
    prompt = (state.get("prompt") or "").strip()
    if not prompt:
        return {"error": "prompt required", "status": "error"}

    fc = max(4, min(64, int(state.get("frame_count") or 8)))
    seed = int(state.get("seed") or int(time.time())) & 0xFFFFFFFF
    style = state.get("style") or ""
    style_suffix = f", style: {style}" if style else ""

    # Per-frame "motion" hint just nudges the seed so the model produces
    # progressively different stills. Frame index → small phrase rotation.
    phases = [
        "wide establishing", "medium shot", "low angle", "high angle",
        "tracking left", "tracking right", "dolly in", "dolly out",
    ]
    plan = []
    for i in range(fc):
        phase = phases[i % len(phases)]
        plan.append({
            "i": i,
            "seed": (seed + i * 1009) & 0xFFFFFFFF,
            "prompt": f"{prompt}, {phase}{style_suffix}, sharp ink lines, monochrome",
        })

    return {"expanded_prompt": prompt + style_suffix, "frame_plan": plan}


# ── frame fan-out ─────────────────────────────────────────────────────────

def _frame_dispatch(state: _State) -> list[Send]:
    plan = state.get("frame_plan") or []
    return [
        Send("render_frame", {
            "i": p["i"],
            "seed": p["seed"],
            "prompt": p["prompt"],
            "sampler_steps": int(state.get("sampler_steps") or 12),
            "cfg_scale_x10": int(state.get("cfg_scale_x10") or 70),
            "size": state.get("size") or "832x1216",
        })
        for p in plan
    ]


async def _render_frame(payload: dict[str, Any]) -> dict[str, Any]:
    """One ComfyUI call. Returns frame bytes base64-encoded under str index."""
    i = int(payload["i"])
    r = await _comfy.refine(
        prompt=payload["prompt"],
        seed=int(payload["seed"]),
        steps=int(payload["sampler_steps"]),
        cfg_x10=int(payload["cfg_scale_x10"]),
        size=str(payload["size"]),
    )
    img = r.get("image_bytes") or b""
    b64 = r.get("image_b64") or (base64.b64encode(img).decode("ascii") if img else "")
    return {
        "frame_b64": {str(i): b64},
        "frames": [{
            "frameIndex": i,
            "source": r.get("source"),
            "imageMime": r.get("image_mime"),
            "latencyMs": r.get("latency_ms"),
            "seed": int(r.get("seed") or payload["seed"]),
            "byteLen": len(img),
            "ok": bool(r.get("ok")),
        }],
    }


# ── aggregate ─────────────────────────────────────────────────────────────

async def _aggregate(state: _State) -> dict[str, Any]:
    frames = sorted(state.get("frames") or [], key=lambda f: f["frameIndex"])
    return {"frames": frames}


# ── encode ────────────────────────────────────────────────────────────────

async def _encode(state: _State) -> dict[str, Any]:
    started = time.monotonic()
    frame_b64: Dict[str, str] = state.get("frame_b64") or {}
    # Decode once into a sorted-by-int-index list of bytes.
    frames_by_idx: Dict[int, bytes] = {}
    for k, v in frame_b64.items():
        if v:
            try:
                frames_by_idx[int(k)] = base64.b64decode(v)
            except Exception:  # noqa: BLE001
                continue
    fc = len(frames_by_idx)
    fps = max(1, min(60, int(state.get("fps") or 8)))

    if fc == 0:
        return {
            "status": "error",
            "error": "no frames rendered",
            "videoInlineB64": "",
            "videoMime": "video/mp4",
            "fps": fps,
            "elapsedMs": int((time.monotonic() - started) * 1000),
        }

    # Write frames as PNG to a temp dir, then ffmpeg → mp4. Run subprocess
    # in a thread so we don't block the event loop.
    def _run_ffmpeg() -> bytes:
        with tempfile.TemporaryDirectory(prefix="cine-video-") as tmp:
            for i in sorted(frames_by_idx.keys()):
                with open(os.path.join(tmp, f"f{i:05d}.png"), "wb") as f:
                    f.write(frames_by_idx[i])
            out_path = os.path.join(tmp, "out.mp4")
            cmd = [
                _FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                "-framerate", str(fps),
                "-i", os.path.join(tmp, "f%05d.png"),
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                # Even-dimension filter so libx264 accepts arbitrary input sizes.
                "-vf", "scale='trunc(iw/2)*2':'trunc(ih/2)*2'",
                "-movflags", "+faststart",
                out_path,
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            with open(out_path, "rb") as f:
                return f.read()

    try:
        mp4 = await asyncio.to_thread(_run_ffmpeg)
    except subprocess.CalledProcessError as exc:
        return {
            "status": "error",
            "error": f"ffmpeg failed (rc={exc.returncode}): {exc.stderr.decode('utf-8', 'replace')[:300]}",
            "videoInlineB64": "",
            "videoMime": "video/mp4",
            "fps": fps,
            "elapsedMs": int((time.monotonic() - started) * 1000),
        }
    except FileNotFoundError:
        return {
            "status": "error",
            "error": (
                    f"ffmpeg not found ({_FFMPEG!r}) — "
                    "install ffmpeg in the container or set FFMPEG_BIN to an absolute executable path"
                ),
            "videoInlineB64": "",
            "videoMime": "video/mp4",
            "fps": fps,
            "elapsedMs": int((time.monotonic() - started) * 1000),
        }

    return {
        "status": "video_ready",
        "frame_count": fc,
        "videoInlineB64": base64.b64encode(mp4).decode("ascii"),
        "videoMime": "video/mp4",
        "fps": fps,
        "elapsedMs": int((time.monotonic() - started) * 1000),
    }


# ── build ─────────────────────────────────────────────────────────────────

def _build() -> StateGraph:
    g: StateGraph = StateGraph(_State)
    g.add_node("expand",        _expand)
    g.add_node("render_frame",  _render_frame,
               retry_policy=RetryPolicy(max_attempts=2))
    g.add_node("aggregate",     _aggregate)
    g.add_node("encode",        _encode)

    g.add_edge(START, "expand")
    g.add_conditional_edges("expand", _frame_dispatch, ["render_frame"])
    g.add_edge("render_frame", "aggregate")
    g.add_edge("aggregate", "encode")
    g.add_edge("encode", END)
    return g


GRAPH = _build().compile(name="cine_generate_video")
