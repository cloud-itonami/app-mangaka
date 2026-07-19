"""Unit tests for the Hume distillation persistence path.

Covers three layers:
  1. `tool_persist_hume_emotion_observation` shape (dry-run + missing-blob
     short-circuits, `signal_id` derivation, full row payload sanity).
  2. `_score_one_render` returning the 4-tuple `(score, axes, notes,
     hume_evidence)` and `_step_critique_and_select` attaching
     `humeEvidence` onto each scored render entry.
  3. `_step_persist` fanning all per-render Hume evidence rows into the
     persist tool (one call per render, `selected=True` only for the chosen
     blob).

No DB I/O — psycopg connect is patched out. The persist tool is exercised
once for real shape via dry_run, then re-patched at the graph level to
collect call arguments.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_LG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_LG_DIR))

from lg_mangaka import tools as _tools
from lg_mangaka.graphs import compose_scene_3d as csd
from lg_mangaka.graphs.compose_scene_3d import (
    _VISION_AXES,
    _step_critique_and_select,
    _step_persist,
)


def _run(coro):
    return asyncio.run(coro)


# ── tool_persist_hume_emotion_observation ─────────────────────────────────


def test_persist_hume_dry_run_skips_db():
    out = _run(
        _tools.tool_persist_hume_emotion_observation(
            panel_rkey="panel-001",
            scene_rkey="scene3d-panel-001-1",
            iteration=0,
            angle="MediumShot",
            blob_key="blobs/anonymous/aaa",
            target_mood="triumph",
            target_family="joy",
            hume_score=0.87,
            primary={"name": "joy", "score": 0.91},
            top_emotions=[{"name": "joy", "score": 0.91}],
            image_features={"luminance": 0.6, "saturation": 0.8},
            algorithm="visual_heuristic_v1",
            dry_run=True,
        )
    )
    assert out["status"] == "skipped-dry-run"
    assert out["signal_id"] is None


def test_persist_hume_no_blob_skips_silently():
    out = _run(
        _tools.tool_persist_hume_emotion_observation(
            panel_rkey="panel-001",
            scene_rkey=None,
            iteration=0,
            angle=None,
            blob_key=None,
            target_mood=None,
            target_family=None,
            hume_score=0.5,
            primary=None,
            top_emotions=None,
            image_features=None,
        )
    )
    assert out["status"] == "skipped-no-blob"
    assert out["signal_id"] is None


def test_persist_hume_executes_insert_with_full_payload(monkeypatch):
    """When `dry_run=False` and a blob is supplied, the tool reaches
    psycopg.AsyncConnection.connect with a SQL INSERT carrying all
    distillation fields: imageFeatures, topEmotions, targetMoodFamily,
    targetMood, primary, humeScore. We capture the SQL + params via a fake
    connection so the test stays offline."""

    captured: dict = {"execs": []}

    class _FakeCursor:
        async def execute(self, sql, params):
            captured["execs"].append((sql, params))

    class _FakeConn:
        def cursor(self):
            return _FakeCursor()

        async def close(self):
            captured["closed"] = True

    async def _fake_connect(url, **_kw):
        captured["url"] = url
        return _FakeConn()

    import psycopg
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", _fake_connect)

    out = _run(
        _tools.tool_persist_hume_emotion_observation(
            panel_rkey="panel-007",
            scene_rkey="scene3d-panel-007-42",
            iteration=2,
            angle="OverShoulder",
            blob_key="blobs/anonymous/zzz",
            target_mood="ominous tension",
            target_family="fear",
            hume_score=0.34,
            primary={"name": "anxiety", "score": 0.71},
            top_emotions=[
                {"name": "anxiety", "score": 0.71},
                {"name": "doubt", "score": 0.42},
            ],
            image_features={
                "luminance": 0.18,
                "r_weight": 0.20,
                "g_weight": 0.22,
                "b_weight": 0.58,
                "saturation": 0.31,
                "contrast": 0.74,
            },
            algorithm="visual_heuristic_v1",
            source="compose_scene_3d",
            selected=True,
            dry_run=False,
            rw_url="postgresql://fake/rw",
        )
    )
    assert out["status"] == "stored"
    assert out["signal_id"] == "hume:panel-007:i2:OverShoulder:blobs/anonymous/zzz"
    assert captured["url"] == "postgresql://fake/rw"
    assert captured["closed"] is True
    # 2 execs: DELETE then INSERT
    assert len(captured["execs"]) == 2
    delete_sql, delete_params = captured["execs"][0]
    assert delete_sql.startswith("DELETE FROM vertex_vector_emotion_signal")
    assert delete_params == (out["signal_id"],)
    insert_sql, insert_params = captured["execs"][1]
    assert insert_sql.startswith("INSERT INTO vertex_vector_emotion_signal")
    # _seq + sensitivity_ord are inlined as 0 in the SQL string, so the
    # parameter tuple drops those two columns. Effective positions:
    #   0: signal_id
    #   1: created_date
    #   2: owner_did
    #   3: source_uri
    #   4: source_vertex_id
    #   5: tenant_id ('public')
    #   6: shard_id (0)
    #   7: modality ('image')
    #   8: provider ('Hume AI')
    #   9: model_id ('hume-image-head')
    #  10: model_version (algorithm)
    #  11: granularity ('panel')
    #  12: language (None)
    #  13: top_emotion
    #  14: top_score
    #  15: scores_json
    #  16: raw_json
    #  17: analyzed_at
    #  18: org_id
    assert insert_params[0] == out["signal_id"]
    assert insert_params[3].endswith("/scene3d-panel-007-42")
    assert insert_params[7] == "image"
    assert insert_params[8] == "Hume AI"
    assert insert_params[9] == "hume-image-head"
    assert insert_params[10] == "visual_heuristic_v1"
    assert insert_params[13] == "anxiety"
    assert abs(insert_params[14] - 0.71) < 1e-9
    # scores_json carries topEmotions
    import json
    scores = json.loads(insert_params[15])
    assert scores[0]["name"] == "anxiety"
    # raw_json is the distillation envelope
    raw = json.loads(insert_params[16])
    assert raw["schema"] == "com.etzhayyim.mangaka.humeObservation.v1"
    assert raw["input"]["imageFeatures"]["b_weight"] == 0.58
    assert raw["labels"]["targetFamily"] == "fear"
    assert raw["labels"]["targetMood"] == "ominous tension"
    assert raw["selected"] is True
    assert raw["humeScore"] == 0.34


def test_persist_hume_returns_error_when_rw_url_missing():
    out = _run(
        _tools.tool_persist_hume_emotion_observation(
            panel_rkey="panel-001",
            scene_rkey=None,
            iteration=0,
            angle="Closeup",
            blob_key="blobs/anonymous/aaa",
            target_mood="calm",
            target_family="calm",
            hume_score=0.5,
            primary=None,
            top_emotions=None,
            image_features=None,
            dry_run=False,
            rw_url="",  # explicit empty + env unset would also yield this
        )
    )
    # _DEFAULT_RW_URL might be unset in the test env; if it is, error path
    # triggers. If it's set (live dev shell), the test is skipped to avoid
    # accidentally writing to a real cluster.
    if "error" in out:
        assert out["error"] == "RW_URL not configured"
    else:
        pytest.skip("RW_URL is set in env — skip the unset-env branch")


# ── critique attaches humeEvidence per render ─────────────────────────────


def test_critique_attaches_hume_evidence_to_each_entry(monkeypatch):
    """`_step_critique_and_select` must surface `humeEvidence` on every entry
    where the PNG was fetchable, so `_step_persist` can fan rows out."""
    monkeypatch.setattr(csd._blob, "is_configured", lambda: True)
    monkeypatch.setattr(csd._blob, "get", lambda key, **_: b"\x89PNG-fake")
    # Pin Hume so the test is deterministic and verifies plumbing only.
    monkeypatch.setattr(
        csd._hume,
        "score_emotion_alignment",
        lambda png, mood: (
            0.7,
            {
                "family": "joy" if mood else None,
                "primary": {"name": "joy", "score": 0.8},
                "topEmotions": [{"name": "joy", "score": 0.8}],
                "imageFeatures": {"luminance": 0.6},
                "algorithm": "visual_heuristic_v1",
                "source": "hume_image_head",
            },
        ),
    )

    async def fake_vision(*a, **k):
        return {ax: 0.8 for ax in _VISION_AXES}

    monkeypatch.setattr(csd._llm, "llm_vision_score", fake_vision)

    state = {
        "renders": [
            {"blobKey": "blobs/anonymous/aaa", "angle": "FullShot"},
            {"blobKey": "blobs/anonymous/bbb", "angle": "Closeup"},
        ],
        "panel_plan": {"shot": "FullShot", "mood": "triumph"},
        "camera_plan": {"camera": {"shot": "FullShot"}},
    }
    out = _run(_step_critique_and_select(state))

    for entry in out["renders"]:
        evidence = entry.get("humeEvidence")
        assert evidence is not None, f"missing humeEvidence on {entry['blobKey']}"
        assert evidence["family"] == "joy"
        assert evidence["humeScore"] == 0.7
        assert evidence["targetMood"] == "triumph"
        assert evidence["imageFeatures"]["luminance"] == 0.6


def test_critique_omits_hume_evidence_for_placeholder_renders(monkeypatch):
    """`pending-*` renders never reach Hume — `humeEvidence` should be
    absent so the persist step skips them."""
    state = {
        "renders": [{"blobKey": "pending-foo-i1-a0", "angle": "MediumShot"}],
        "panel_plan": {"shot": "MediumShot", "mood": "urgent"},
        "camera_plan": {"camera": {"shot": "MediumShot"}},
    }
    out = _run(_step_critique_and_select(state))
    entry = out["selected"]
    assert "humeEvidence" not in entry


# ── _step_persist fans evidence into the persist tool ─────────────────────


def test_persist_fans_hume_observations_per_render(monkeypatch):
    """Every entry with `humeEvidence` must trigger one
    `tool_persist_hume_emotion_observation` call. Only the selected entry's
    call carries `selected=True`."""
    persist_scene_calls: list = []
    persist_hume_calls: list = []

    async def fake_persist_scene(**kwargs):
        persist_scene_calls.append(kwargs)
        return {"scene_rkey": "scene3d-panel-007-99", "status": "rendered"}

    async def fake_persist_hume(**kwargs):
        persist_hume_calls.append(kwargs)
        return {"signal_id": f"hume:{kwargs.get('blob_key')}", "status": "stored"}

    monkeypatch.setattr(csd._tools, "tool_persist_scene_3d", fake_persist_scene)
    monkeypatch.setattr(csd._tools, "tool_persist_hume_emotion_observation", fake_persist_hume)

    state = {
        "panel_rkey": "panel-007",
        "iteration": 2,
        "selected": {"blobKey": "blobs/anonymous/bbb", "angle": "Closeup"},
        "scene_dag": {},
        "camera_plan": {},
        "pose_plan": {},
        "score": 0.81,
        "sim_seed": 0,
        "dry_run": False,
        "renders": [
            {
                "blobKey": "blobs/anonymous/aaa",
                "angle": "FullShot",
                "humeEvidence": {
                    "family": "joy",
                    "humeScore": 0.6,
                    "targetMood": "triumph",
                    "primary": {"name": "joy", "score": 0.8},
                    "topEmotions": [{"name": "joy", "score": 0.8}],
                    "imageFeatures": {"luminance": 0.6},
                    "algorithm": "visual_heuristic_v1",
                },
            },
            {
                "blobKey": "blobs/anonymous/bbb",
                "angle": "Closeup",
                "humeEvidence": {
                    "family": "joy",
                    "humeScore": 0.85,
                    "targetMood": "triumph",
                    "primary": {"name": "excitement", "score": 0.9},
                    "topEmotions": [{"name": "excitement", "score": 0.9}],
                    "imageFeatures": {"luminance": 0.7},
                    "algorithm": "visual_heuristic_v1",
                },
            },
            {
                "blobKey": "pending-foo-i2-a2",
                "angle": "OverShoulder",
                # No humeEvidence — placeholder render, should be skipped.
            },
        ],
    }

    out = _run(_step_persist(state))

    assert out["status"] == "rendered"
    assert out["scene_rkey"] == "scene3d-panel-007-99"
    assert out["hume_persisted"] == 2  # placeholder skipped

    # One scene_3d insert.
    assert len(persist_scene_calls) == 1
    # Two hume observation inserts.
    assert len(persist_hume_calls) == 2
    by_blob = {c["blob_key"]: c for c in persist_hume_calls}
    assert by_blob["blobs/anonymous/aaa"]["selected"] is False
    assert by_blob["blobs/anonymous/bbb"]["selected"] is True
    assert by_blob["blobs/anonymous/bbb"]["scene_rkey"] == "scene3d-panel-007-99"
    assert by_blob["blobs/anonymous/aaa"]["iteration"] == 2
    assert by_blob["blobs/anonymous/aaa"]["image_features"] == {"luminance": 0.6}


def test_persist_swallows_hume_errors_without_failing_scene(monkeypatch):
    """A Hume persist failure must NOT roll back the scene row — the scene
    is already durable; Hume rows are best-effort distillation evidence."""

    async def fake_persist_scene(**_):
        return {"scene_rkey": "scene3d-ok", "status": "rendered"}

    async def fake_persist_hume(**_):
        raise RuntimeError("simulated RW outage")

    monkeypatch.setattr(csd._tools, "tool_persist_scene_3d", fake_persist_scene)
    monkeypatch.setattr(csd._tools, "tool_persist_hume_emotion_observation", fake_persist_hume)

    state = {
        "panel_rkey": "panel-X",
        "iteration": 0,
        "selected": {"blobKey": "blobs/anonymous/x"},
        "renders": [
            {
                "blobKey": "blobs/anonymous/x",
                "humeEvidence": {
                    "family": "calm",
                    "humeScore": 0.5,
                    "targetMood": "calm",
                    "primary": {"name": "calm", "score": 0.6},
                    "topEmotions": [],
                    "imageFeatures": {},
                    "algorithm": "visual_heuristic_v1",
                },
            },
        ],
        "score": 0.7,
    }
    out = _run(_step_persist(state))
    assert out["status"] == "rendered"
    assert out["scene_rkey"] == "scene3d-ok"
    assert out["hume_persisted"] == 0
