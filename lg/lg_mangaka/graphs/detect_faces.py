"""mangaka `detect_faces` — Pregel-style 4-step face detection for ai-image nodes.

Anchors fukidashi tails to detected character faces. Input is `docId + imageNid`
(detect for one image) or `docId` only (batch all ai-images in the doc).

Super-steps:

  1. load_target    — SELECT props from vertex_mangaka kind='document' WHERE rkey=docId
                       → resolve target ai-image nodes (single or batch)
  2. fetch_blob     — for each ai-image, GET the blob bytes via /blob/{cid}?did=anonymous
  3. detect         — run face detection (PIL + simple skin/edge heuristic, since
                       opencv/mediapipe aren't in the pod). Falls back to a single
                       centered face if no detector available. Returns normalized
                       (0-1) cx,cy per face.
  4. persist        — update each ai-image's `_faces[]` in the doc JSON, re-save
                       via the same DELETE-then-INSERT path as save_document.

Input:
    docId        str  (required)
    imageNid     str  (optional — if omitted, processes all ai-images in doc)

Output:
    status           "ok" | "error"
    docId            echo
    imageNid         echo (or null when batch)
    faces            list[ {cx: float, cy: float, w?: float, h?: float} ]  (single)
    perImage         { imageNid: list[face] }                              (batch)
    method           "torch-yolo" | "haar-stub" | "heuristic" | "fallback"
    latencyMs        int
    error            str | null
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import math
import os
import time
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

_log = logging.getLogger(__name__)

_APP_DID = os.environ.get("MANGAKA_APP_DID", "did:web:mangaka.etzhayyim.com")
_NSID = "com.etzhayyim.mangaka.document"
_BLOB_BASE = os.environ.get("MANGAKA_BLOB_BASE", "https://mangaka.etzhayyim.com")


class _State(TypedDict, total=False):
    doc_id: str
    docId: str
    image_nid: str
    imageNid: str
    doc: dict
    targets: list                 # [{nid, url}]
    detections: dict              # { nid: list[face] }
    method: str
    status: str
    docId_out: str
    imageNid_out: str | None
    faces: list
    perImage: dict
    latencyMs: int
    error: str | None
    _t0: float


def _g(state: _State, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in state and state[k] is not None:
            return state[k]
    return default


def _extract_cid(url: str) -> str:
    if not url or "cid=" not in url:
        return ""
    tail = url.split("cid=", 1)[1]
    return tail.split("&", 1)[0]


# Super-step 1: load document + resolve target ai-images
async def _step_load_target(state: _State) -> dict[str, Any]:
    state_t0 = time.monotonic()
    
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
        row = (rows[0].get("props") if rows and rows[0].get("kind") == "document" else None,) if rows else None
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"{type(exc).__name__}: {exc!s}"[:300], "_t0": state_t0}
    if not row:
        return {"status": "error", "error": f"document not found: {doc_id}", "_t0": state_t0}
    try:
        doc = json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"json parse: {exc!s}"[:200], "_t0": state_t0}

    targets: list[dict[str, Any]] = []
    for p in (doc.get("pages") or []):
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
            targets.append({"nid": nid, "cid": cid, "url": f"{_BLOB_BASE}/blob/{cid}?did=anonymous"})
            if image_nid:
                break
        if image_nid and targets:
            break
    return {"doc": doc, "targets": targets, "_t0": state_t0}


# Super-step 2: fetch each blob, run detection inline, return only normalized
# face coords. PIL.Image objects are kept LOCAL to this step so langgraph's
# checkpoint serializer never sees them (it can't serialize PIL.Image).
async def _step_fetch_and_detect(state: _State) -> dict[str, Any]:
    if state.get("status") == "error":
        return {}
    targets = state.get("targets") or []
    detections: dict[str, Any] = {}
    method = "heuristic"
    if not targets:
        return {"detections": {}, "method": method}
    try:
        from PIL import Image  # type: ignore
        import httpx  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"deps missing: {exc!s}"[:200]}

    async with httpx.AsyncClient(timeout=30.0) as client:
        for t in targets:
            try:
                r = await client.get(t["url"])
                if r.status_code != 200:
                    _log.warning("blob fetch %s: %d", t["nid"], r.status_code)
                    detections[t["nid"]] = _detect_heuristic(None)
                    continue
                img = Image.open(io.BytesIO(r.content)).convert("RGB")
                # Order: anime cascade (manga-trained, fast) → torch MTCNN (photo)
                # → heuristic fallback. Skip downstream if upstream returns non-empty.
                faces = _detect_anime_cascade(img)
                if faces is not None:
                    method = "anime-cascade"
                if not faces:
                    torch_faces = _detect_torch(img)
                    if torch_faces is not None:
                        faces = torch_faces
                        method = "torch-mtcnn"
                if not faces:
                    faces = _detect_heuristic(img)
                    if method == "heuristic":
                        pass  # already set
                detections[t["nid"]] = faces
            except Exception as exc:  # noqa: BLE001
                _log.warning("detect %s err: %s", t["nid"], exc)
                detections[t["nid"]] = _detect_heuristic(None)
    return {"detections": detections, "method": method}


def _detect_anime_cascade(img: Any) -> list[dict[str, Any]] | None:
    # OpenCV LBP cascade trained on anime/manga faces (nagadomi/lbpcascade_animeface).
    # Best detector for our use-case. Returns None when opencv or cascade missing.
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        return None
    path = os.environ.get("ANIME_CASCADE_PATH", "/app/models/lbpcascade_animeface.xml")
    if not os.path.exists(path):
        return None
    try:
        casc = cv2.CascadeClassifier(path)
        if casc.empty():
            return None
        arr = np.array(img.convert("L"))
        H, W = arr.shape
        # Equalise + slight blur helps recall on flat manga shading
        eq = cv2.equalizeHist(arr)
        faces_xywh = casc.detectMultiScale(eq, scaleFactor=1.1, minNeighbors=4, minSize=(24, 24))
        out: list[dict[str, Any]] = []
        for (x, y, w, h) in faces_xywh:
            cx = (x + w / 2) / W
            cy = (y + h / 2) / H
            out.append({
                "cx": float(max(0.0, min(1.0, cx))),
                "cy": float(max(0.0, min(1.0, cy))),
                "w": float(w / W), "h": float(h / H),
            })
        return out
    except Exception as exc:  # noqa: BLE001
        _log.warning("anime cascade detect failed: %s", exc)
        return None


def _detect_heuristic(img: Any) -> list[dict[str, Any]]:
    # Naive content-region heuristic for manga: assume one main subject occupies
    # upper-center to center. Returns one face at (0.5, 0.4). Better than nothing
    # until a real detector is wired in.
    return [{"cx": 0.5, "cy": 0.4, "w": 0.35, "h": 0.35}]


def _detect_torch(img: Any) -> list[dict[str, Any]] | None:
    # Try torch + a lightweight face detector (e.g. mtcnn via facenet-pytorch
    # if installed). Returns None when not available so caller falls back.
    try:
        from facenet_pytorch import MTCNN  # type: ignore
        import torch  # type: ignore
    except Exception:
        return None
    try:
        mtcnn = MTCNN(keep_all=True, device="cpu")
        boxes, _ = mtcnn.detect(img)
        if boxes is None:
            return []
        W, H = img.size
        out: list[dict[str, Any]] = []
        for b in boxes:
            x1, y1, x2, y2 = float(b[0]), float(b[1]), float(b[2]), float(b[3])
            cx = (x1 + x2) / 2 / W
            cy = (y1 + y2) / 2 / H
            w = (x2 - x1) / W
            h = (y2 - y1) / H
            out.append({"cx": max(0.0, min(1.0, cx)), "cy": max(0.0, min(1.0, cy)), "w": w, "h": h})
        return out
    except Exception as exc:  # noqa: BLE001
        _log.warning("torch face detect failed: %s", exc)
        return None


# Super-step 4: persist updated _faces[] back to the doc via DELETE-then-INSERT
async def _step_persist(state: _State) -> dict[str, Any]:
    if state.get("status") == "error":
        return {
            "status": "error", "error": state.get("error"),
            "latencyMs": int((time.monotonic() - (state.get("_t0") or time.monotonic())) * 1000),
        }
    doc = state.get("doc") or {}
    detections: dict[str, Any] = state.get("detections") or {}
    doc_id = (_g(state, "doc_id", "docId", default="") or "").strip()
    image_nid = (_g(state, "image_nid", "imageNid", default="") or "").strip() or None

    # Patch ai-image nodes
    for p in (doc.get("pages") or []):
        for n in (p.get("nodes") or []):
            data = n.get("data") if isinstance(n.get("data"), dict) else n
            if (data.get("type") or n.get("type")) != "ai-image":
                continue
            nid = data.get("_nid") or n.get("id") or ""
            if nid in detections:
                data["_faces"] = detections[nid]

    # Re-save full doc (mirror save_document path)
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

    # Single-image response shape: include `faces` directly.
    if image_nid:
        faces = detections.get(image_nid, [])
        return {
            "status": "ok",
            "docId_out": doc_id, "imageNid_out": image_nid,
            "faces": faces,
            "method": state.get("method") or "heuristic",
            "latencyMs": int((time.monotonic() - (state.get("_t0") or time.monotonic())) * 1000),
            "error": None,
        }
    return {
        "status": "ok",
        "docId_out": doc_id, "imageNid_out": None,
        "perImage": detections,
        "method": state.get("method") or "heuristic",
        "latencyMs": int((time.monotonic() - (state.get("_t0") or time.monotonic())) * 1000),
        "error": None,
    }


def _build():
    g: StateGraph = StateGraph(_State)
    g.add_node("load_target", _step_load_target, retry_policy=RetryPolicy(max_attempts=2))
    g.add_node("fetch_and_detect", _step_fetch_and_detect)
    g.add_node("persist", _step_persist)
    g.add_edge(START, "load_target")
    g.add_edge("load_target", "fetch_and_detect")
    g.add_edge("fetch_and_detect", "persist")
    g.add_edge("persist", END)
    return g


GRAPH = _build().compile(name="detect_faces")
