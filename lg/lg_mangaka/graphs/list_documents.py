"""mangaka `list_documents` graph — listing for com.etzhayyim.mangaka.listDocuments.

Returns the catalog of mangaka documents stored in vertex_mangaka.
Optionally filter by convoId. Always returns offset/limit/total per
60-apps/CLAUDE.md pagination convention.

Input:
    convoId  str (optional)
    limit    int (default 50, max 200)
    offset   int (default 0)
Output:
    items    list[{ docId, name, vertexId, createdAt }]
    total    int
    offset   int
    limit    int
"""

from __future__ import annotations

import logging
import os
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

_log = logging.getLogger(__name__)

_APP_DID = os.environ.get("MANGAKA_APP_DID", "did:web:mangaka.etzhayyim.com")
_NSID = "com.etzhayyim.mangaka.document"


class _ListState(TypedDict, total=False):
    convo_id: str
    convoId: str
    limit: int
    offset: int
    items: list[dict[str, Any]]
    total: int
    error: str | None


async def _node_list(state: _ListState) -> dict[str, Any]:
    limit = max(1, min(200, int(state.get("limit") or 50)))
    offset = max(0, int(state.get("offset") or 0))
    where = ["kind = 'document'", "collection = %s"]
    params: list[Any] = [_NSID]

    convo_id = (state.get("convo_id") or state.get("convoId") or "").strip()
    if convo_id:
        where.append("props LIKE %s")
        params.append(f'%"convoId":"{convo_id}"%')

    where_sql = " AND ".join(where)
    try:
        import asyncio
        from kotodama.kotoba_datomic import get_kotoba_client
        def _fetch():
            client = get_kotoba_client()
            res = client.select_where("vertex_mangaka", "kind", "document", columns=["rkey", "name", "vertex_id", "created_at", "props"])
            if convo_id:
                res = [r for r in res if r.get("props") and f'"convoId":"{convo_id}"' in r.get("props")]
            
            total = len(res)
            res.sort(key=lambda x: x.get("rkey") or "")
            res.sort(key=lambda x: x.get("created_at") or "", reverse=True)
            
            page = res[offset:offset+limit]
            return total, [[r.get("rkey"), r.get("name"), r.get("vertex_id"), r.get("created_at")] for r in page]
            
        total, rows = await asyncio.to_thread(_fetch)
    except Exception as exc:  # noqa: BLE001
        _log.exception("list_documents query failed")
        return {"items": [], "total": 0, "error": f"{type(exc).__name__}: {exc!s}"[:300]}

    items = [
        {
            "docId": r[0],
            "name": r[1] or r[0],
            "vertexId": r[2],
            "createdAt": r[3] or "",
        }
        for r in rows
    ]
    return {"items": items, "total": total, "offset": offset, "limit": limit, "error": None}


def _build():
    g: StateGraph = StateGraph(_ListState)
    g.add_node("list", _node_list)
    g.add_edge(START, "list")
    g.add_edge("list", END)
    return g


GRAPH = _build().compile(name="list_documents")
