"""mangaka `agent_chat` graph — manga production AI persona chat.

Per mangaka CLAUDE.md, the project has 7 production-stage actors:
  writer / storyboarder / penciler / inker / toner / letterer / colorist

This graph forwards a single user turn to RunPod vLLM with a stage-
specific system prompt. Replaces LLM-side calls in BPMN
`pipelineChat` / `projectChatStandalone` / `breakdownPage` /
`generateScript`.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, TypedDict

import httpx
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from lg_mangaka.audit import emit_audit_bg

_log = logging.getLogger(__name__)


class _ChatState(TypedDict, total=False):
    actor_role: str          # writer / storyboarder / penciler / inker / toner / letterer / colorist
    work_id: str
    chapter_id: str
    page_id: str
    user_did: str
    message: str
    history: list[dict[str, Any]]
    max_tokens: int
    temperature: float
    reply: str
    actor_did: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: int
    error: str | None


_VLLM_URL = os.environ.get("VLLM_URL", "https://vyp99t9px7h4dl-4000.proxy.runpod.net/v1").rstrip("/")
_VLLM_MODEL = os.environ.get("VLLM_MODEL", "tier0-general")
_VLLM_TIMEOUT = float(os.environ.get("VLLM_TIMEOUT_SEC", "60"))
_DEFAULT_APP_DID = os.environ.get("MANGAKA_APP_DID", "did:web:mangaka.etzhayyim.com")


_ACTOR_PROMPTS: dict[str, str] = {
    "writer": (
        "You are a manga writer (gensaku-sha) AI. Your job: draft scripts "
        "(plot beats, dialogue, panel directions). Reply in standard manga "
        "script format: PAGE/PANEL annotations + dialogue + action notes. "
        "Cap at 4 pages per turn. Genre-conscious (shōnen / seinen / shōjo / etc.)."
    ),
    "storyboarder": (
        "You are a manga storyboarder (nemu / name) AI. You break a script "
        "page into 4-9 panels: shot type, character placement, focal beat. "
        "Reply with a numbered panel list, 1 line per panel. Note any sfx."
    ),
    "penciler": (
        "You are a penciler AI. You describe rough character poses + key "
        "props for each panel. Reply with structured pose notes: "
        "Character / Action / Expression / Camera angle. Max 6 panels per turn."
    ),
    "inker": (
        "You are an inker AI. You describe line weight + cross-hatching "
        "decisions for a panel. Be concise — 2-3 sentences per panel. "
        "Reference specific elements (face, costume, BG)."
    ),
    "toner": (
        "You are a screen-toner AI. You suggest tone density + pattern + "
        "placement per panel. Format: 'Panel N: [tone family] @ [%density] "
        "for [region]'. Cap at 6 panels per turn."
    ),
    "letterer": (
        "You are a letterer AI. You set balloon shape, tail direction, "
        "font choice for dialogue. Reply: 'Balloon N: [shape] / [font] / "
        "tail toward [character] / [position note]'."
    ),
    "colorist": (
        "You are a colorist AI. You choose color palettes for cover/key "
        "art (mostly B/W body, color highlights). Reply with a 3-5 hex "
        "color palette + 1-line mood justification."
    ),
}


def _system_prompt(role: str) -> str:
    return _ACTOR_PROMPTS.get(role) or _ACTOR_PROMPTS["writer"]


async def _node_resolve_actor(state: _ChatState) -> dict[str, Any]:
    role = state.get("actor_role") or "writer"
    actor_did = f"{_DEFAULT_APP_DID}:actor:{role}"
    return {"actor_did": actor_did, "actor_role": role}


async def _node_llm_call(state: _ChatState) -> dict[str, Any]:
    user_text = (state.get("message") or "").strip()
    if not user_text:
        return {"error": "message required"}

    role = state.get("actor_role") or "writer"
    work_id = state.get("work_id") or ""
    chapter_id = state.get("chapter_id") or ""
    page_id = state.get("page_id") or ""

    system = _system_prompt(role)
    ctx_bits: list[str] = []
    if work_id: ctx_bits.append(f"work_id={work_id}")
    if chapter_id: ctx_bits.append(f"chapter_id={chapter_id}")
    if page_id: ctx_bits.append(f"page_id={page_id}")
    if ctx_bits:
        system += "\n\nContext: " + ", ".join(ctx_bits) + "."

    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for h in (state.get("history") or [])[-12:]:
        if isinstance(h, dict):
            r = h.get("role")
            c = h.get("content")
            if r in {"user", "assistant"} and isinstance(c, str) and c:
                messages.append({"role": r, "content": c[:2000]})
    messages.append({"role": "user", "content": user_text[:4000]})

    payload: dict[str, Any] = {
        "model": _VLLM_MODEL,
        "messages": messages,
        "max_tokens": int(state.get("max_tokens") or 512),
        "temperature": float(state.get("temperature") or 0.7),
    }

    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_VLLM_TIMEOUT) as client:
            r = await client.post(
                f"{_VLLM_URL}/chat/completions",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
        latency_ms = int((time.monotonic() - started) * 1000)
        if r.status_code >= 400:
            return {"error": f"vllm http {r.status_code}: {r.text[:200]}",
                    "latency_ms": latency_ms}
        resp = r.json()
    except httpx.TimeoutException:
        return {"error": f"vllm timeout after {_VLLM_TIMEOUT}s",
                "latency_ms": int((time.monotonic() - started) * 1000)}
    except Exception as exc:  # noqa: BLE001
        _log.exception("vllm call failed")
        return {"error": f"vllm: {type(exc).__name__}: {exc!s}"[:200],
                "latency_ms": int((time.monotonic() - started) * 1000)}

    choice = (resp.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    usage = resp.get("usage") or {}
    return {
        "reply": (msg.get("content") or "").strip(),
        "model": resp.get("model") or _VLLM_MODEL,
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
        "latency_ms": latency_ms,
    }


async def _node_emit_audit(state: _ChatState) -> dict[str, Any]:
    emit_audit_bg(
        actor=state.get("actor_did") or _DEFAULT_APP_DID,
        activity="mangaka.chat.reply",
        object_id=f"chat:{state.get('actor_role', 'writer')}:{int(time.time())}",
        object_type="mangaka.chat",
        attributes={
            "actorRole": state.get("actor_role"),
            "workId": state.get("work_id"),
            "userDid": state.get("user_did"),
            "totalTokens": state.get("total_tokens", 0),
            "latencyMs": state.get("latency_ms", 0),
            "model": state.get("model"),
            "ok": not bool(state.get("error")),
        },
    )
    return {}


def _build():
    g: StateGraph = StateGraph(_ChatState)
    g.add_node("resolve_actor", _node_resolve_actor)
    g.add_node("llm_call", _node_llm_call,
               retry_policy=RetryPolicy(max_attempts=3, backoff_factor=1.5))
    g.add_node("emit_audit", _node_emit_audit)
    g.add_edge(START, "resolve_actor")
    g.add_edge("resolve_actor", "llm_call")
    g.add_edge("llm_call", "emit_audit")
    g.add_edge("emit_audit", END)
    return g


GRAPH = _build().compile(name="agent_chat")
