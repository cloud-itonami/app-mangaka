"""mangaka_generate_page_flux — whole-page composite via Flux.1 [dev].

Flux variant of mangaka_generate_page. Iterates the per-panel bbox list,
runs panel_flux_workflow for each (sequential through ComfyUI's queue),
then PIL-composites the inked Flux outputs onto a manga page canvas with
configurable gutter + border.

No character reference image upload — Flux is text-only here. Character
look stability comes from consistent prompt phrasing across panels
(same character description verbatim, same style anchor). The shared
style_prompt + per-panel framing/action variation gives a coherent page
without the IPAdapter / PuLID stack.

Pregel: plan -> render -> composite -> END

Inputs:
    page_rkey               str
    page_width              int    default 1280
    page_height             int    default 1817
    gutter                  int    default 14
    border                  int    default 2
    seed_base               int    base seed (per-panel = seed + i*1009)
    panels                  list   each:
       { panel_rkey, x, y, w, h, framing, characters[], environment, mood, action }
    style_prompt            str    shared style anchor (default manga ink)
    width                   int    per-panel render width (default 1024)
    height                  int    per-panel render height (default 1536)
    steps                   int    default 22
    guidance                float  default 3.5

Output:
    status                  "ok" | "error"
    page_image_inline_b64   str   composited page PNG
    panel_results           list  per-panel { panel_rkey, image_b64, latency_ms, ok }
    elapsed_ms              int
    error                   str | None

Time budget: Flux Q4 on AMD ROCm runs ~180s per 1024x1536 panel, so a
4-panel page takes ~12 minutes. Increase the langgraph stream timeout
when calling from Studio.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import time
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from lg_mangaka import comfy_runner as _runner
from lg_mangaka import comfy_workflows as _wf

_log = logging.getLogger(__name__)


def _merge_list(a: list | None, b: list | None) -> list:
    return list(a or []) + list(b or [])


class _State(TypedDict, total=False):
    page_rkey: str
    page_width: int
    page_height: int
    gutter: int
    border: int

    panels: list
    style_prompt: str

    width: int
    height: int
    steps: int
    guidance: float
    seed_base: int

    comfy_url: str
    timeout_seconds: int

    panel_results: Annotated[list, _merge_list]
    page_image_inline_b64: str
    status: str
    elapsed_ms: int
    error: str | None
    started_at_ms: int


# ── plan ───────────────────────────────────────────────────────────────────

async def _plan(state: _State) -> dict[str, Any]:
    if not state.get("panels"):
        return {"status": "error", "error": "panels (list) required"}
    return {"started_at_ms": int(time.time() * 1000)}


# ── render (sequential through ComfyUI's queue) ────────────────────────────

async def _render_all_panels(state: _State) -> dict[str, Any]:
    if state.get("status") == "error":
        return {}
    panels = state.get("panels") or []
    comfy_url = state.get("comfy_url") or _runner.DEFAULT_URL
    seed_base = int(state.get("seed_base") or 0) or int(time.time())
    style = state.get("style_prompt") or (
        "manga inked panel, sharp black ink lines, screentone, hatching, "
        "high contrast monochrome, masterpiece, best quality, "
        "shonen-jump style"
    )
    panel_w = int(state.get("width") or 1024)
    panel_h = int(state.get("height") or 1536)
    steps = int(state.get("steps") or 22)
    guidance = float(state.get("guidance") or 3.5)

    results: list[dict[str, Any]] = []
    for i, p in enumerate(panels):
        wf = _wf.panel_flux_workflow(
            panel_rkey=str(p.get("panel_rkey") or f"p{i:02d}"),
            framing=p.get("framing") or "medium",
            characters=list(p.get("characters") or []),
            environment=p.get("environment") or "",
            mood=p.get("mood") or "",
            action=p.get("action") or "",
            width=panel_w,
            height=panel_h,
            steps=steps,
            guidance=guidance,
            seed=(seed_base + i * 1009) & 0xFFFFFFFF,
            style_prompt=style,
        )
        sub = await _runner.submit_workflow(wf, comfy_url=comfy_url)
        if sub.get("status") == "error":
            results.append({
                "panel_rkey": p.get("panel_rkey") or f"p{i:02d}",
                "ok": False, "error": sub.get("error"),
            })
            continue
        poll = await _runner.poll_outputs(
            sub["prompt_id"],
            comfy_url=comfy_url,
            started_at_ms=sub.get("started_at_ms") or 0,
            timeout_seconds=int(state.get("timeout_seconds") or 600),
        )
        imgs = poll.get("images") or []
        first = imgs[0] if imgs else None
        results.append({
            "panel_rkey": p.get("panel_rkey") or f"p{i:02d}",
            "x": int(p.get("x") or 0), "y": int(p.get("y") or 0),
            "w": int(p.get("w") or 0), "h": int(p.get("h") or 0),
            "ok": bool(first),
            "image_b64": (first or {}).get("imageInlineB64") or "",
            "image_mime": (first or {}).get("imageMime") or "image/png",
            "latency_ms": poll.get("elapsed_ms") or 0,
            "error": poll.get("error"),
        })

    return {"panel_results": results}


# ── composite (PIL) ────────────────────────────────────────────────────────

def _composite_blocking(state: _State) -> tuple[str, str | None]:
    try:
        from PIL import Image, ImageDraw  # noqa: WPS433
    except Exception as exc:  # noqa: BLE001
        return "", f"PIL not available: {exc}"

    panels = state.get("panel_results") or []
    w = int(state.get("page_width") or 1280)
    h = int(state.get("page_height") or 1817)
    gutter = int(state.get("gutter") or 14)
    border = int(state.get("border") or 2)
    canvas = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(canvas)

    for pr in panels:
        if not pr.get("ok") or not pr.get("image_b64"):
            continue
        try:
            img = Image.open(io.BytesIO(base64.b64decode(pr["image_b64"]))).convert("RGB")
        except Exception:  # noqa: BLE001
            continue
        bx, by, bw, bh = pr.get("x", 0), pr.get("y", 0), pr.get("w", 0), pr.get("h", 0)
        if bw <= 0 or bh <= 0:
            continue
        target_w = max(1, bw - gutter)
        target_h = max(1, bh - gutter)
        # Resize preserving aspect, then center-crop.
        src_ratio = img.width / img.height
        dst_ratio = target_w / target_h
        if src_ratio > dst_ratio:
            new_h = target_h
            new_w = int(round(target_h * src_ratio))
        else:
            new_w = target_w
            new_h = int(round(target_w / src_ratio))
        img = img.resize((new_w, new_h), Image.LANCZOS)
        ox = (new_w - target_w) // 2
        oy = (new_h - target_h) // 2
        img = img.crop((ox, oy, ox + target_w, oy + target_h))
        canvas.paste(img, (bx + gutter // 2, by + gutter // 2))
        if border > 0:
            draw.rectangle(
                [(bx + gutter // 2, by + gutter // 2),
                 (bx + gutter // 2 + target_w - 1, by + gutter // 2 + target_h - 1)],
                outline="black", width=border,
            )

    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    return base64.b64encode(out.getvalue()).decode("ascii"), None


async def _composite(state: _State) -> dict[str, Any]:
    if state.get("status") == "error":
        return {}
    b64, err = await asyncio.to_thread(_composite_blocking, state)
    elapsed = int(time.time() * 1000) - int(state.get("started_at_ms") or 0)
    if err:
        return {"status": "error", "error": err, "elapsed_ms": elapsed}
    return {
        "page_image_inline_b64": b64,
        "status": "ok",
        "elapsed_ms": elapsed,
    }


def _build_graph() -> StateGraph:
    g: StateGraph = StateGraph(_State)
    g.add_node("plan",      _plan, retry_policy=RetryPolicy(max_attempts=2))
    g.add_node("render",    _render_all_panels)
    g.add_node("composite", _composite)
    g.add_edge(START, "plan")
    g.add_edge("plan", "render")
    g.add_edge("render", "composite")
    g.add_edge("composite", END)
    return g


GRAPH = _build_graph().compile(name="mangaka_generate_page_flux")
