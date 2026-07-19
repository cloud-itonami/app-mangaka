"""mangaka `backfill_mangaka_edges` — Pregel-style edge backfill from
vertex_mangaka.parent_rkey + props cross-refs into 9 edge_mangaka_* tables.

5 super-steps (each a LangGraph node):
  1. load_vertices       — SELECT every kind=work/chapter/page/panel/character/
                            environment/organization/incident/chatSession/chatMessage/
                            generatedImage with parent_rkey + props
  2. derive_hierarchy    — emit has_chapter / has_page / has_panel / has_message /
                            depicts (chapter→incident) / rendered_as (panel→genImage)
  3. derive_cross_refs   — parse panel.props.characters[] / environment,
                            organization.profile.gh:keyCharacters[] →
                            appears_in / set_in / member_of
  4. dedupe_edges        — collapse by deterministic edge_id
  5. write_edges         — delete-then-insert per edge_id into each table

Idempotent: edge_id = `<rel>:<src_rkey>:<dst_rkey>`, delete-then-insert.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

_log = logging.getLogger(__name__)
_APP_DID = os.environ.get("MANGAKA_APP_DID", "did:web:mangaka.etzhayyim.com")


class _State(TypedDict, total=False):
    dry_run: bool
    vertices_by_rkey: dict  # rkey -> {kind, vid, parent_rkey, props}
    edges_raw: list         # [{rel, src_rkey, dst_rkey}]
    edges_by_rel: dict      # rel -> list of unique (edge_id, src_vid, dst_vid)
    status: str
    counts: dict
    error: str | None


def _vid(coll: str, rkey: str) -> str:
    return f"at://{_APP_DID}/com.etzhayyim.mangaka.{coll}/{rkey}"


# Step 1: load all mangaka vertices we need
async def _step_load_vertices(state: _State) -> dict[str, Any]:
    kinds_needed = ('work', 'chapter', 'page', 'panel', 'character', 'environment',
                    'organization', 'incident', 'chatSession', 'chatMessage', 'generatedImage')
    vmap: dict[str, dict[str, Any]] = {}
    from kotodama.kotoba_datomic import get_kotoba_client
    import asyncio
    client = get_kotoba_client()
    # Datomic EDN query fallback to individual selects if kinds are many, but here we can just query all by kind and filter
    for k in kinds_needed:
        rows = await asyncio.to_thread(client.select_where, "vertex_mangaka", "kind", k, limit=100000)
        for r in rows:
            rkey = r.get("rkey", "")
            if not rkey.startswith("gh-"):
                continue
            vid = r.get("vertex_id")
            parent_rkey = r.get("parent_rkey")
            props_str = r.get("props")
            try: p = json.loads(props_str or "{}") if isinstance(props_str, str) else (props_str or {})
            except Exception: p = {}
            vmap[rkey] = {"kind": k, "vid": vid, "parent_rkey": parent_rkey, "props": p}
    _log.info("backfill_mangaka_edges: loaded %d vertices", len(vmap))
    return {"vertices_by_rkey": vmap}


# Step 2: hierarchy edges (parent_rkey) + chapter→incident + panel→generatedImage
async def _step_derive_hierarchy(state: _State) -> dict[str, Any]:
    vmap = state.get("vertices_by_rkey") or {}
    raw: list[dict[str, str]] = []
    for rkey, v in vmap.items():
        kind = v["kind"]
        parent = v.get("parent_rkey")
        if parent and parent in vmap:
            pkind = vmap[parent]["kind"]
            # Hierarchy mapping
            if pkind == "work" and kind == "chapter":
                raw.append({"rel": "has_chapter", "src_rkey": parent, "dst_rkey": rkey})
            elif pkind == "chapter" and kind == "page":
                raw.append({"rel": "has_page", "src_rkey": parent, "dst_rkey": rkey})
            elif pkind == "page" and kind == "panel":
                raw.append({"rel": "has_panel", "src_rkey": parent, "dst_rkey": rkey})
            elif pkind == "panel" and kind == "generatedImage":
                raw.append({"rel": "rendered_as", "src_rkey": parent, "dst_rkey": rkey})
            elif pkind == "chatSession" and kind == "chatMessage":
                raw.append({"rel": "has_message", "src_rkey": parent, "dst_rkey": rkey})
        # chapter→incident from chapter.props.incidents[]
        if kind == "chapter":
            for inc_rkey in (v["props"].get("incidents") or []):
                if inc_rkey in vmap:
                    raw.append({"rel": "depicts", "src_rkey": rkey, "dst_rkey": inc_rkey})
    return {"edges_raw": raw}


# Step 3: panel cross-refs + org→character membership
async def _step_derive_cross_refs(state: _State) -> dict[str, Any]:
    vmap = state.get("vertices_by_rkey") or {}
    raw = list(state.get("edges_raw") or [])
    for rkey, v in vmap.items():
        kind = v["kind"]
        if kind == "panel":
            for char_rkey in (v["props"].get("characters") or []):
                if char_rkey in vmap:
                    raw.append({"rel": "appears_in", "src_rkey": char_rkey, "dst_rkey": rkey})
            env_rkey = v["props"].get("environment")
            if env_rkey and env_rkey in vmap:
                raw.append({"rel": "set_in", "src_rkey": rkey, "dst_rkey": env_rkey})
        elif kind == "organization":
            prof = v["props"].get("profile") or {}
            for kc in (prof.get("gh:keyCharacters") or prof.get("keyCharacters") or []):
                # kc may be a string ID or a dict {@id: ...}
                cid = kc if isinstance(kc, str) else (kc.get("@id") if isinstance(kc, dict) else "")
                if not cid: continue
                # Convert character:Foo → gh-char-Foo
                ck = str(cid).replace("character:", "").replace(":", "-")
                # Try common variants since profile.@id != folder name in our import
                for candidate in (f"gh-char-{ck}",):
                    if candidate in vmap:
                        raw.append({"rel": "member_of", "src_rkey": candidate, "dst_rkey": rkey})
                        break
    return {"edges_raw": raw}


# Step 4: dedupe by deterministic edge_id; resolve vids
async def _step_dedupe_edges(state: _State) -> dict[str, Any]:
    vmap = state.get("vertices_by_rkey") or {}
    raw = state.get("edges_raw") or []
    by_rel: dict[str, dict[str, tuple[str, str, str]]] = defaultdict(dict)
    for e in raw:
        rel = e["rel"]
        src_rkey = e["src_rkey"]
        dst_rkey = e["dst_rkey"]
        if src_rkey not in vmap or dst_rkey not in vmap: continue
        eid = f"{rel}:{src_rkey}:{dst_rkey}"
        by_rel[rel][eid] = (eid, vmap[src_rkey]["vid"], vmap[dst_rkey]["vid"])
    out = {r: list(d.values()) for r, d in by_rel.items()}
    counts = {r: len(v) for r, v in out.items()}
    _log.info("derived edges: %s", counts)
    return {"edges_by_rel": out}


# Step 5: write edges (delete-then-insert per edge_id)
async def _step_write_edges(state: _State) -> dict[str, Any]:
    edges_by_rel = state.get("edges_by_rel") or {}
    if state.get("dry_run"):
        return {"status": "backfilled",
                "counts": {r: len(v) for r, v in edges_by_rel.items()},
                "error": None}
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    now_date = now_iso[:10]
    written: dict[str, int] = {}
    from kotodama.kotoba_datomic import get_kotoba_client
    import asyncio
    client = get_kotoba_client()
    for rel, edges in edges_by_rel.items():
        table = f"edge_mangaka_{rel}"
        n = 0
        for eid, src_vid, dst_vid in edges:
            await asyncio.to_thread(client.insert_row, table, {
                "edge_id": eid,
                "src_vid": src_vid,
                "dst_vid": dst_vid,
                "_seq": 0,
                "created_date": now_date,
                "sensitivity_ord": 0,
                "owner_did": _APP_DID
            })
            n += 1
        written[rel] = n
        _log.info("wrote %d edges into %s", n, table)
    return {"status": "backfilled", "counts": written, "error": None}


def _build():
    g: StateGraph = StateGraph(_State)
    g.add_node("load_vertices",     _step_load_vertices,     retry_policy=RetryPolicy(max_attempts=2))
    g.add_node("derive_hierarchy",  _step_derive_hierarchy)
    g.add_node("derive_cross_refs", _step_derive_cross_refs)
    g.add_node("dedupe_edges",      _step_dedupe_edges)
    g.add_node("write_edges",       _step_write_edges,       retry_policy=RetryPolicy(max_attempts=2))
    g.add_edge(START, "load_vertices")
    g.add_edge("load_vertices",     "derive_hierarchy")
    g.add_edge("derive_hierarchy",  "derive_cross_refs")
    g.add_edge("derive_cross_refs", "dedupe_edges")
    g.add_edge("dedupe_edges",      "write_edges")
    g.add_edge("write_edges",       END)
    return g


GRAPH = _build().compile(name="backfill_mangaka_edges")
