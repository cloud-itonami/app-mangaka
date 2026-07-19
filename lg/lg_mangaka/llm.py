"""LLM helpers for lg_mangaka graphs.

Two surfaces:

  • `llm_json(system, user, *, model)` — calls the RunPod-served vLLM OpenAI-
    compatible endpoint (same URL as `agent_chat.py`) and asks for a JSON
    object back. Returns `dict` on success or `None` when the call fails or
    output is malformed. Caller is responsible for the fallback path.

  • `llm_vision_score(prompt, images_b64, *, model)` — calls OpenAI directly
    (`OPENAI_API_KEY`) for multimodal scoring. Used by `_step_critique_and_select`
    in P4. Returns `dict` or `None`.

Both helpers are defensive: failures log + return `None` rather than raising,
so the Pregel pipeline keeps moving with a deterministic fallback. Per the
mangaka CLAUDE.md, LLM auth failures (`401`) and rate-limit errors (`429`)
are still surfaced via `LLM error` log entries.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx

_log = logging.getLogger(__name__)

_VLLM_URL = os.environ.get(
    "VLLM_URL", "https://vyp99t9px7h4dl-4000.proxy.runpod.net/v1"
).rstrip("/")
_VLLM_MODEL = os.environ.get("VLLM_MODEL", "tier0-general")
_VLLM_TIMEOUT = float(os.environ.get("VLLM_TIMEOUT_SEC", "60"))

_OPENAI_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
_OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
_OPENAI_VISION_MODEL = os.environ.get("OPENAI_VISION_MODEL", "gpt-4o-mini")


# ── vLLM JSON path ────────────────────────────────────────────────────────

async def llm_json(
    system: str,
    user: str,
    *,
    model: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.4,
) -> dict[str, Any] | None:
    """Call vLLM chat/completions and parse the assistant reply as JSON.

    Tolerates fenced ```json blocks and leading prose. Returns `None` on any
    failure (network, parse, malformed) so the Pregel node can fall back.
    """
    payload = {
        "model": model or _VLLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    try:
        async with httpx.AsyncClient(timeout=_VLLM_TIMEOUT) as client:
            r = await client.post(
                f"{_VLLM_URL}/chat/completions",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as e:
        _log.warning("LLM error %s: %s", e.response.status_code, e.response.text[:200])
        return None
    except Exception as e:
        _log.warning("LLM call failed: %s", e)
        return None

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        _log.warning("LLM response missing choices[0].message.content: %s", str(data)[:200])
        return None

    return _parse_json_loose(content)


def _parse_json_loose(text: str) -> dict[str, Any] | None:
    """Parse JSON tolerating ```json fences and trailing prose."""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else text.strip()
    # Trim to outermost braces if there's prose surrounding.
    if not candidate.startswith("{"):
        i = candidate.find("{")
        j = candidate.rfind("}")
        if i >= 0 and j > i:
            candidate = candidate[i : j + 1]
    try:
        out = json.loads(candidate)
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        _log.warning("LLM JSON parse failed; head=%s", candidate[:120])
        return None


# ── OpenAI vision path (P4) ───────────────────────────────────────────────

async def llm_vision_score(
    prompt: str,
    images_b64: list[str],
    *,
    model: str | None = None,
    max_tokens: int = 512,
) -> dict[str, Any] | None:
    """Score images against `prompt` via OpenAI gpt-4o-mini-vision.

    `images_b64` are base64-encoded PNG strings (without the data: URI prefix).
    Returns `None` when `OPENAI_API_KEY` is unset or the call fails.
    """
    if not _OPENAI_KEY:
        return None
    if not images_b64:
        return None

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for b64 in images_b64:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            }
        )
    payload = {
        "model": model or _OPENAI_VISION_MODEL,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f"{_OPENAI_URL}/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {_OPENAI_KEY}",
                    "Content-Type": "application/json",
                },
            )
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as e:
        _log.warning("Vision LLM error %s: %s", e.response.status_code, e.response.text[:200])
        return None
    except Exception as e:
        _log.warning("Vision LLM failed: %s", e)
        return None

    try:
        content_str = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    return _parse_json_loose(content_str)
