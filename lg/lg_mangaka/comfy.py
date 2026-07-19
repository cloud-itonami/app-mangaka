"""ComfyUI client for the diffusionPass stage of kami-cine.

PRIMARY target: the production CF Worker gateway at `comfyui.etzhayyim.com`,
which exposes an **OpenAI-compatible** /v1/images/generations endpoint
(Bearer `sk_live_*` API key required). The Worker translates the OpenAI
shape into raw ComfyUI workflows and dispatches to the Mac mini fleet
(jacob:8200 → murakumo.lan:8188 vanilla ComfyUI) per ADR 2026-05-13.

FALLBACK target (legacy / direct dev): if `COMFY_POD_URL` points at a raw
ComfyUI pod (e.g. RunPod), submits a workflow there. Only used when
`COMFYUI_URL` is empty.

GRACEFUL DEGRADATION: any failure path (no key, network error, 502,
timeout) returns a base64 SVG placeholder so the Studio UI still has
something to render and the dev loop never hard-fails.

Environment:
    COMFYUI_URL       gateway base, default "https://comfyui.etzhayyim.com"
    COMFYUI_API_KEY   sk_live_* Bearer (required for the gateway path)
    COMFY_POD_URL     legacy raw-ComfyUI pod (alternative to the gateway)
    COMFY_DEFAULT_CKPT  override the model (default leaves gateway choice)
    COMFY_TIMEOUT_SEC   per-request timeout (default 300)

Return:
    {
      ok: bool, source: "gateway"|"pod"|"stub",
      image_bytes: bytes, image_mime: str, image_b64: str,
      model: str, seed: int, latency_ms: int, error: str | None
    }
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import Any

import httpx

_log = logging.getLogger(__name__)

# Primary: CF Worker OpenAI gateway. Empty → skip gateway, try pod path.
_GATEWAY_URL = (os.environ.get("COMFYUI_URL") or "https://comfyui.etzhayyim.com").rstrip("/")
_API_KEY = (
    os.environ.get("COMFYUI_API_KEY")
    or os.environ.get("SS_COMFYUI_API_KEY")
    or ""
).strip()

# Fallback: raw ComfyUI pod (no auth, RunPod proxy or local).
_POD_URL = (os.environ.get("COMFY_POD_URL") or os.environ.get("COMFYUI_POD_URL") or "").rstrip("/")

_DEFAULT_CKPT = os.environ.get("COMFY_DEFAULT_CKPT", "animagine-xl-4.0.safetensors")
_TIMEOUT = float(os.environ.get("COMFY_TIMEOUT_SEC", "300"))
_UA = "lg-mangaka/0.1 (+studio.etzhayyim.com)"
_POLL_INTERVAL = 2.0
_POLL_MAX = 150  # × 2s = 300s ceiling


def is_configured() -> bool:
    return bool((_GATEWAY_URL and _API_KEY) or _POD_URL)


async def refine(
    *,
    prompt: str,
    seed: int = 0,
    steps: int = 20,
    cfg_x10: int = 75,
    size: str = "832x1216",
) -> dict[str, Any]:
    """Generate one PNG from the prompt. Returns a stub SVG on any failure."""
    started = time.monotonic()

    # 1. Gateway path (preferred — production-grade auth + fleet routing).
    if _GATEWAY_URL and _API_KEY:
        try:
            r = await _via_gateway(prompt=prompt, seed=seed, size=size)
            r["latency_ms"] = int((time.monotonic() - started) * 1000)
            return r
        except Exception as exc:  # noqa: BLE001
            _log.warning("comfy gateway failed, trying pod path: %s", exc)

    # 2. Pod path (legacy / direct RunPod).
    if _POD_URL:
        try:
            r = await _via_raw_pod(prompt=prompt, seed=seed, steps=steps, cfg_x10=cfg_x10, size=size)
            r["latency_ms"] = int((time.monotonic() - started) * 1000)
            return r
        except Exception as exc:  # noqa: BLE001
            _log.warning("comfy raw pod failed: %s", exc)

    # 3. Stub fallback.
    why = (
        "no COMFYUI_API_KEY (gateway path)" if (_GATEWAY_URL and not _API_KEY)
        else "no COMFYUI_URL or COMFY_POD_URL" if not (_GATEWAY_URL or _POD_URL)
        else "all upstream paths failed"
    )
    return _stub(prompt, seed, why, started)


# ── gateway (OpenAI-compatible) ───────────────────────────────────────────

async def _via_gateway(*, prompt: str, seed: int, size: str) -> dict[str, Any]:
    """POST /v1/images/generations on comfyui.etzhayyim.com."""
    body: dict[str, Any] = {
        "prompt": prompt,
        "n": 1,
        "size": size,
        "model": _DEFAULT_CKPT,
        "response_format": "b64_json",
    }
    if seed:
        body["seed"] = seed
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {_API_KEY}",
        "user-agent": _UA,
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(
            f"{_GATEWAY_URL}/v1/images/generations",
            headers=headers,
            content=json.dumps(body),
        )
    if r.status_code != 200:
        raise RuntimeError(f"gateway HTTP {r.status_code}: {r.text[:300]}")
    j = r.json()
    data = j.get("data") or []
    if not data:
        raise RuntimeError("gateway returned empty data[]")
    b64 = data[0].get("b64_json") or ""
    if not b64:
        raise RuntimeError("gateway data[0] missing b64_json")
    img_bytes = base64.b64decode(b64)
    return {
        "ok": True,
        "source": "gateway",
        "image_bytes": img_bytes,
        "image_mime": "image/png",
        "image_b64": b64,
        "model": j.get("model") or _DEFAULT_CKPT,
        "seed": int(j.get("seed") or seed),
        "error": None,
    }


# ── raw pod (legacy / direct ComfyUI on port 8188) ────────────────────────

def _raw_workflow(prompt: str, seed: int, steps: int, cfg_x10: int, w: int, h: int) -> dict[str, Any]:
    return {
        "3": {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": steps, "cfg": cfg_x10 / 10.0,
            "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
            "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0],
            "latent_image": ["5", 0],
        }},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": _DEFAULT_CKPT}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {
            "text": "blurry, low quality, watermark, signature, text",
            "clip": ["4", 1],
        }},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "cine", "images": ["8", 0]}},
    }


async def _via_raw_pod(*, prompt: str, seed: int, steps: int, cfg_x10: int, size: str) -> dict[str, Any]:
    w, h = _parse_size(size)
    real_seed = seed or int(time.time()) & 0xFFFFFFFF
    workflow = _raw_workflow(prompt, real_seed, steps, cfg_x10, w, h)
    headers = {"content-type": "application/json", "user-agent": _UA}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(
            f"{_POD_URL}/prompt", headers=headers,
            content=json.dumps({"prompt": workflow}),
        )
        if r.status_code != 200:
            raise RuntimeError(f"/prompt HTTP {r.status_code}: {r.text[:200]}")
        pid = r.json().get("prompt_id")
        if not pid:
            raise RuntimeError("/prompt missing prompt_id")
        # Poll /history/{pid}
        import asyncio
        for _ in range(_POLL_MAX):
            await asyncio.sleep(_POLL_INTERVAL)
            h_resp = await client.get(f"{_POD_URL}/history/{pid}", headers=headers)
            if h_resp.status_code != 200:
                continue
            entry = (h_resp.json() or {}).get(pid)
            if not entry:
                continue
            outputs = entry.get("outputs") or {}
            first = next(
                (img for node in outputs.values() for img in (node.get("images") or [])),
                None,
            )
            if not first:
                continue
            params = {
                "filename": first.get("filename", ""),
                "subfolder": first.get("subfolder", ""),
                "type": first.get("type", "output"),
            }
            v = await client.get(f"{_POD_URL}/view", headers=headers, params=params)
            if v.status_code != 200:
                raise RuntimeError(f"/view HTTP {v.status_code}")
            return {
                "ok": True,
                "source": "pod",
                "image_bytes": v.content,
                "image_mime": v.headers.get("content-type", "image/png"),
                "image_b64": base64.b64encode(v.content).decode("ascii"),
                "model": _DEFAULT_CKPT,
                "seed": real_seed,
                "error": None,
            }
    raise RuntimeError("pod poll timeout")


def _parse_size(size: str) -> tuple[int, int]:
    try:
        w, h = size.lower().split("x", 1)
        return int(w), int(h)
    except Exception:
        return 832, 1216


# ── stub ──────────────────────────────────────────────────────────────────

def _stub(prompt: str, seed: int, why: str, started: float) -> dict[str, Any]:
    safe_prompt = (prompt or "(empty)")[:80].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    safe_why = why.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 832 1216">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#1f2937"/><stop offset="1" stop-color="#111827"/>'
        '</linearGradient></defs>'
        '<rect width="832" height="1216" fill="url(#g)"/>'
        '<g fill="#9ca3af" font-family="ui-monospace,Menlo,monospace">'
        f'<text x="40" y="80" font-size="28" fill="#e5e7eb">cine.diffusionPass · STUB</text>'
        f'<text x="40" y="130" font-size="18">prompt:</text>'
        f'<text x="40" y="160" font-size="16" fill="#d1d5db">{safe_prompt}</text>'
        f'<text x="40" y="220" font-size="16">seed: {seed}</text>'
        f'<text x="40" y="250" font-size="16">why stub: {safe_why}</text>'
        f'<text x="40" y="1180" font-size="14" fill="#6b7280">'
        f'set COMFYUI_API_KEY (sk_live_*) to use comfyui.etzhayyim.com gateway</text>'
        '</g></svg>'
    )
    body = svg.encode("utf-8")
    return {
        "ok": False,
        "source": "stub",
        "image_bytes": body,
        "image_mime": "image/svg+xml",
        "image_b64": base64.b64encode(body).decode("ascii"),
        "model": "stub",
        "seed": seed,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "error": why,
    }
