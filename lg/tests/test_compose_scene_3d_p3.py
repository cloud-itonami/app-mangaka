"""P3 unit tests — LLM merging + B2 content addressing.

Pure-CPU. No network calls (LLM path is mocked), no GPU. Designed to run via
`pytest 60-apps/etzhayyim-project-mangaka/lg/tests/` once pytest is installed
(`pip install pytest` or via `pyproject.toml [project.optional-dependencies]`).
The lg pod base image already pins pytest in the dev container layer.

Tests exercise only deterministic pure-Python helpers — the LangGraph nodes
themselves require the langgraph runtime + RW_URL + B2 creds and are covered
by integration tests.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_LG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_LG_DIR))

from lg_mangaka import blob
from lg_mangaka.graphs.compose_scene_3d import (
    _camera_variant,
    _resolve_expression,
    _resolve_pose_label,
)
# Camera + lighting validators were lifted into `lg_mangaka.tools` in P10.2
# (Phase C blocker #2) as the shared SSoT for the in-tree node and the
# Phase C `validate_camera_plan` MCP tool. Tests exercise them at their new
# home so the suite continues to cover the same behaviour.
from lg_mangaka.tools import _camera_from_llm, _lights_from_llm  # noqa: E402


# ── lexicon routing (P1, asserted from P3 vantage) ────────────────────────


def test_resolve_pose_label_routes_keywords():
    assert _resolve_pose_label("she dashes toward the camera") == "action.dash"
    assert _resolve_pose_label("punches the wall") == "action.attack"
    assert _resolve_pose_label("falls over") == "action.fall"
    assert _resolve_pose_label("cowers in the corner") == "action.cower"
    assert _resolve_pose_label("points at the door") == "action.point"
    assert _resolve_pose_label("stands proudly") == "action.stand_proud"


def test_resolve_pose_label_defaults_idle_on_blank_and_unknown():
    assert _resolve_pose_label("") == "action.idle"
    assert _resolve_pose_label(None) == "action.idle"
    assert _resolve_pose_label("contemplates the universe") == "action.idle"


def test_resolve_pose_label_passes_through_direct_label():
    assert _resolve_pose_label("action.heroic") == "action.heroic"


def test_resolve_expression_maps_aliases():
    assert _resolve_expression("joy") == "Happy"
    assert _resolve_expression("FURIOUS") == "Angry"
    assert _resolve_expression("about to cry") == "Sad"
    assert _resolve_expression("shock") == "Surprised"
    assert _resolve_expression("smug grin") == "Smirk"
    assert _resolve_expression("???") == "Neutral"


# ── camera merge ─────────────────────────────────────────────────────────


def test_camera_from_llm_uses_preset_when_parsed_is_none():
    cam = _camera_from_llm(None, "Closeup")
    assert cam["shot"] == "Closeup"
    assert cam["up"] == [0.0, 1.0, 0.0]
    assert "eye" in cam and len(cam["eye"]) == 3


def test_camera_from_llm_falls_back_when_shot_unknown():
    cam = _camera_from_llm({"shot": "TiltedRubicCube", "fov_deg": 33.0}, "MediumShot")
    # shot rejected, falls back to MediumShot preset; fov overridden by clamp.
    assert cam["shot"] == "MediumShot"
    assert abs(cam["fov_deg"] - 33.0) < 1e-6


def test_camera_from_llm_clamps_fov_and_roll():
    cam = _camera_from_llm({"shot": "Dutch", "fov_deg": 200.0, "roll_deg": 90.0}, "Dutch")
    assert cam["fov_deg"] == 85.0  # clamped
    assert cam["roll_deg"] == 25.0  # clamped


def test_camera_from_llm_accepts_dof():
    cam = _camera_from_llm(
        {"shot": "Closeup", "dof": {"focus_distance_m": 1.4, "aperture": 1.8}},
        "Closeup",
    )
    assert cam["dof"] == {"focus_distance_m": 1.4, "aperture": 1.8}


def test_camera_from_llm_rejects_malformed_dof():
    cam = _camera_from_llm(
        {"shot": "Closeup", "dof": {"focus_distance_m": "near", "aperture": "wide"}},
        "Closeup",
    )
    # Preset default is None for Closeup; malformed dof should not promote.
    assert cam.get("dof") is None


def test_camera_from_llm_validates_eye_target_up():
    cam = _camera_from_llm(
        {
            "shot": "FullShot",
            "eye": [1.0, 1.5, 4.0],
            "target": [0.0, 1.2, 0.0],
            "up": [0.0, 1.0, 0.0],
        },
        "FullShot",
    )
    assert cam["eye"] == [1.0, 1.5, 4.0]
    assert cam["target"] == [0.0, 1.2, 0.0]


def test_camera_from_llm_rejects_malformed_vec():
    cam = _camera_from_llm({"shot": "FullShot", "eye": [1.0, 2.0]}, "FullShot")
    # Malformed eye ignored; preset value preserved.
    assert len(cam["eye"]) == 3


# ── lights merge ─────────────────────────────────────────────────────────


def test_lights_from_llm_returns_none_when_empty():
    assert _lights_from_llm(None) is None
    assert _lights_from_llm({}) is None
    assert _lights_from_llm({"lights": []}) is None


def test_lights_from_llm_accepts_valid_set():
    parsed = {
        "lights": [
            {"role": "Key",  "direction": [-1.0, -0.5, 0.0], "colour": [1.0, 1.0, 1.0], "intensity": 3.5},
            {"role": "Fill", "direction": [ 1.0, -0.5, 0.0], "colour": [0.9, 0.95, 1.0], "intensity": 1.2},
        ]
    }
    out = _lights_from_llm(parsed)
    assert out is not None
    assert len(out) == 2
    assert out[0]["role"] == "Key"
    assert out[1]["intensity"] == 1.2


def test_lights_from_llm_accepts_color_spelling():
    parsed = {"lights": [{"role": "Rim", "direction": [0, 0, 1], "color": [1, 1, 1], "intensity": 2.0}]}
    out = _lights_from_llm(parsed)
    assert out is not None and out[0]["colour"] == [1.0, 1.0, 1.0]


def test_lights_from_llm_drops_malformed_entries():
    parsed = {
        "lights": [
            {"role": "Key", "direction": [0, 0, 1], "colour": [1, 1, 1], "intensity": 1.0},
            {"role": "NotARole", "direction": [0, 0, 1], "colour": [1, 1, 1], "intensity": 1.0},
            {"role": "Fill", "direction": [0, 0], "colour": [1, 1, 1], "intensity": 1.0},
        ]
    }
    out = _lights_from_llm(parsed)
    assert out is not None and len(out) == 1 and out[0]["role"] == "Key"


# ── camera variants ──────────────────────────────────────────────────────


def test_camera_variant_idx_zero_is_identity():
    base = {"eye": [0.0, 1.6, 3.0], "target": [0.0, 1.4, 0.0], "shot": "MediumShot"}
    cam = _camera_variant(base, 0)
    assert cam["eye"] == [0.0, 1.6, 3.0]


def test_camera_variant_yaw_swings_alt_angles():
    base = {"eye": [0.0, 1.6, 3.0], "target": [0.0, 1.4, 0.0]}
    a = _camera_variant(base, 1)
    b = _camera_variant(base, 2)
    # idx 1 and 2 should produce different eye x/z than each other and from base.
    assert a["eye"] != base["eye"]
    assert b["eye"] != base["eye"]
    assert a["eye"] != b["eye"]
    # y unchanged.
    assert a["eye"][1] == base["eye"][1]
    assert b["eye"][1] == base["eye"][1]


# ── B2 helpers ───────────────────────────────────────────────────────────


def test_blob_content_key_is_sha256_indexed():
    key_a = blob.content_key(b"hello")
    key_b = blob.content_key(b"hello")
    key_c = blob.content_key(b"world")
    assert key_a == key_b
    assert key_a != key_c
    assert key_a.startswith("blobs/anonymous/")
    assert len(key_a.split("/")[-1]) == 64  # sha256 hex


def test_blob_content_key_respects_prefix():
    key = blob.content_key(b"x", prefix="blobs/mangaka/scene3d")
    assert key.startswith("blobs/mangaka/scene3d/")


def test_blob_is_configured_reflects_env(monkeypatch):
    monkeypatch.setattr(blob, "_B2_KEY_ID", "")
    monkeypatch.setattr(blob, "_B2_KEY", "")
    assert blob.is_configured() is False
    monkeypatch.setattr(blob, "_B2_KEY_ID", "K")
    monkeypatch.setattr(blob, "_B2_KEY", "S")
    assert blob.is_configured() is True


def test_blob_put_raises_when_unconfigured(monkeypatch):
    monkeypatch.setattr(blob, "_B2_KEY_ID", "")
    monkeypatch.setattr(blob, "_B2_KEY", "")
    import pytest

    with pytest.raises(blob.B2NotConfigured):
        blob.put("blobs/anonymous/deadbeef", b"x")
