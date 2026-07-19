"""Unit tests for `lg_mangaka.graphs.score_emotion`.

Pure-CPU. Mocks psycopg + httpx + hume_image_head so the suite never
touches RisingWave, B2, or any LLM. Covers the 4 super-steps end-to-end
on a synthetic doc with 2 ai-images + 1 panel parent, then asserts the
panel aggregate is the max-saliency winner across its scored children.
"""

from __future__ import annotations

import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_LG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_LG_DIR))

from lg_mangaka.graphs import score_emotion as se
from lg_mangaka.graphs.score_emotion import (
    _step_aggregate,
    _step_load_target,
    _step_persist,
)


# ── fixtures ──────────────────────────────────────────────────────────────


def _doc_with_two_ai_images_under_one_panel() -> dict:
    """A page with a single panel that contains two ai-images. Mirrors the
    Genko document shape: each node carries `_nid` + `type` + arbitrary
    payload fields under `data`."""
    return {
        "name": "test-doc",
        "pages": [{
            "nodes": [
                {"data": {"_nid": "pnl-1", "type": "panel",
                          "_panelChildren": ["img-a", "img-b"]}},
                {"data": {"_nid": "img-a", "type": "ai-image",
                          "_genImageUrl": "https://mangaka.etzhayyim.com/blob/cid-a?did=anonymous"}},
                {"data": {"_nid": "img-b", "type": "ai-image",
                          "_genImageUrl": "https://mangaka.etzhayyim.com/blob/cid-b?did=anonymous"}},
                # Unrelated nodes that must NOT be touched.
                {"data": {"_nid": "txt-1", "type": "text", "text": "hello"}},
            ],
        }],
    }


def _fake_conn_returning(doc_json: str):
    """Async psycopg connection shaped to match score_emotion's usage:
       conn = await psycopg.AsyncConnection.connect(...)
       cur  = conn.cursor()
       await cur.execute(...)
       row  = await cur.fetchone()
    Both `execute` and `fetchone` on the cursor must be awaitable mocks."""
    cur = MagicMock()
    cur.execute = AsyncMock()
    cur.fetchone = AsyncMock(return_value=(doc_json,))
    conn = MagicMock()
    conn.cursor = MagicMock(return_value=cur)
    conn.close = AsyncMock()
    return conn, cur


# ── _step_load_target ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_target_resolves_targets_with_panel_parent(monkeypatch):
    monkeypatch.setattr(se, "_RW_URL", "postgresql://fake/rw")
    doc = _doc_with_two_ai_images_under_one_panel()
    conn, _ = _fake_conn_returning(json.dumps(doc))

    async def _connect(url, **_kw):
        return conn

    with patch("psycopg.AsyncConnection.connect", _connect):
        out = await _step_load_target({"docId": "doc-001"})

    assert out["doc"]["name"] == "test-doc"
    targets = out["targets"]
    assert len(targets) == 2
    assert {t["nid"] for t in targets} == {"img-a", "img-b"}
    # `_panelChildren` lookup wired both ai-images to their parent panel.
    assert {t["parent_panel_nid"] for t in targets} == {"pnl-1"}
    assert targets[0]["cid"] == "cid-a"
    assert "blob/cid-a" in targets[0]["url"]


@pytest.mark.asyncio
async def test_load_target_errors_without_rw_url(monkeypatch):
    monkeypatch.setattr(se, "_RW_URL", "")
    out = await _step_load_target({"docId": "doc-001"})
    assert out["status"] == "error"
    assert "RW_URL" in out["error"]


@pytest.mark.asyncio
async def test_load_target_filters_to_single_image_when_imageNid_given(monkeypatch):
    monkeypatch.setattr(se, "_RW_URL", "postgresql://fake/rw")
    doc = _doc_with_two_ai_images_under_one_panel()
    conn, _ = _fake_conn_returning(json.dumps(doc))

    async def _connect(url, **_kw):
        return conn

    with patch("psycopg.AsyncConnection.connect", _connect):
        out = await _step_load_target({"docId": "doc-001", "imageNid": "img-b"})

    assert [t["nid"] for t in out["targets"]] == ["img-b"]


# ── _step_aggregate ───────────────────────────────────────────────────────


def test_aggregate_picks_max_saliency_child_for_each_panel():
    """The child with the highest `primary.score` wins the panel aggregate.
    Ties are broken by sum of topEmotions scores."""
    state = {
        "targets": [
            {"nid": "img-a", "parent_panel_nid": "pnl-1"},
            {"nid": "img-b", "parent_panel_nid": "pnl-1"},
            {"nid": "img-c", "parent_panel_nid": "pnl-2"},
        ],
        "emotions": {
            "img-a": {"primary": {"name": "joy", "score": 0.65},
                      "topEmotions": [{"name": "joy", "score": 0.65}]},
            "img-b": {"primary": {"name": "anxiety", "score": 0.81},
                      "topEmotions": [{"name": "anxiety", "score": 0.81}]},
            "img-c": {"primary": {"name": "calm", "score": 0.55},
                      "topEmotions": [{"name": "calm", "score": 0.55}]},
        },
    }
    out = _step_aggregate(state)
    panel_emos = out["panel_emotions"]
    # pnl-1's max-saliency winner is img-b (anxiety @ 0.81).
    assert panel_emos["pnl-1"]["primary"]["name"] == "anxiety"
    assert panel_emos["pnl-1"]["winningChild"] == "img-b"
    assert panel_emos["pnl-1"]["sourceCount"] == 2
    # pnl-2 has only one child.
    assert panel_emos["pnl-2"]["primary"]["name"] == "calm"
    assert panel_emos["pnl-2"]["sourceCount"] == 1


def test_aggregate_skips_panels_without_scored_children():
    """A target whose `parent_panel_nid` is None doesn't roll up to any
    panel aggregate — its emotion stays on the ai-image alone."""
    state = {
        "targets": [{"nid": "img-loose", "parent_panel_nid": None}],
        "emotions": {"img-loose": {"primary": {"name": "joy", "score": 0.5},
                                    "topEmotions": []}},
    }
    out = _step_aggregate(state)
    assert out["panel_emotions"] == {}


def test_aggregate_passes_through_empty_emotions():
    out = _step_aggregate({"targets": [], "emotions": {}})
    assert out["panel_emotions"] == {}


# ── _step_persist ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_persist_patches_emotion_onto_ai_image_and_panel_nodes(monkeypatch):
    """End-to-end persist: both ai-image and panel nodes get `_emotion`
    written; unrelated node types stay untouched; doc INSERT preserves
    the doc.name + RLS columns."""
    monkeypatch.setattr(se, "_RW_URL", "postgresql://fake/rw")
    doc = _doc_with_two_ai_images_under_one_panel()
    captured_sql: list[tuple] = []

    cur = MagicMock()
    cur.execute = AsyncMock()
    conn = MagicMock()
    conn.cursor = MagicMock(return_value=cur)
    conn.close = AsyncMock()

    async def _execute(sql, params=None):
        captured_sql.append((sql, params))

    cur.execute = AsyncMock(side_effect=_execute)

    async def _connect(url, **_kw):
        return conn

    state = {
        "docId": "doc-001",
        "doc": doc,
        "emotions": {
            "img-a": {"primary": {"name": "joy", "score": 0.7},
                      "topEmotions": [{"name": "joy", "score": 0.7}],
                      "algorithm": "visual_heuristic_v1", "scoredAt": "2026-05-14T00:00:00Z",
                      "sourceCount": 1},
            "img-b": {"primary": {"name": "anxiety", "score": 0.85},
                      "topEmotions": [{"name": "anxiety", "score": 0.85}],
                      "algorithm": "visual_heuristic_v1", "scoredAt": "2026-05-14T00:00:00Z",
                      "sourceCount": 1},
        },
        "panel_emotions": {
            "pnl-1": {"primary": {"name": "anxiety", "score": 0.85},
                      "topEmotions": [{"name": "anxiety", "score": 0.85}],
                      "algorithm": "visual_heuristic_v1", "scoredAt": "2026-05-14T00:00:00Z",
                      "sourceCount": 2, "winningChild": "img-b"},
        },
        "method": "heuristic",
        "_t0": 0.0,
    }

    with patch("psycopg.AsyncConnection.connect", _connect):
        out = await _step_persist(state)

    assert out["status"] == "ok"
    assert out["docId_out"] == "doc-001"
    assert out["imageNid_out"] is None
    assert "perImage" in out and "panelEmotion" in out

    # The INSERT body must be the patched doc JSON — re-parse it and walk
    # the nodes to confirm _emotion landed on each.
    insert_sql_calls = [c for c in captured_sql if "INSERT INTO vertex_mangaka" in c[0]]
    assert len(insert_sql_calls) == 1
    insert_params = insert_sql_calls[0][1]
    # props column is the 15th param per the INSERT statement (0-indexed 14).
    # The exact index is fragile; safer to find the JSON one by shape.
    props_json = next(p for p in insert_params if isinstance(p, str) and p.startswith("{"))
    persisted_doc = json.loads(props_json)
    nodes = persisted_doc["pages"][0]["nodes"]
    by_nid = {n["data"]["_nid"]: n["data"] for n in nodes}
    # ai-images patched.
    assert by_nid["img-a"]["_emotion"]["primary"]["name"] == "joy"
    assert by_nid["img-b"]["_emotion"]["primary"]["name"] == "anxiety"
    # Panel patched with the aggregate.
    assert by_nid["pnl-1"]["_emotion"]["primary"]["name"] == "anxiety"
    assert by_nid["pnl-1"]["_emotion"]["winningChild"] == "img-b"
    # Unrelated node untouched.
    assert "_emotion" not in by_nid["txt-1"]


@pytest.mark.asyncio
async def test_persist_single_image_mode_returns_emotion_directly(monkeypatch):
    """When `imageNid` is set, persist returns `emotion` (single) instead
    of perImage/panelEmotion maps so the lexicon's single-mode shape lands
    cleanly on the XRPC wire."""
    monkeypatch.setattr(se, "_RW_URL", "postgresql://fake/rw")
    cur = MagicMock()
    cur.execute = AsyncMock()
    conn = MagicMock()
    conn.cursor = MagicMock(return_value=cur)
    conn.close = AsyncMock()

    async def _connect(url, **_kw):
        return conn

    state = {
        "docId": "doc-001", "imageNid": "img-a",
        "doc": _doc_with_two_ai_images_under_one_panel(),
        "emotions": {"img-a": {"primary": {"name": "joy", "score": 0.7},
                                "topEmotions": [], "algorithm": "h", "scoredAt": "x",
                                "sourceCount": 1}},
        "panel_emotions": {},
        "method": "heuristic", "_t0": 0.0,
    }
    with patch("psycopg.AsyncConnection.connect", _connect):
        out = await _step_persist(state)

    assert out["imageNid_out"] == "img-a"
    assert out["emotion"]["primary"]["name"] == "joy"
    assert "perImage" not in out
    assert "panelEmotion" not in out


@pytest.mark.asyncio
async def test_persist_propagates_error_state(monkeypatch):
    out = await _step_persist({"status": "error", "error": "upstream broke", "_t0": 0.0})
    assert out["status"] == "error"
    assert out["error"] == "upstream broke"
    assert "latencyMs" in out


# ── load_student_model ────────────────────────────────────────────────────


def test_load_student_model_handles_bare_and_wrapped_json(tmp_path, monkeypatch):
    """The runner accepts both the bare `train_image_centroid` output
    (top-level `emotionCentroids`) and the `run_distillation` wrapper
    `{model, metrics}` form."""
    from lg_mangaka.graphs.score_emotion import _load_student_model

    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps({"emotionCentroids": {"joy": {"luminance": 0.5}}}))
    monkeypatch.setattr(se, "_STUDENT_MODEL_PATH", str(bare))
    out = _load_student_model()
    assert out is not None and "emotionCentroids" in out

    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text(json.dumps({"model": {"emotionCentroids": {"calm": {"luminance": 0.3}}},
                                    "metrics": {"rows": 10}}))
    monkeypatch.setattr(se, "_STUDENT_MODEL_PATH", str(wrapped))
    out = _load_student_model()
    assert out is not None and "calm" in out["emotionCentroids"]


def test_load_student_model_returns_none_when_unset(monkeypatch):
    from lg_mangaka.graphs.score_emotion import _load_student_model

    monkeypatch.setattr(se, "_STUDENT_MODEL_PATH", "")
    assert _load_student_model() is None
