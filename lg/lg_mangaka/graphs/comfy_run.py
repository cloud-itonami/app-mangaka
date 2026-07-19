"""mangaka `comfy_run` — passthrough to ComfyUI for arbitrary workflows.

The Studio LangGraph wrappers (cine_generate_scene / panel / video) hide
ComfyUI behind a hardcoded SDXL workflow. When you want the *real* ComfyUI
node graph — your own LoRAs, custom samplers, ControlNet, AnimateDiff,
etc. — design it in the embedded ComfyUI tab, hit **Save (API Format)** to
get the workflow JSON, paste it into this graph's Input, and Run.

Pregel super-steps:

  start → submit → poll → END

  submit   POST workflow → /prompt → {prompt_id}
  poll     loop GET /history/{prompt_id} until outputs land,
           then GET /view for every image node and inline its base64

Inputs:
    workflow             dict   — ComfyUI API-format workflow JSON
    comfy_url            str?   — base URL, default http://192.168.1.70:8188
    client_id            str?   — optional ComfyUI client_id
    timeout_seconds      int    — overall poll deadline (default 300)
    poll_interval_ms     int    — between /history polls (default 1500)

Output:
    status      "ok" | "error" | "timeout"
    prompt_id   str
    images      list[{node, filename, type, subfolder, imageInlineB64, imageMime, byteLen}]
    raw_history dict           — the /history entry (last poll)
    elapsed_ms  int
    error       str | None
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from typing import Any, TypedDict

import httpx
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

_log = logging.getLogger(__name__)
_DEFAULT_URL = (
    os.environ.get("COMFY_POD_URL")
    or os.environ.get("COMFYUI_POD_URL")
    or os.environ.get("COMFYUI_URL")
    or "http://192.168.1.70:8188"
).rstrip("/")


class _State(TypedDict, total=False):
    # input
    workflow: dict
    comfy_url: str
    client_id: str
    timeout_seconds: int
    poll_interval_ms: int

    # submit output
    prompt_id: str
    number: int
    submit_response: dict
    started_at_ms: int

    # poll output
    status: str
    images: list
    raw_history: dict
    elapsed_ms: int
    error: str | None


def _base_url(state: _State) -> str:
    return (state.get("comfy_url") or _DEFAULT_URL).rstrip("/")


# ── submit ────────────────────────────────────────────────────────────────

async def _submit(state: _State) -> dict[str, Any]:
    wf = state.get("workflow")
    if not wf or not isinstance(wf, dict):
        return {"status": "error", "error": "workflow (object) required"}

    base = _base_url(state)
    body: dict[str, Any] = {"prompt": wf}
    if state.get("client_id"):
        body["client_id"] = state["client_id"]

    started_ms = int(time.time() * 1000)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(f"{base}/prompt", json=body,
                                  headers={"user-agent": "studio-comfy-run/0.1"})
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"POST /prompt failed: {exc}",
                "started_at_ms": started_ms}

    if r.status_code != 200:
        return {"status": "error",
                "error": f"/prompt HTTP {r.status_code}: {r.text[:300]}",
                "started_at_ms": started_ms}

    j = r.json() or {}
    pid = j.get("prompt_id")
    if not pid:
        return {"status": "error",
                "error": f"/prompt missing prompt_id: {j}",
                "started_at_ms": started_ms}

    return {
        "prompt_id": pid,
        "number": int(j.get("number") or 0),
        "submit_response": j,
        "started_at_ms": started_ms,
    }


# ── poll ──────────────────────────────────────────────────────────────────

async def _poll(state: _State) -> dict[str, Any]:
    if state.get("status") == "error":
        return {
            "status": "error",
            "elapsed_ms": int(time.time() * 1000) - int(state.get("started_at_ms") or 0),
        }

    base = _base_url(state)
    pid = state.get("prompt_id") or ""
    if not pid:
        return {"status": "error", "error": "no prompt_id"}

    timeout_s = max(10, min(900, int(state.get("timeout_seconds") or 300)))
    interval_ms = max(250, min(10000, int(state.get("poll_interval_ms") or 1500)))
    deadline = time.monotonic() + timeout_s
    headers = {"user-agent": "studio-comfy-run/0.1"}
    images: list[dict[str, Any]] = []
    last_history: dict[str, Any] = {}

    async with httpx.AsyncClient(timeout=30.0) as client:
        while time.monotonic() < deadline:
            try:
                hr = await client.get(f"{base}/history/{pid}", headers=headers)
            except Exception as exc:  # noqa: BLE001
                _log.warning("history poll failed (retrying): %s", exc)
                await asyncio.sleep(interval_ms / 1000.0)
                continue
            if hr.status_code != 200:
                await asyncio.sleep(interval_ms / 1000.0)
                continue

            entry = (hr.json() or {}).get(pid)
            if not entry:
                await asyncio.sleep(interval_ms / 1000.0)
                continue
            last_history = entry

            status = (entry.get("status") or {})
            messages = status.get("messages") or []
            for m in messages:
                if isinstance(m, list) and len(m) >= 2 and m[0] in (
                    "execution_error", "execution_interrupted",
                ):
                    return {
                        "status": "error",
                        "error": f"{m[0]}: {str(m[1])[:400]}",
                        "raw_history": entry,
                        "elapsed_ms": int(time.time() * 1000) - int(state.get("started_at_ms") or 0),
                    }

            outputs = entry.get("outputs") or {}
            if not outputs:
                await asyncio.sleep(interval_ms / 1000.0)
                continue

            # Fetch every image artifact from every node that produced one.
            for node_id, node_out in outputs.items():
                for img in (node_out.get("images") or []):
                    params = {
                        "filename": img.get("filename", ""),
                        "subfolder": img.get("subfolder", ""),
                        "type": img.get("type", "output"),
                    }
                    try:
                        vr = await client.get(f"{base}/view", headers=headers, params=params)
                    except Exception as exc:  # noqa: BLE001
                        return {
                            "status": "error",
                            "error": f"/view fetch failed: {exc}",
                            "raw_history": entry,
                            "elapsed_ms": int(time.time() * 1000) - int(state.get("started_at_ms") or 0),
                        }
                    if vr.status_code != 200:
                        continue
                    body = vr.content or b""
                    images.append({
                        "node": str(node_id),
                        "filename": params["filename"],
                        "type": params["type"],
                        "subfolder": params["subfolder"],
                        "imageInlineB64": base64.b64encode(body).decode("ascii"),
                        "imageMime": vr.headers.get("content-type", "image/png"),
                        "byteLen": len(body),
                    })
            return {
                "status": "ok",
                "images": images,
                "raw_history": entry,
                "elapsed_ms": int(time.time() * 1000) - int(state.get("started_at_ms") or 0),
            }

    return {
        "status": "timeout",
        "error": f"poll deadline {timeout_s}s exceeded",
        "raw_history": last_history,
        "elapsed_ms": int(time.time() * 1000) - int(state.get("started_at_ms") or 0),
    }


# ── build ─────────────────────────────────────────────────────────────────

def _build() -> StateGraph:
    g: StateGraph = StateGraph(_State)
    g.add_node("submit", _submit, retry_policy=RetryPolicy(max_attempts=2))
    g.add_node("poll",   _poll)
    g.add_edge(START, "submit")
    g.add_edge("submit", "poll")
    g.add_edge("poll", END)
    return g


GRAPH = _build().compile(name="comfy_run")
