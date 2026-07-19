"""mangaka `derive_chapter_incidents` — create kind=incident rows from
incidents.jsonld grouping and inject cross-refs into chapter rows' props.

incidents.jsonld structure:
    { gh:episodes: [
        { gh:arc, gh:episodes:[1,6,10], gh:episodeIds:[episode:...,...],
          gh:industry, gh:mainCharacter, gh:supportingCharacters:[],
          gh:description }, ... ] }

Pregel-style 4-step:
  1. parse_incidents       — input.incidents → list of grouped incidents
  2. create_incident_rows  — INSERT/upsert kind=incident rows (parent=work)
  3. link_chapters         — for each chapter rkey in incident.episodeIds,
                             merge { incidentRkey: <list> } into chapter.props
  4. write_back            — apply all upserts

Input:
    incidents list   — gh:episodes array verbatim
    work_rkey  str   — parent work rkey (default "gh-work-ghost-hacker")
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
    incidents:  list
    work_rkey:  str
    dry_run:    bool
    parsed:     list   # [{rkey, data, episodeRkeys}]
    chap_refs:  dict   # chapter_rkey -> [incident_rkey, ...]
    status:     str
    counts:     dict
    error:      str | None


def _slug(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "x"


async def _step_parse_incidents(state: _State) -> dict[str, Any]:
    incs = state.get("incidents") or []
    parsed = []
    for i, it in enumerate(incs):
        arc = it.get("gh:arc") or it.get("arc") or ""
        # rkey: gh-inc-{arc-slug}-{index}
        rkey = f"gh-inc-{_slug(arc)}-{i+1}"
        ep_ids = it.get("gh:episodeIds") or it.get("episodeIds") or []
        ep_rkeys = [f"gh-chap-{e}" for e in ep_ids]
        parsed.append({"rkey": rkey, "data": it, "episodeRkeys": ep_rkeys})
    return {"parsed": parsed}


async def _step_link_chapters(state: _State) -> dict[str, Any]:
    chap_refs: dict[str, list[str]] = {}
    for inc in state.get("parsed") or []:
        for cr in inc["episodeRkeys"]:
            chap_refs.setdefault(cr, []).append(inc["rkey"])
    return {"chap_refs": chap_refs}


async def _step_write_back(state: _State) -> dict[str, Any]:
    parsed = state.get("parsed") or []
    chap_refs = state.get("chap_refs") or {}
    if state.get("dry_run"):
        return {"status": "derived", "counts": {
            "incidents": len(parsed),
            "chapters_linked": len(chap_refs),
        }, "error": None}

    import asyncio
    from kotodama.kotoba_datomic import get_kotoba_client
    work_rkey = state.get("work_rkey") or "gh-work-ghost-hacker"
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    now_date = now_iso[:10]
    
    def _write():
        inc_count, chap_count = 0, 0
        client = get_kotoba_client()
        # 1. Upsert incident rows
        for inc in parsed:
            rkey = inc["rkey"]
            data = inc["data"]
            name = data.get("gh:description") or rkey
            title = (data.get("gh:arc") or "") + " — " + (data.get("gh:industry") or "")
            vid = f"at://{_APP_DID}/com.etzhayyim.mangaka.incident/{rkey}"
            props = {
                "arc": data.get("gh:arc") or "",
                "industry": data.get("gh:industry") or "",
                "mainCharacter": data.get("gh:mainCharacter") or "",
                "supportingCharacters": data.get("gh:supportingCharacters") or [],
                "description": data.get("gh:description") or "",
                "episodes": data.get("gh:episodes") or [],
                "episodeIds": data.get("gh:episodeIds") or [],
                "episodeRkeys": inc["episodeRkeys"],
            }
            client.insert_row("vertex_mangaka", {
                "vertex_id": vid,
                "created_date": now_date,
                "sensitivity_ord": 0,
                "owner_did": _APP_DID,
                "rkey": rkey,
                "repo": _APP_DID,
                "did": _APP_DID,
                "collection": "com.etzhayyim.mangaka.incident",
                "label": "incident",
                "title": title.strip(" —"),
                "name": name,
                "display_name": name,
                "kind": "incident",
                "status": "saved",
                "created_at": now_iso,
                "props": json.dumps(props, ensure_ascii=False),
                "parent_rkey": work_rkey,
                "actor_did": _APP_DID,
                "org_did": _DEFAULT_ORG_DID
            })
            inc_count += 1

        # 2. Merge chap_refs into existing chapter rows' props.incidents
        for chap_rkey, inc_rkeys in chap_refs.items():
            res = client.select_where("vertex_mangaka", "rkey", chap_rkey, columns=["vertex_id", "props", "kind"])
            row = None
            for r in res:
                if r.get("kind") == "chapter":
                    row = r
                    break
            if not row: continue
            vid = row.get("vertex_id")
            props_str = row.get("props")
            try: p = json.loads(props_str or "{}")
            except Exception: p = {}
            p["incidents"] = inc_rkeys
            
            # Reinsert to update props
            full_row = client.select_where("vertex_mangaka", "vertex_id", vid)
            if full_row:
                updated_row = dict(full_row[0])
                updated_row["props"] = json.dumps(p, ensure_ascii=False)
                client.insert_row("vertex_mangaka", updated_row)
                chap_count += 1
        return inc_count, chap_count
        
    inc_count, chap_count = await asyncio.to_thread(_write)
    return {"status": "derived", "counts": {"incidents": inc_count, "chapters_linked": chap_count}, "error": None}


def _build():
    g: StateGraph = StateGraph(_State)
    g.add_node("parse_incidents", _step_parse_incidents)
    g.add_node("link_chapters",   _step_link_chapters)
    g.add_node("write_back",      _step_write_back, retry_policy=RetryPolicy(max_attempts=2))
    g.add_edge(START, "parse_incidents")
    g.add_edge("parse_incidents", "link_chapters")
    g.add_edge("link_chapters", "write_back")
    g.add_edge("write_back", END)
    return g


GRAPH = _build().compile(name="derive_chapter_incidents")
