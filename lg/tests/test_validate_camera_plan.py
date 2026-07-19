"""P10.2 unit tests — `tool_validate_camera_plan`.

Pure-CPU. Covers the clamp/validate logic that gates the cinematography
LLM output before simulate_one + render_keyframes consume it. Mirrors the
acceptance rules from `lg_mangaka.tools.tool_validate_camera_plan` and
the in-tree wrapper `_camera_from_llm` / `_lights_from_llm`.
"""

from __future__ import annotations

import sys
from pathlib import Path

_LG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_LG_DIR))

import pytest

from lg_mangaka.tools import (
    _CAMERA_PRESETS,
    _DEFAULT_LIGHTS,
    tool_validate_camera_plan,
)


# ── empty / null payload ──────────────────────────────────────────────────


def test_validate_returns_preset_when_raw_is_none():
    out = tool_validate_camera_plan(camera_plan_raw=None)
    assert "camera_plan" in out
    cp = out["camera_plan"]
    assert cp["camera"]["shot"] == "MediumShot"
    assert cp["lights"] == _DEFAULT_LIGHTS
    # `llm: false` when the LLM didn't contribute anything.
    assert cp["llm"] is False


def test_validate_returns_preset_when_raw_is_empty_dict():
    out = tool_validate_camera_plan(camera_plan_raw={})
    cp = out["camera_plan"]
    assert cp["camera"]["shot"] == "MediumShot"
    assert cp["lights"] == _DEFAULT_LIGHTS
    # Empty {} is falsy → `llm: false`, same as None.
    assert cp["llm"] is False


# ── fallback shot routing ─────────────────────────────────────────────────


def test_validate_uses_fallback_shot_param():
    out = tool_validate_camera_plan(camera_plan_raw=None, fallback_shot="Closeup")
    cp = out["camera_plan"]
    assert cp["camera"]["shot"] == "Closeup"


def test_validate_falls_back_when_llm_shot_unknown():
    out = tool_validate_camera_plan(
        camera_plan_raw={"shot": "TiltedRubicCube"},
        fallback_shot="OverShoulder",
    )
    cp = out["camera_plan"]
    assert cp["camera"]["shot"] == "OverShoulder"
    # LLM contributed something even though the shot got rejected.
    assert cp["llm"] is True


def test_validate_accepts_llm_shot_when_in_enum():
    out = tool_validate_camera_plan(
        camera_plan_raw={"shot": "WormsEye"},
        fallback_shot="MediumShot",
    )
    cp = out["camera_plan"]
    assert cp["camera"]["shot"] == "WormsEye"


# ── numeric clamping ──────────────────────────────────────────────────────


def test_validate_clamps_fov_out_of_range():
    out = tool_validate_camera_plan(
        camera_plan_raw={"shot": "Closeup", "fov_deg": 200.0},
    )
    assert out["camera_plan"]["camera"]["fov_deg"] == 85.0


def test_validate_clamps_fov_negative():
    out = tool_validate_camera_plan(
        camera_plan_raw={"shot": "Closeup", "fov_deg": -10.0},
    )
    assert out["camera_plan"]["camera"]["fov_deg"] == 15.0


def test_validate_clamps_roll_to_dutch_safe_range():
    out = tool_validate_camera_plan(
        camera_plan_raw={"shot": "Dutch", "roll_deg": 90.0},
    )
    assert out["camera_plan"]["camera"]["roll_deg"] == 25.0


# ── vector validation ────────────────────────────────────────────────────


def test_validate_accepts_well_formed_vectors():
    out = tool_validate_camera_plan(
        camera_plan_raw={
            "shot": "FullShot",
            "eye": [1.0, 1.5, 4.0],
            "target": [0.0, 1.2, 0.0],
            "up": [0.0, 1.0, 0.0],
        },
    )
    cam = out["camera_plan"]["camera"]
    assert cam["eye"] == [1.0, 1.5, 4.0]
    assert cam["target"] == [0.0, 1.2, 0.0]


def test_validate_rejects_two_element_vector():
    out = tool_validate_camera_plan(
        camera_plan_raw={"shot": "FullShot", "eye": [1.0, 2.0]},
    )
    # Malformed eye → preset preserved.
    eye = out["camera_plan"]["camera"]["eye"]
    assert len(eye) == 3


# ── DoF ───────────────────────────────────────────────────────────────────


def test_validate_accepts_dof_dict():
    out = tool_validate_camera_plan(
        camera_plan_raw={
            "shot": "Closeup",
            "dof": {"focus_distance_m": 1.4, "aperture": 1.8},
        },
    )
    assert out["camera_plan"]["camera"]["dof"] == {
        "focus_distance_m": 1.4,
        "aperture": 1.8,
    }


def test_validate_rejects_malformed_dof():
    out = tool_validate_camera_plan(
        camera_plan_raw={
            "shot": "Closeup",
            "dof": {"focus_distance_m": "near", "aperture": "wide"},
        },
    )
    assert out["camera_plan"]["camera"].get("dof") is None


# ── lights ────────────────────────────────────────────────────────────────


def test_validate_accepts_well_formed_3_point_lights():
    parsed_lights = [
        {"role": "Key",  "direction": [-1.0, -0.5, 0.0], "colour": [1.0, 1.0, 1.0], "intensity": 3.5},
        {"role": "Fill", "direction": [ 1.0, -0.5, 0.0], "colour": [0.9, 0.95, 1.0], "intensity": 1.2},
        {"role": "Rim",  "direction": [ 0.1, -0.2, 0.95],"colour": [1.0, 1.0, 1.0], "intensity": 2.0},
    ]
    out = tool_validate_camera_plan(
        camera_plan_raw={"shot": "MediumShot", "lights": parsed_lights},
    )
    assert len(out["camera_plan"]["lights"]) == 3
    roles = [l["role"] for l in out["camera_plan"]["lights"]]
    assert roles == ["Key", "Fill", "Rim"]


def test_validate_accepts_color_spelling():
    parsed_lights = [
        {"role": "Rim", "direction": [0, 0, 1], "color": [1, 1, 1], "intensity": 2.0},
    ]
    out = tool_validate_camera_plan(
        camera_plan_raw={"shot": "MediumShot", "lights": parsed_lights},
    )
    assert out["camera_plan"]["lights"][0]["colour"] == [1.0, 1.0, 1.0]


def test_validate_falls_back_to_default_lights_on_malformed():
    parsed_lights = [
        {"role": "NotARole", "direction": [0, 0, 1], "colour": [1, 1, 1], "intensity": 1.0},
        {"role": "Fill", "direction": [0, 0], "colour": [1, 1, 1], "intensity": 1.0},
    ]
    out = tool_validate_camera_plan(
        camera_plan_raw={"shot": "MediumShot", "lights": parsed_lights},
    )
    # Mixed valid/invalid → only valid entries pass; if all invalid → default.
    # In this case both are invalid, so we get the 3-point default.
    assert out["camera_plan"]["lights"] == _DEFAULT_LIGHTS


def test_validate_falls_back_to_default_lights_when_omitted():
    out = tool_validate_camera_plan(
        camera_plan_raw={"shot": "MediumShot"},
    )
    assert out["camera_plan"]["lights"] == _DEFAULT_LIGHTS


# ── output shape contract ────────────────────────────────────────────────


def test_validate_output_shape_matches_lexicon():
    """Lexicon validateCameraPlan declares output as {cameraPlan: {camera, lights, llm}}.
    The dispatcher (server.py P9) snake→camel converts the top-level key, so
    here the body uses `camera_plan` and the wire shape is `cameraPlan`."""
    out = tool_validate_camera_plan(camera_plan_raw={"shot": "FullShot"})
    assert set(out.keys()) == {"camera_plan"}
    cp = out["camera_plan"]
    assert set(cp.keys()) >= {"camera", "lights", "llm"}


# ── camera preset coverage ───────────────────────────────────────────────


def test_every_manga_grammar_shot_has_a_preset():
    expected = {
        "FullShot", "MediumShot", "Closeup",
        "OverShoulder", "Dutch", "BirdsEye", "WormsEye",
    }
    assert set(_CAMERA_PRESETS.keys()) == expected
