"""mangaka `debug_canvas_state` — Pregel-style 4-step diagnostic for the canvas
UX. Loads the live Genko document for a docId and analyses ai-image / panel
rect overlap, helping debug selection / resize behaviour.

Why: user reports "selection range looks like panel even though I selected the
ai-image". This typically means the ai-image's bounding rect (x1/y1/x2/y2)
still matches its parent panel's rect, so resize handles surround the same
area. This graph quantifies the overlap and surfaces mismatches.

Super-steps (each is a LangGraph node):

  1. load_doc          — SELECT props from vertex_mangaka kind='document' WHERE rkey=docId
  2. parse_nodes       — walk pages[].nodes[], group by type (panel / ai-image / fukidashi / text)
  3. compute_rect_diff — for each ai-image, find parent panel via _parent, compare rects
  4. summarise         — aggregate per-page + per-doc stats, emit verdict

Pregel idiom: each step writes its output channel; downstream consumes it.

Input:
    docId    str — required, e.g. "doc-gh-arc0-1-origin"
    pageIdx  int — optional, restrict to one page
    verbose  bool — include per-ai-image detail in output

Output:
    status         "ok" | "error"
    docId          echo
    pageCount      int
    nodeCounts     { panel, ai-image, fukidashi, text, total }
    rectAnalysis   {
                     identical_to_panel: int,   # ai-image rect == panel rect
                     smaller_than_panel: int,   # contained
                     larger_than_panel:  int,   # overflows
                     no_parent_panel:    int,   # orphan
                   }
    perPage        list[ { idx, name, nodes, identical, smaller, larger, orphan } ]
    samples        list[ { page, panel_nid, image_nid, image_rect, panel_rect, status } ]  # only if verbose
    verdict        str
    error          str | null
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

_log = logging.getLogger(__name__)

_APP_DID = os.environ.get("MANGAKA_APP_DID", "did:web:mangaka.etzhayyim.com")
_NSID = "com.etzhayyim.mangaka.document"


class _State(TypedDict, total=False):
    doc_id: str
    docId: str
    page_idx: int
    pageIdx: int
    verbose: bool
    # channels
    doc: dict
    nodes_by_page: list   # list[ { idx, name, nodes: list[node] } ]
    rect_diffs: list      # list[ { page, panel_nid, image_nid, status, image_rect, panel_rect } ]
    # output
    status: str
    pageCount: int
    nodeCounts: dict
    rectAnalysis: dict
    perPage: list
    samples: list
    verdict: str
    error: str | None


def _g(state: _State, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in state and state[k] is not None:
            return state[k]
    return default


# Super-step 1: load document body from RisingWave
async def _step_load_doc(state: _State) -> dict[str, Any]:
    
    doc_id = (_g(state, "doc_id", "docId", default="") or "").strip()
    if not doc_id:
        return {"status": "error", "error": "docId required"}

    vertex_id = f"at://{_APP_DID}/{_NSID}/{doc_id}"
    try:
        from kotodama.kotoba_datomic import get_kotoba_client
        import asyncio
        client = get_kotoba_client()
        rows = await asyncio.to_thread(
            client.select_where,
            "vertex_mangaka",
            "vertex_id",
            vertex_id,
            ["props", "kind"],
            limit=1
        )
        row = (rows[0].get("props") if rows and rows[0].get("kind") == "document" else None,) if rows else None
    except Exception as exc:  # noqa: BLE001
        _log.exception("load_doc failed (docId=%s)", doc_id)
        return {"status": "error", "error": f"{type(exc).__name__}: {exc!s}"[:300]}

    if not row:
        return {"status": "error", "error": f"document not found: {doc_id}"}

    try:
        doc = json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"json parse: {exc!s}"[:200]}
    return {"doc": doc, "docId": doc_id, "doc_id": doc_id}


# Super-step 2: walk pages → nodes, group by type
async def _step_parse_nodes(state: _State) -> dict[str, Any]:
    doc = state.get("doc") or {}
    pages = doc.get("pages") or []
    page_idx = _g(state, "page_idx", "pageIdx", default=None)
    result: list[dict[str, Any]] = []
    for i, p in enumerate(pages):
        if page_idx is not None and i != int(page_idx):
            continue
        nodes = []
        for n in (p.get("nodes") or []):
            data = n.get("data") if isinstance(n.get("data"), dict) else n
            nodes.append({
                "nid": data.get("_nid") or n.get("id") or "",
                "type": data.get("type") or n.get("type") or "",
                "parent": data.get("_parent") or "",
                "unit": data.get("_unit") or "",
                "x1": data.get("x1"), "y1": data.get("y1"),
                "x2": data.get("x2"), "y2": data.get("y2"),
                "imageX": data.get("_imageX"), "imageY": data.get("_imageY"),
                "imageScale": data.get("_imageScale"),
                "hasUrl": bool(data.get("_genImageUrl")),
            })
        result.append({"idx": i, "name": p.get("name") or "", "nodes": nodes})
    return {"nodes_by_page": result, "pageCount": len(pages)}


# Super-step 3: for each ai-image, find parent panel, compare rects
async def _step_compute_rect_diff(state: _State) -> dict[str, Any]:
    pages = state.get("nodes_by_page") or []
    diffs: list[dict[str, Any]] = []
    eps = 0.01
    for p in pages:
        by_nid = {n["nid"]: n for n in p["nodes"] if n["nid"]}
        for n in p["nodes"]:
            if n["type"] != "ai-image":
                continue
            parent = by_nid.get(n.get("parent") or "")
            if not parent or parent["type"] != "panel":
                diffs.append({
                    "page": p["idx"], "page_name": p["name"],
                    "image_nid": n["nid"], "panel_nid": None,
                    "image_rect": [n["x1"], n["y1"], n["x2"], n["y2"]],
                    "panel_rect": None,
                    "status": "no_parent_panel",
                })
                continue
            ax1, ay1, ax2, ay2 = n["x1"], n["y1"], n["x2"], n["y2"]
            px1, py1, px2, py2 = parent["x1"], parent["y1"], parent["x2"], parent["y2"]
            if None in (ax1, ay1, ax2, ay2, px1, py1, px2, py2):
                continue
            identical = (
                abs(ax1 - px1) < eps and abs(ay1 - py1) < eps
                and abs(ax2 - px2) < eps and abs(ay2 - py2) < eps
            )
            if identical:
                status = "identical_to_panel"
            else:
                # contained = all corners of ai-image inside panel
                contained = ax1 >= px1 - eps and ay1 >= py1 - eps and ax2 <= px2 + eps and ay2 <= py2 + eps
                status = "smaller_than_panel" if contained else "larger_than_panel"
            diffs.append({
                "page": p["idx"], "page_name": p["name"],
                "image_nid": n["nid"], "panel_nid": parent["nid"],
                "image_rect": [ax1, ay1, ax2, ay2],
                "panel_rect": [px1, py1, px2, py2],
                "status": status,
            })
    return {"rect_diffs": diffs}


# Super-step 4: aggregate + emit verdict
async def _step_summarise(state: _State) -> dict[str, Any]:
    diffs = state.get("rect_diffs") or []
    pages = state.get("nodes_by_page") or []
    verbose = bool(state.get("verbose"))
    total_nodes = sum(len(p["nodes"]) for p in pages)
    counts_by_type: dict[str, int] = {}
    for p in pages:
        for n in p["nodes"]:
            counts_by_type[n["type"]] = counts_by_type.get(n["type"], 0) + 1
    counts_by_type["total"] = total_nodes

    bucket_counts = {
        "identical_to_panel": 0, "smaller_than_panel": 0,
        "larger_than_panel": 0, "no_parent_panel": 0,
    }
    per_page: dict[int, dict[str, Any]] = {}
    for d in diffs:
        bucket_counts[d["status"]] = bucket_counts.get(d["status"], 0) + 1
        pg = per_page.setdefault(d["page"], {
            "idx": d["page"], "name": d.get("page_name") or "",
            "identical": 0, "smaller": 0, "larger": 0, "orphan": 0,
        })
        if d["status"] == "identical_to_panel": pg["identical"] += 1
        elif d["status"] == "smaller_than_panel": pg["smaller"] += 1
        elif d["status"] == "larger_than_panel": pg["larger"] += 1
        else: pg["orphan"] += 1
    per_page_list = sorted(per_page.values(), key=lambda x: x["idx"])
    for pg in per_page_list:
        pg["nodes"] = sum(1 for p in pages if p["idx"] == pg["idx"] for _ in p["nodes"])

    identical = bucket_counts["identical_to_panel"]
    total_ai = sum(bucket_counts.values())
    if total_ai == 0:
        verdict = "no ai-image nodes found"
    elif identical == total_ai:
        verdict = (
            f"all {total_ai} ai-images share rect with parent panel. "
            "selecting inside the shared area picks the ai-image (priority), "
            "but handles will overlap the panel boundary — they look identical "
            "until the ai-image rect is resized to differ from panel."
        )
    elif identical > 0:
        verdict = (
            f"{identical}/{total_ai} ai-images still share rect with parent panel; "
            f"{bucket_counts['smaller_than_panel']} have been shrunk inside the panel, "
            f"{bucket_counts['larger_than_panel']} overflow."
        )
    else:
        verdict = (
            f"no ai-images share panel rect. all {total_ai} have been adjusted "
            "(smaller / larger / orphan)."
        )

    samples = []
    if verbose:
        samples = diffs[:20]

    return {
        "status": "ok",
        "pageCount": state.get("pageCount") or len(pages),
        "nodeCounts": counts_by_type,
        "rectAnalysis": bucket_counts,
        "perPage": per_page_list,
        "samples": samples,
        "verdict": verdict,
        "error": None,
    }


def _build():
    g: StateGraph = StateGraph(_State)
    g.add_node("load_doc", _step_load_doc, retry_policy=RetryPolicy(max_attempts=2))
    g.add_node("parse_nodes", _step_parse_nodes)
    g.add_node("compute_rect_diff", _step_compute_rect_diff)
    g.add_node("summarise", _step_summarise)
    g.add_edge(START, "load_doc")
    g.add_edge("load_doc", "parse_nodes")
    g.add_edge("parse_nodes", "compute_rect_diff")
    g.add_edge("compute_rect_diff", "summarise")
    g.add_edge("summarise", END)
    return g


GRAPH = _build().compile(name="debug_canvas_state")
