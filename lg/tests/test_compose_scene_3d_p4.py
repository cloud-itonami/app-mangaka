"""P4 unit tests — vision critique parser + axis clamping + fallback.

Pure-CPU, no network: we exercise `_axes_from_parsed`, `_vision_user_prompt`,
and the `_step_critique_and_select` happy + fallback paths by stubbing
`llm.llm_vision_score` and `blob.get`. The real OpenAI vision call is
integration-tested separately when `OPENAI_API_KEY` is set.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_LG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_LG_DIR))

import pytest

from lg_mangaka.graphs import compose_scene_3d as csd
from lg_mangaka.graphs.compose_scene_3d import (
    _VISION_AXES,
    _axes_from_parsed,
    _step_critique_and_select,
    _vision_user_prompt,
)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


# ── _axes_from_parsed ─────────────────────────────────────────────────────


def test_axes_from_parsed_clamps_to_unit():
    parsed = {
        "composition": 1.5,
        "silhouette": -0.4,
        "characterRecognizability": 0.81,
        "framing": 0.62,
        "mangaShotGrammar": 0.91,
        "lightingDrama": 0.55,
        "actionClarity": 0.88,
    }
    axes = _axes_from_parsed(parsed)
    assert axes is not None
    assert axes["composition"] == 1.0
    assert axes["silhouette"] == 0.0
    assert abs(axes["characterRecognizability"] - 0.81) < 1e-6


def test_axes_from_parsed_fills_missing_with_neutral():
    parsed = {
        "composition": 0.9,
        "silhouette": 0.8,
        "characterRecognizability": 0.85,
        "framing": 0.75,
        # mangaShotGrammar / lightingDrama / actionClarity missing
    }
    axes = _axes_from_parsed(parsed)
    assert axes is not None
    assert axes["mangaShotGrammar"] == 0.5
    assert axes["lightingDrama"] == 0.5
    assert axes["actionClarity"] == 0.5
    assert len(axes) == len(_VISION_AXES)


def test_axes_from_parsed_rejects_too_few_axes():
    parsed = {
        "composition": 0.9,
        "framing": 0.8,
        "notes": "missing most axes",
    }
    assert _axes_from_parsed(parsed) is None


def test_axes_from_parsed_rejects_non_numeric():
    parsed = {
        "composition": "high",
        "silhouette": None,
        "characterRecognizability": True,  # bool is technically int — keep this in mind
        "framing": "n/a",
        "mangaShotGrammar": "n/a",
        "lightingDrama": "n/a",
        "actionClarity": "n/a",
    }
    # `True` evaluates as int 1.0 (Python quirk); rest non-numeric → still
    # only 1 valid axis, < 4 threshold ⇒ None.
    assert _axes_from_parsed(parsed) is None


# ── _vision_user_prompt ───────────────────────────────────────────────────


def test_vision_user_prompt_serialises_brief():
    plan = {
        "shot": "Closeup",
        "action": "swings the blade",
        "mood": "fury",
        "characters": ["ch-honoka", "ch-yumi"],
        "environment": "env-rooftop-rain",
    }
    cam = {"shot": "Closeup", "fov_deg": 38.0, "roll_deg": 8.0, "dof": {"focus_distance_m": 1.2, "aperture": 1.8}}
    prompt = _vision_user_prompt(plan, cam)
    # JSON, includes both plan and camera context.
    import json
    obj = json.loads(prompt)
    assert obj["shot_intent"] == "Closeup"
    assert obj["camera"]["fov_deg"] == 38.0
    assert obj["characters"] == ["ch-honoka", "ch-yumi"]


# ── _step_critique_and_select integration paths ───────────────────────────


def test_critique_returns_error_when_no_renders(monkeypatch):
    out = _run(_step_critique_and_select({}))
    assert out["status"] == "error"
    assert "no renders" in out["error"]


def test_critique_uses_fallback_for_placeholder_renders(monkeypatch):
    state = {
        "renders": [
            {"blobKey": "pending-foo-i1-a0", "angle": "MediumShot"},
            {"blobKey": "pending-foo-i1-a1", "angle": "Closeup"},
        ],
        "panel_plan": {"shot": "MediumShot", "action": "dash", "mood": "urgent"},
        "camera_plan": {"camera": {"shot": "MediumShot"}},
    }
    monkeypatch.setattr(csd, "_FALLBACK_SCORE", 0.6)
    out = _run(_step_critique_and_select(state))
    assert "selected" in out
    assert out["score"] == 0.6
    # No critique key when vision wasn't used.
    assert "critique" not in out["selected"]


def test_critique_picks_highest_aggregate_when_vision_succeeds(monkeypatch):
    """Mock `_blob.get` + `_llm.llm_vision_score` to return different scores
    per render and verify the highest-aggregate render wins."""
    monkeypatch.setattr(csd._blob, "is_configured", lambda: True)
    monkeypatch.setattr(csd._blob, "get", lambda key, **_: b"\x89PNG-fake")
    # `_score_one_render` overlays the Hume `emotionAlignment` axis on top of
    # the LLM scores; pin it deterministically so this test only validates the
    # max-aggregate routing logic and not Hume's image-feature heuristic.
    # Hume returns the same score the LLM emits so the aggregate-mean math
    # stays predictable in this test. Hume itself is exercised separately in
    # hume_emotion tests.
    monkeypatch.setattr(csd._hume, "score_emotion_alignment", lambda png, mood: (0.85, {}))

    # We need score to vary per render. Patch llm_vision_score to return
    # different axes per call (call_counter advances each invocation).
    call_counter = {"n": 0}

    async def fake_vision_seq(prompt, images_b64, **_):
        call_counter["n"] += 1
        n = call_counter["n"]
        if n == 1:
            return {a: 0.4 for a in _VISION_AXES}
        if n == 2:
            return {a: 0.85 for a in _VISION_AXES}
        if n == 3:
            return {a: 0.7 for a in _VISION_AXES}
        return None

    monkeypatch.setattr(csd._llm, "llm_vision_score", fake_vision_seq)

    state = {
        "renders": [
            {"blobKey": "blobs/anonymous/aaa", "angle": "FullShot"},
            {"blobKey": "blobs/anonymous/bbb", "angle": "Closeup"},
            {"blobKey": "blobs/anonymous/ccc", "angle": "OverShoulder"},
        ],
        "panel_plan": {"shot": "FullShot"},
        "camera_plan": {"camera": {"shot": "FullShot"}},
    }
    out = _run(_step_critique_and_select(state))
    assert out["selected"]["blobKey"] == "blobs/anonymous/bbb"
    assert abs(out["score"] - 0.85) < 1e-6
    assert out["selected"]["critique"]["axes"]["composition"] == 0.85


def test_critique_falls_back_when_vision_returns_none(monkeypatch):
    monkeypatch.setattr(csd._blob, "is_configured", lambda: True)
    monkeypatch.setattr(csd._blob, "get", lambda key, **_: b"\x89PNG-fake")
    # Hume returns the same score the LLM emits so the aggregate-mean math
    # stays predictable in this test. Hume itself is exercised separately in
    # hume_emotion tests.
    monkeypatch.setattr(csd._hume, "score_emotion_alignment", lambda png, mood: (0.85, {}))

    async def fake_vision_none(*a, **k):
        return None

    monkeypatch.setattr(csd._llm, "llm_vision_score", fake_vision_none)
    monkeypatch.setattr(csd, "_FALLBACK_SCORE", 0.55)

    state = {
        "renders": [{"blobKey": "blobs/anonymous/x", "angle": "MediumShot"}],
        "panel_plan": {"shot": "MediumShot"},
        "camera_plan": {"camera": {"shot": "MediumShot"}},
    }
    out = _run(_step_critique_and_select(state))
    assert out["score"] == 0.55
    assert "critique" not in out["selected"]


def test_critique_includes_notes_and_improvements(monkeypatch):
    monkeypatch.setattr(csd._blob, "is_configured", lambda: True)
    monkeypatch.setattr(csd._blob, "get", lambda key, **_: b"\x89PNG")

    async def fake_vision(*a, **k):
        return {
            **{ax: 0.7 for ax in _VISION_AXES},
            "notes": "Silhouette merges with background",
            "improvements": "Raise rim light intensity",
        }

    monkeypatch.setattr(csd._llm, "llm_vision_score", fake_vision)

    state = {
        "renders": [{"blobKey": "blobs/anonymous/x", "angle": "MediumShot"}],
        "panel_plan": {"shot": "MediumShot"},
        "camera_plan": {"camera": {"shot": "MediumShot"}},
    }
    out = _run(_step_critique_and_select(state))
    crit = out["selected"]["critique"]
    assert "Silhouette" in crit["notes"]
    assert "rim light" in crit["notes"].lower()
