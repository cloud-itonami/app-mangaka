"""mangaka `enrich_characters` — merge per-character profile metadata
(gh:appearance / age / role / slug) into existing kind=character rows.

Pregel-style 3-step:
  1. load_targets   — SELECT kind=character rows that need enrichment
  2. merge_profiles — per-entity merge of input profiles into row.props
  3. write_back     — delete-then-insert per row with enriched props

Input (snake_case after server.py shim):
    profiles dict[rkey, profileDict]   — e.g. { "gh-char-Yuto": { name, schema_age, gh_appearance, ... } }
    dry_run  bool                       — skip write
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

_log = logging.getLogger(__name__)


class _State(TypedDict, total=False):
    profiles: dict
    dry_run: bool
    targets: list      # [{rkey, vid, props}]
    merged:  list      # [{vid, new_props}]
    status:  str
    counts:  dict
    error:   str | None


async def _step_load_targets(state: _State) -> dict[str, Any]:
    profiles = state.get("profiles") or {}
    if not profiles: return {"targets": []}
    rkeys = list(profiles.keys())
    rows = []
    from kotodama.kotoba_datomic import get_kotoba_client
    import asyncio
    client = get_kotoba_client()
    for rkey in rkeys:
        rs = await asyncio.to_thread(client.select_where, "vertex_mangaka", "rkey", rkey, limit=100)
        row = next((r for r in rs if r.get("kind") == "character"), None)
        if row:
            props_str = row.get("props")
            try: p = json.loads(props_str or "{}") if isinstance(props_str, str) else (props_str or {})
            except Exception: p = {}
            rows.append({"vid": row.get("vertex_id"), "rkey": rkey, "props": p})
    return {"targets": rows}


async def _step_merge_profiles(state: _State) -> dict[str, Any]:
    profiles = state.get("profiles") or {}
    targets = state.get("targets") or []
    merged = []
    for t in targets:
        prof = profiles.get(t["rkey"]) or {}
        new_props = dict(t["props"] or {})
        new_props["profile"] = prof
        merged.append({"vid": t["vid"], "new_props": new_props})
    return {"merged": merged}


async def _step_write_back(state: _State) -> dict[str, Any]:
    merged = state.get("merged") or []
    if state.get("dry_run"):
        return {"status": "enriched", "counts": {"updated": len(merged)}, "error": None}
    written = 0
    from kotodama.kotoba_datomic import get_kotoba_client
    import asyncio
    client = get_kotoba_client()
    for m in merged:
        rows = await asyncio.to_thread(client.select_where, "vertex_mangaka", "vertex_id", m["vid"], limit=1)
        if rows:
            row = rows[0]
            row["props"] = json.dumps(m["new_props"], ensure_ascii=False)
            await asyncio.to_thread(client.insert_row, "vertex_mangaka", row)
            written += 1
    return {"status": "enriched", "counts": {"updated": written}, "error": None}





def _build():
    g: StateGraph = StateGraph(_State)
    g.add_node("load_targets",   _step_load_targets,   retry_policy=RetryPolicy(max_attempts=2))
    g.add_node("merge_profiles", _step_merge_profiles)
    g.add_node("write_back",     _step_write_back,     retry_policy=RetryPolicy(max_attempts=2))
    g.add_edge(START, "load_targets")
    g.add_edge("load_targets", "merge_profiles")
    g.add_edge("merge_profiles", "write_back")
    g.add_edge("write_back", END)
    return g


GRAPH = _build().compile(name="enrich_characters")
