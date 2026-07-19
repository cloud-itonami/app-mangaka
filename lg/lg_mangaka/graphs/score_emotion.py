"""mangaka `score_emotion` — Pregel-style 4-step emotion scoring for
ai-image + panel nodes.

Mirrors the `detect_faces` 4-step shape. Tags every ai-image with a
Hume image-head emotion record, then rolls a max-saliency aggregate up
to each panel that contains those images so the Genko side-panel can
display "this panel reads as joy 0.82" without re-scanning child nodes
client-side.

Super-steps:

  1. load_target    — SELECT props from vertex_mangaka kind='document'
                       → resolve target ai-image nodes (single or batch)
  2. fetch_and_score — GET blob bytes per ai-image, run
                       `kotodama.primitives.hume_image_head.predict_image_emotion`
                       with the optional distilled student model
                       (`MANGAKA_HUME_STUDENT_MODEL` env path, JSON);
                       falls back to the stdlib `visual_heuristic_v1`
                       when the model isn't loadable.
  3. aggregate      — for each panel node that contains scored ai-images,
                       compute a panel-level `_emotion` aggregate via
                       max-saliency primary (the strongest child emotion
                       wins) — this lets the panel show one chip without
                       averaging away the dominant beat.
  4. persist        — patch each ai-image's `_emotion` + each panel's
                       `_emotion` into the doc JSON, re-save via the same
                       DELETE-then-INSERT path as `save_document`.

Input:
    docId        str  (required)
    imageNid     str  (optional — if omitted, processes all ai-images
                       in the doc; panel aggregates are recomputed for
                       every panel that contains a scored child)

Output:
    status            "ok" | "error"
    docId             echo
    imageNid          echo (single mode) or null (batch)
    emotion           the scored record (single mode)
    perImage          { imageNid: emotionRecord }  (batch)
    panelEmotion      { panelNid: emotionRecord }  (batch — rolled up)
    method            "centroid" | "heuristic"
    latencyMs         int
    error             str | null

Emotion record shape (mirrors hume_image_head.predict_image_emotion output
with three extra fields for graph-side bookkeeping):

    {
      "primary":      {"name": str, "score": float},
      "topEmotions":  [{"name": str, "score": float}, ...],
      "imageFeatures": {...6 floats...},
      "algorithm":    "visual_heuristic_v1" | "visual_centroid_v1",
      "scoredAt":     "2026-05-14T..." (UTC ISO 8601),
      "sourceCount":  int  (1 for ai-image; #scored children for panel),
    }
"""

from __future__ import annotations

import io
import json
import logging
import os
import time
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

_log = logging.getLogger(__name__)

_APP_DID = os.environ.get("MANGAKA_APP_DID", "did:web:mangaka.etzhayyim.com")
_NSID = "com.etzhayyim.mangaka.document"
_RW_URL = os.environ.get("RW_URL", "")
_BLOB_BASE = os.environ.get("MANGAKA_BLOB_BASE", "https://mangaka.etzhayyim.com")
_STUDENT_MODEL_PATH = os.environ.get("MANGAKA_HUME_STUDENT_MODEL", "")


class _State(TypedDict, total=False):
    doc_id: str
    docId: str
    image_nid: str
    imageNid: str
    doc: dict
    targets: list                 # [{nid, url, parent_panel_nid}]
    emotions: dict                # { nid: emotionRecord }
    panel_emotions: dict          # { panel_nid: emotionRecord }
    method: str
    status: str
    error: str
    docId_out: str
    imageNid_out: str | None
    emotion: dict
    perImage: dict
    panelEmotion: dict
    latencyMs: int
    _t0: float


# ── helpers ──────────────────────────────────────────────────────────────


def _g(state: _State, *keys: str, default: Any = None) -> Any:
    for k in keys:
        v = state.get(k)
        if v not in (None, ""):
            return v
    return default


def _extract_cid(url: str) -> str:
    """`https://.../blob/{cid}?did=anon` → `cid`. Same parser detect_faces uses."""
    if not url:
        return ""
    pos = url.find("/blob/")
    if pos < 0:
        return ""
    tail = url[pos + len("/blob/"):]
    return tail.split("?", 1)[0].split("&", 1)[0]


def _load_student_model() -> dict | None:
    """Load the distilled centroid JSON (if `MANGAKA_HUME_STUDENT_MODEL` is
    set) once per pod boot. Returns None when unset / unreadable so callers
    fall back to the stdlib heuristic."""
    if not _STUDENT_MODEL_PATH:
        return None
    try:
        with open(_STUDENT_MODEL_PATH, "rb") as fh:
            blob = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        _log.warning("student model %s unreadable: %s", _STUDENT_MODEL_PATH, exc)
        return None
    # Tolerate the `{model, metrics}` wrapper emitted by run_distillation
    # with `--include-metrics` so a maintainer can paste either form.
    if isinstance(blob, dict) and "emotionCentroids" in blob:
        return blob
    if isinstance(blob, dict) and isinstance(blob.get("model"), dict):
        return blob["model"]
    return None


_STUDENT_MODEL: dict | None = _load_student_model()


# ── super-step 1: load_target ────────────────────────────────────────────


async def _step_load_target(state: _State) -> dict[str, Any]:
    state_t0 = time.monotonic()
    if not _RW_URL:
        return {"status": "error", "error": "RW_URL not configured", "_t0": state_t0}
    doc_id = (_g(state, "doc_id", "docId", default="") or "").strip()
    if not doc_id:
        return {"status": "error", "error": "docId required", "_t0": state_t0}
    image_nid = (_g(state, "image_nid", "imageNid", default="") or "").strip() or None

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
        row = rows[0] if rows else None
        
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"{type(exc).__name__}: {exc!s}"[:300], "_t0": state_t0}
    if not row or row.get("kind") != "document":
        return {"status": "error", "error": f"document not found: {doc_id}", "_t0": state_t0}
    try:
        doc_props = row.get("props")
        doc = json.loads(doc_props) if isinstance(doc_props, str) else (doc_props or {})
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"json parse: {exc!s}"[:200], "_t0": state_t0}

    targets: list[dict[str, Any]] = []
    for p in (doc.get("pages") or []):
        # Per-page, build (panel_nid → set of ai-image nids it contains).
        # Genko's data model has panels carry a `_panelChildren` list of
        # ai-image nids; if absent (legacy doc) we fall back to "any
        # ai-image on the same page belongs to no specific panel" — the
        # panel aggregate just covers all ai-images on the page.
        ai_to_panel: dict[str, str | None] = {}
        for n in (p.get("nodes") or []):
            data = n.get("data") if isinstance(n.get("data"), dict) else n
            nid = data.get("_nid") or n.get("id") or ""
            if (data.get("type") or n.get("type")) == "panel":
                kids = data.get("_panelChildren") or []
                for k in kids:
                    ai_to_panel[k] = nid
        for n in (p.get("nodes") or []):
            data = n.get("data") if isinstance(n.get("data"), dict) else n
            if (data.get("type") or n.get("type")) != "ai-image":
                continue
            nid = data.get("_nid") or n.get("id") or ""
            if image_nid and nid != image_nid:
                continue
            url = data.get("_genImageUrl") or ""
            if not url:
                continue
            cid = _extract_cid(url)
            if not cid:
                continue
            targets.append({
                "nid": nid, "cid": cid,
                "url": f"{_BLOB_BASE}/blob/{cid}?did=anonymous",
                "parent_panel_nid": ai_to_panel.get(nid),
            })
            if image_nid:
                break
        if image_nid and targets:
            break
    return {"doc": doc, "targets": targets, "_t0": state_t0}


# ── super-step 2: fetch_and_score ────────────────────────────────────────


async def _step_fetch_and_score(state: _State) -> dict[str, Any]:
    if state.get("status") == "error":
        return {}
    targets = state.get("targets") or []
    emotions: dict[str, Any] = {}
    method = "centroid" if _STUDENT_MODEL else "heuristic"
    if not targets:
        return {"emotions": {}, "method": method}

    try:
        import httpx  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"deps missing: {exc!s}"[:200]}

    from kotodama.primitives.hume_image_head import predict_image_emotion

    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    async with httpx.AsyncClient(timeout=30.0) as client:
        for t in targets:
            try:
                r = await client.get(t["url"])
                if r.status_code != 200:
                    _log.warning("blob fetch %s: %d", t["nid"], r.status_code)
                    continue
                result = predict_image_emotion(
                    r.content, "image/png", model=_STUDENT_MODEL,
                )
            except Exception as exc:  # noqa: BLE001
                _log.warning("score %s failed: %s", t["nid"], exc)
                continue
            emotions[t["nid"]] = {
                "primary": result.get("primary") or {},
                "topEmotions": result.get("topEmotions") or [],
                "imageFeatures": ((result.get("evidence") or {}).get("imageFeatures") or {}),
                "algorithm": (result.get("teacher") or {}).get("algorithm") or "visual_heuristic_v1",
                "scoredAt": now_iso,
                "sourceCount": 1,
            }
    return {"emotions": emotions, "method": method}


# ── super-step 3: aggregate ──────────────────────────────────────────────


def _step_aggregate(state: _State) -> dict[str, Any]:
    """Roll per-image emotion records up to per-panel records using
    max-saliency primary: among the scored children of a panel, the
    record with the highest `primary.score` wins. Ties broken by score
    sum across topEmotions (so a panel with two strong joy children
    beats a panel with one slightly-stronger joy + a tepid sadness)."""
    if state.get("status") == "error":
        return {}
    emotions: dict[str, Any] = state.get("emotions") or {}
    targets = state.get("targets") or []
    if not emotions:
        return {"panel_emotions": {}}

    # panel_nid → list of (child_nid, emotion)
    by_panel: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for t in targets:
        nid = t["nid"]
        emo = emotions.get(nid)
        if not emo:
            continue
        panel = t.get("parent_panel_nid")
        if not panel:
            continue
        by_panel.setdefault(panel, []).append((nid, emo))

    panel_emotions: dict[str, Any] = {}
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for panel, children in by_panel.items():
        def _key(item: tuple[str, dict[str, Any]]) -> tuple[float, float]:
            _, emo = item
            primary_score = float((emo.get("primary") or {}).get("score") or 0.0)
            top_sum = sum(float(e.get("score") or 0.0) for e in (emo.get("topEmotions") or []))
            return (primary_score, top_sum)
        children.sort(key=_key, reverse=True)
        winner_nid, winner = children[0]
        panel_emotions[panel] = {
            "primary": dict(winner.get("primary") or {}),
            "topEmotions": [dict(e) for e in (winner.get("topEmotions") or [])],
            "algorithm": winner.get("algorithm"),
            "scoredAt": now_iso,
            "sourceCount": len(children),
            "winningChild": winner_nid,
        }
    return {"panel_emotions": panel_emotions}


# ── super-step 4: persist ────────────────────────────────────────────────


async def _step_persist(state: _State) -> dict[str, Any]:
    if state.get("status") == "error":
        return {
            "status": "error", "error": state.get("error"),
            "latencyMs": int((time.monotonic() - (state.get("_t0") or time.monotonic())) * 1000),
        }
    doc = state.get("doc") or {}
    emotions: dict[str, Any] = state.get("emotions") or {}
    panel_emotions: dict[str, Any] = state.get("panel_emotions") or {}
    doc_id = (_g(state, "doc_id", "docId", default="") or "").strip()
    image_nid = (_g(state, "image_nid", "imageNid", default="") or "").strip() or None

    # Patch ai-image + panel nodes in place.
    for p in (doc.get("pages") or []):
        for n in (p.get("nodes") or []):
            data = n.get("data") if isinstance(n.get("data"), dict) else n
            nid = data.get("_nid") or n.get("id") or ""
            node_type = data.get("type") or n.get("type")
            if node_type == "ai-image" and nid in emotions:
                data["_emotion"] = emotions[nid]
            elif node_type == "panel" and nid in panel_emotions:
                data["_emotion"] = panel_emotions[nid]

    name = doc.get("name") or doc_id
    document_json = json.dumps(doc, ensure_ascii=False)
    vertex_id = f"at://{_APP_DID}/{_NSID}/{doc_id}"
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    now_date = now_iso[:10]
    try:
        from kotodama.kotoba_datomic import get_kotoba_client
        import asyncio
        client = get_kotoba_client()
        await asyncio.to_thread(client.insert_row, "vertex_mangaka", {
            "vertex_id": vertex_id,
            "created_date": now_date,
            "sensitivity_ord": 0,
            "owner_did": _APP_DID,
            "rkey": doc_id,
            "repo": _APP_DID,
            "did": _APP_DID,
            "collection": _NSID,
            "label": "document",
            "title": name,
            "name": name,
            "display_name": name,
            "kind": "document",
            "status": "saved",
            "created_at": now_iso,
            "props": document_json,
            "actor_did": _APP_DID,
            "org_did": "did:erc725:etzhayyim:260425:etzhayyim-japan"
        })
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "error": f"persist: {type(exc).__name__}: {exc!s}"[:300],
            "latencyMs": int((time.monotonic() - (state.get("_t0") or time.monotonic())) * 1000),
        }

    if image_nid:
        return {
            "status": "ok",
            "docId_out": doc_id, "imageNid_out": image_nid,
            "emotion": emotions.get(image_nid),
            "method": state.get("method") or "heuristic",
            "latencyMs": int((time.monotonic() - (state.get("_t0") or time.monotonic())) * 1000),
            "error": None,
        }
    return {
        "status": "ok",
        "docId_out": doc_id, "imageNid_out": None,
        "perImage": emotions,
        "panelEmotion": panel_emotions,
        "method": state.get("method") or "heuristic",
        "latencyMs": int((time.monotonic() - (state.get("_t0") or time.monotonic())) * 1000),
        "error": None,
    }


# ── build ────────────────────────────────────────────────────────────────


def _build():
    g: StateGraph = StateGraph(_State)
    g.add_node("load_target", _step_load_target, retry_policy=RetryPolicy(max_attempts=2))
    g.add_node("fetch_and_score", _step_fetch_and_score)
    g.add_node("aggregate", _step_aggregate)
    g.add_node("persist", _step_persist)
    g.add_edge(START, "load_target")
    g.add_edge("load_target", "fetch_and_score")
    g.add_edge("fetch_and_score", "aggregate")
    g.add_edge("aggregate", "persist")
    g.add_edge("persist", END)
    return g


GRAPH = _build().compile(name="score_emotion")
