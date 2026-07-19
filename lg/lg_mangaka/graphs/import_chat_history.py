"""mangaka `import_chat_history` — import authoring chat sessions from
chat_history/*.jsonld into kind=chatSession + kind=chatMessage vertices.

Each session jsonld has shape:
    { @id, dct:title, gh:slug, schema:dateCreated, gh:messages:[
        { content, role?, context_json?, timestamp? }, ...
    ] }

We create:
  - 1 row kind=chatSession per session, parent_rkey=work
  - N rows kind=chatMessage per session, parent_rkey=sessionRkey, page_number=index

Pregel 3-step:
  1. parse_sessions — input.sessions → flat list of session + messages
  2. plan_writes    — assign rkeys / build upsert list
  3. write_back     — INSERT per row

Input:
    sessions   list — [{ sessionId, title, slug, createdAt, messages:[...] }, ...]
    work_rkey  str
    dry_run    bool
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

_log = logging.getLogger(__name__)
_APP_DID = os.environ.get("MANGAKA_APP_DID", "did:web:mangaka.etzhayyim.com")
_DEFAULT_ORG_DID = os.environ.get("MANGAKA_DEFAULT_ORG_DID", "did:erc725:etzhayyim:260425:etzhayyim-japan")


class _State(TypedDict, total=False):
    sessions:  list
    work_rkey: str
    dry_run:   bool
    plans:     list   # [{kind, rkey, parent, props, ...}]
    status:    str
    counts:    dict
    error:     str | None


def _slug(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "x"


async def _step_parse_sessions(state: _State) -> dict[str, Any]:
    plans: list[dict[str, Any]] = []
    for s in state.get("sessions") or []:
        sid = s.get("sessionId") or s.get("@id") or _slug(s.get("title") or "")
        # Strip 'chat:' prefix and any special chars
        sid_clean = re.sub(r"[^a-zA-Z0-9_-]", "", sid.replace("chat:", ""))
        sess_rkey = f"gh-chat-{sid_clean[:24]}"
        title = s.get("title") or sid_clean
        slug = s.get("slug") or _slug(title)
        msgs = s.get("messages") or []
        plans.append({"kind": "chatSession", "rkey": sess_rkey, "parent": None,
                      "title": title, "name": title, "props": {
                          "slug": slug,
                          "createdAt": s.get("createdAt") or "",
                          "messageCount": len(msgs),
                      }})
        for i, m in enumerate(msgs):
            msg_rkey = f"{sess_rkey}-m{i+1}"
            content = (m.get("content") or "")[:8000]  # cap to avoid huge rows
            role = m.get("role") or ""
            plans.append({"kind": "chatMessage", "rkey": msg_rkey, "parent": sess_rkey,
                          "title": f"msg {i+1}", "name": f"msg {i+1}",
                          "page_number": i + 1,
                          "props": {
                              "role": role,
                              "content": content,
                              "context": m.get("context_json") or m.get("context") or "",
                              "timestamp": m.get("timestamp") or "",
                          }})
    return {"plans": plans}


async def _step_plan_writes(state: _State) -> dict[str, Any]:
    # No-op transformation; could deduplicate here.
    return {}


async def _step_write_back(state: _State) -> dict[str, Any]:
    plans = state.get("plans") or []
    if state.get("dry_run"):
        sess_n = sum(1 for p in plans if p["kind"] == "chatSession")
        msg_n = sum(1 for p in plans if p["kind"] == "chatMessage")
        return {"status": "imported", "counts": {"sessions": sess_n, "messages": msg_n}, "error": None}
    
    work_rkey = state.get("work_rkey") or "gh-work-ghost-hacker"
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    now_date = now_iso[:10]
    sess_count, msg_count = 0, 0
    from kotodama.kotoba_datomic import get_kotoba_client
    import asyncio
    client = get_kotoba_client()
    for plan in plans:
        kind = plan["kind"]
        rkey = plan["rkey"]
        parent = plan.get("parent") or (work_rkey if kind == "chatSession" else None)
        vid = f"at://{_APP_DID}/com.etzhayyim.mangaka.{kind}/{rkey}"
        await asyncio.to_thread(client.insert_row, "vertex_mangaka", {
            "vertex_id": vid,
            "created_date": now_date,
            "sensitivity_ord": 0,
            "owner_did": _APP_DID,
            "rkey": rkey,
            "repo": _APP_DID,
            "did": _APP_DID,
            "collection": f"com.etzhayyim.mangaka.{kind}",
            "label": kind,
            "title": plan.get("title"),
            "name": plan.get("name"),
            "display_name": plan.get("name"),
            "kind": kind,
            "status": "saved",
            "created_at": now_iso,
            "props": json.dumps(plan.get("props") or {}, ensure_ascii=False),
            "parent_rkey": parent,
            "page_number": plan.get("page_number"),
            "actor_did": _APP_DID,
            "org_did": _DEFAULT_ORG_DID
        })
        if kind == "chatSession": sess_count += 1
        else: msg_count += 1
    return {"status": "imported", "counts": {"sessions": sess_count, "messages": msg_count}, "error": None}


def _build():
    g: StateGraph = StateGraph(_State)
    g.add_node("parse_sessions", _step_parse_sessions)
    g.add_node("plan_writes",    _step_plan_writes)
    g.add_node("write_back",     _step_write_back, retry_policy=RetryPolicy(max_attempts=2))
    g.add_edge(START, "parse_sessions")
    g.add_edge("parse_sessions", "plan_writes")
    g.add_edge("plan_writes", "write_back")
    g.add_edge("write_back", END)
    return g


GRAPH = _build().compile(name="import_chat_history")
