"""P10.2b unit tests — `tool_aggregate_critique`.

Pure-CPU with mocked Hume + B2. Validates the critique-side aggregator
that overlays Hume `emotionAlignment` over per-render axes, picks the
best render, and feeds the refinement-condition edge.
"""

from __future__ import annotations

import sys
from pathlib import Path

_LG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_LG_DIR))

import pytest

from lg_mangaka import blob as _blob
from lg_mangaka import hume_emotion as _hume
from lg_mangaka import tools as tools_mod
from lg_mangaka.tools import (
    VISION_AXES,
    _clamp_and_fill_axes,
    tool_aggregate_critique,
)


# ── _clamp_and_fill_axes ──────────────────────────────────────────────────


def test_clamp_and_fill_clamps_values():
    parsed = {a: 1.5 if i % 2 == 0 else -0.4 for i, a in enumerate(VISION_AXES)}
    axes = _clamp_and_fill_axes(parsed)
    assert axes is not None
    for v in axes.values():
        assert 0.0 <= v <= 1.0


def test_clamp_and_fill_fills_missing_with_neutral():
    parsed = {a: 0.8 for a in list(VISION_AXES)[:5]}  # 5/8 axes present
    axes = _clamp_and_fill_axes(parsed)
    assert axes is not None
    for a in VISION_AXES:
        assert a in axes
    # First 5 use the given value, rest fill 0.5
    for a in list(VISION_AXES)[:5]:
        assert axes[a] == 0.8
    for a in list(VISION_AXES)[5:]:
        assert axes[a] == 0.5


def test_clamp_and_fill_rejects_too_few_axes():
    parsed = {"composition": 0.9}
    assert _clamp_and_fill_axes(parsed) is None


def test_clamp_and_fill_rejects_non_numeric():
    parsed = {a: "high" for a in VISION_AXES}
    assert _clamp_and_fill_axes(parsed) is None


# ── empty / error paths ───────────────────────────────────────────────────


def test_aggregate_returns_error_when_no_renders():
    out = tool_aggregate_critique(renders=None)
    assert "error" in out and "no renders" in out["error"]


def test_aggregate_handles_empty_list():
    out = tool_aggregate_critique(renders=[])
    assert "error" in out


# ── fallback path (no axes, no Hume) ─────────────────────────────────────


def test_aggregate_uses_fallback_when_no_axes(monkeypatch):
    monkeypatch.setattr(_blob, "is_configured", lambda: False)
    renders = [
        {"blobKey": "pending-x-i1-a0", "angle": "MediumShot"},
        {"blobKey": "pending-x-i1-a1", "angle": "Closeup"},
    ]
    out = tool_aggregate_critique(renders=renders, fallback_score=0.55)
    assert "renders" in out and len(out["renders"]) == 2
    for r in out["renders"]:
        assert r["score"] == 0.55
    assert out["score"] == 0.55
    # First render wins on tie (deterministic max).
    assert out["selected"] is out["renders"][0]


# ── full axes path picks max aggregate ───────────────────────────────────


def test_aggregate_picks_highest_when_axes_present(monkeypatch):
    monkeypatch.setattr(_blob, "is_configured", lambda: False)
    renders = [
        {
            "blobKey": "blobs/anonymous/aaa",
            "angle": "FullShot",
            "critique": {"axes": {a: 0.4 for a in VISION_AXES}},
        },
        {
            "blobKey": "blobs/anonymous/bbb",
            "angle": "Closeup",
            "critique": {"axes": {a: 0.85 for a in VISION_AXES}},
        },
        {
            "blobKey": "blobs/anonymous/ccc",
            "angle": "OverShoulder",
            "critique": {"axes": {a: 0.7 for a in VISION_AXES}},
        },
    ]
    out = tool_aggregate_critique(renders=renders)
    assert out["selected"]["blobKey"] == "blobs/anonymous/bbb"
    assert abs(out["score"] - 0.85) < 1e-6
    assert out["selected"]["critique"]["axes"]["composition"] == 0.85


# ── Hume overlay path ────────────────────────────────────────────────────


def test_hume_overrides_emotion_alignment_axis(monkeypatch):
    monkeypatch.setattr(_blob, "is_configured", lambda: True)
    monkeypatch.setattr(_blob, "get", lambda key, **_: b"\x89PNG-fake")
    monkeypatch.setattr(_hume, "score_emotion_alignment", lambda png, mood: (0.95, {"primary": "joy"}))

    renders = [
        {
            "blobKey": "blobs/anonymous/aaa",
            "angle": "FullShot",
            "critique": {
                # LLM emitted emotionAlignment=0.30 — Hume should override to 0.95.
                "axes": {**{a: 0.5 for a in VISION_AXES}, "emotionAlignment": 0.30},
            },
        },
    ]
    out = tool_aggregate_critique(renders=renders, target_mood="joy")
    axes = out["selected"]["critique"]["axes"]
    assert axes["emotionAlignment"] == 0.95
    # The other axes preserved at 0.5.
    assert axes["composition"] == 0.5
    # Hume evidence surfaces.
    assert out["selected"]["critique"]["humeEvidence"] == {"primary": "joy"}


def test_hume_skipped_when_target_mood_missing(monkeypatch):
    fired = {"n": 0}

    def stub_hume(png, mood):
        fired["n"] += 1
        return (0.95, {})

    monkeypatch.setattr(_blob, "is_configured", lambda: True)
    monkeypatch.setattr(_blob, "get", lambda key, **_: b"\x89PNG-fake")
    monkeypatch.setattr(_hume, "score_emotion_alignment", stub_hume)

    renders = [
        {
            "blobKey": "blobs/anonymous/aaa",
            "critique": {"axes": {a: 0.7 for a in VISION_AXES}},
        },
    ]
    _ = tool_aggregate_critique(renders=renders, target_mood=None)
    assert fired["n"] == 0, "Hume must not fire when target_mood is None"


def test_hume_skipped_for_placeholder_blobs(monkeypatch):
    fired = {"n": 0}
    monkeypatch.setattr(_blob, "is_configured", lambda: True)
    monkeypatch.setattr(
        _hume,
        "score_emotion_alignment",
        lambda png, mood: (fired.__setitem__("n", fired["n"] + 1) or (0.9, {})),
    )
    renders = [{"blobKey": "pending-x-i1-a0", "critique": {"axes": {a: 0.7 for a in VISION_AXES}}}]
    _ = tool_aggregate_critique(renders=renders, target_mood="joy")
    assert fired["n"] == 0, "Hume must skip placeholder blobs"


def test_hume_skipped_when_blob_get_returns_none(monkeypatch):
    monkeypatch.setattr(_blob, "is_configured", lambda: True)
    monkeypatch.setattr(_blob, "get", lambda key, **_: None)
    fired = {"n": 0}
    monkeypatch.setattr(
        _hume,
        "score_emotion_alignment",
        lambda png, mood: (fired.__setitem__("n", fired["n"] + 1) or (0.9, {})),
    )
    renders = [{"blobKey": "blobs/anonymous/x", "critique": {"axes": {a: 0.7 for a in VISION_AXES}}}]
    out = tool_aggregate_critique(renders=renders, target_mood="joy")
    assert fired["n"] == 0
    # Without Hume the existing axes survive.
    assert out["selected"]["critique"]["axes"]["emotionAlignment"] == 0.7


def test_hume_exception_does_not_crash(monkeypatch):
    monkeypatch.setattr(_blob, "is_configured", lambda: True)
    monkeypatch.setattr(_blob, "get", lambda key, **_: b"\x89PNG")
    monkeypatch.setattr(_hume, "score_emotion_alignment", lambda png, mood: (_ for _ in ()).throw(RuntimeError("hume down")))
    renders = [{"blobKey": "blobs/anonymous/x", "critique": {"axes": {a: 0.6 for a in VISION_AXES}}}]
    out = tool_aggregate_critique(renders=renders, target_mood="joy")
    # Hume exception swallowed — LLM axes survive intact.
    assert out["selected"]["critique"]["axes"]["emotionAlignment"] == 0.6


# ── notes propagation ────────────────────────────────────────────────────


def test_notes_propagated_from_input_critique(monkeypatch):
    monkeypatch.setattr(_blob, "is_configured", lambda: False)
    renders = [
        {
            "blobKey": "blobs/anonymous/x",
            "critique": {
                "axes": {a: 0.7 for a in VISION_AXES},
                "notes": "Silhouette reads well; rim light feels muted.",
            },
        },
    ]
    out = tool_aggregate_critique(renders=renders)
    assert out["selected"]["critique"]["notes"] == "Silhouette reads well; rim light feels muted."


# ── Hume fallback when no LLM axes ───────────────────────────────────────


def test_hume_surfaces_when_no_llm_axes(monkeypatch):
    monkeypatch.setattr(_blob, "is_configured", lambda: True)
    monkeypatch.setattr(_blob, "get", lambda key, **_: b"\x89PNG")
    monkeypatch.setattr(_hume, "score_emotion_alignment", lambda png, mood: (0.82, {"primary": "anger"}))
    renders = [{"blobKey": "blobs/anonymous/x", "angle": "Closeup"}]
    out = tool_aggregate_critique(renders=renders, target_mood="rage", fallback_score=0.55)
    # No axes → fallback score, but Hume evidence surfaces as a sidecar.
    assert out["selected"]["score"] == 0.55
    assert out["selected"]["critique"]["axes"] is None
    assert out["selected"]["critique"]["humeEvidence"] == {"primary": "anger"}
