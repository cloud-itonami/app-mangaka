"""mangaka `enrich_environments` — upsert kind=environment rows including
those not previously imported. Input contains BOTH the profile data AND a
flag to create-if-missing (since many env subfolders weren't imported).

Pregel-style 3-step:
  1. load_existing  — SELECT existing env rows for the supplied rkeys
  2. plan_writes    — diff existing vs profiles → list of upserts (new + merge)
  3. write_back     — delete-then-insert per row

Input:
    profiles dict[rkey, profileDict]
    dry_run  bool
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

_log = logging.getLogger(__name__)
_APP_DID = os.environ.get("MANGAKA_APP_DID", "did:web:mangaka.etzhayyim.com")
_DEFAULT_ORG_DID = os.environ.get("MANGAKA_DEFAULT_ORG_DID", "did:erc725:etzhayyim:260425:etzhayyim-japan")


class _State(TypedDict, total=False):
    profiles: dict
    dry_run:  bool
    existing: dict   # rkey -> {vid, props}
    plans:    list   # [{rkey, vid_or_none, new_props}]
    status:   str
    counts:   dict
    error:    str | None


async def _step_load_existing(state: _State) -> dict[str, Any]:
    profiles = state.get("profiles") or {}
    if not profiles: return {"existing": {}}
    rkeys = list(profiles.keys())
    out: dict[str, dict] = {}
    from kotodama.kotoba_datomic import get_kotoba_client
    import asyncio
    client = get_kotoba_client()
    for rkey in rkeys:
        rows = await asyncio.to_thread(client.select_where, "vertex_mangaka", "rkey", rkey, limit=100)
        row = next((r for r in rows if r.get("kind") == "environment"), None)
        if row:
            props_str = row.get("props")
            try: p = json.loads(props_str or "{}") if isinstance(props_str, str) else (props_str or {})
            except Exception: p = {}
            out[rkey] = {"vid": row.get("vertex_id"), "props": p}
    return {"existing": out}


async def _step_plan_writes(state: _State) -> dict[str, Any]:
    profiles = state.get("profiles") or {}
    existing = state.get("existing") or {}
    plans: list[dict[str, Any]] = []
    for rkey, prof in profiles.items():
        if rkey in existing:
            new_props = dict(existing[rkey]["props"] or {})
            new_props["profile"] = prof
            plans.append({"rkey": rkey, "vid": existing[rkey]["vid"], "new_props": new_props})
        else:
            plans.append({"rkey": rkey, "vid": None, "new_props": {"profile": prof, "name": prof.get("name") or rkey}})
    return {"plans": plans}


async def _step_write_back(state: _State) -> dict[str, Any]:
    plans = state.get("plans") or []
    if state.get("dry_run"):
        n_new = sum(1 for p in plans if p["vid"] is None)
        return {"status": "enriched", "counts": {"updated": len(plans) - n_new, "created": n_new}, "error": None}
    updated, created = 0, 0
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    now_date = now_iso[:10]
    from kotodama.kotoba_datomic import get_kotoba_client
    import asyncio
    client = get_kotoba_client()
    for plan in plans:
        if plan["vid"]:
            rows = await asyncio.to_thread(client.select_where, "vertex_mangaka", "vertex_id", plan["vid"], limit=1)
            if rows:
                row = rows[0]
                row["props"] = json.dumps(plan["new_props"], ensure_ascii=False)
                await asyncio.to_thread(client.insert_row, "vertex_mangaka", row)
                updated += 1
        else:
            rkey = plan["rkey"]
            name = (plan["new_props"].get("name") or rkey)
            vid = f"at://{_APP_DID}/com.etzhayyim.mangaka.environment/{rkey}"
            await asyncio.to_thread(client.insert_row, "vertex_mangaka", {
                "vertex_id": vid, "created_date": now_date, "sensitivity_ord": 0, "owner_did": _APP_DID,
                "rkey": rkey, "repo": _APP_DID, "did": _APP_DID, "collection": "com.etzhayyim.mangaka.environment",
                "label": "environment", "title": name, "name": name, "display_name": name,
                "kind": "environment", "status": "saved", "created_at": now_iso,
                "props": json.dumps(plan["new_props"], ensure_ascii=False),
                "actor_did": _APP_DID, "org_did": _DEFAULT_ORG_DID
            })
            created += 1
    return {"status": "enriched", "counts": {"updated": updated, "created": created}, "error": None}





def _build():
    g: StateGraph = StateGraph(_State)
    g.add_node("load_existing", _step_load_existing, retry_policy=RetryPolicy(max_attempts=2))
    g.add_node("plan_writes",   _step_plan_writes)
    g.add_node("write_back",    _step_write_back,    retry_policy=RetryPolicy(max_attempts=2))
    g.add_edge(START, "load_existing")
    g.add_edge("load_existing", "plan_writes")
    g.add_edge("plan_writes", "write_back")
    g.add_edge("write_back", END)
    return g


GRAPH = _build().compile(name="enrich_environments")
