"""Phase A ↔ Phase C parity guard for the cinematography + critique
post-processors.

P10.2 / P10.2b factored the post-LLM validators out of `compose_scene_3d.py`
into `lg_mangaka.tools` so the in-tree Pregel (Phase A) and the data-driven
topology (Phase C) consume the same SSoT:

  • `tool_validate_camera_plan`  — replaces inline `_camera_from_llm` +
    `_lights_from_llm` after the cinematography LLM.
  • `tool_aggregate_critique`    — replaces the per-render axes-clamp +
    Hume `emotionAlignment` overlay + best-of-N selection after the
    vision critic.

These tests fail when either path drifts from the other so a maintainer
fixing a clamp / overlay rule in one place can't silently degrade the
other. Pure-CPU — Hume + B2 are pinned, LLM is not invoked.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_LG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_LG_DIR))

from lg_mangaka import tools as tools_mod
from lg_mangaka.graphs import compose_scene_3d as csd


def _run(coro):
    return asyncio.run(coro)


# ── camera-plan parity ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,fallback_shot",
    [
        (None, "MediumShot"),
        ({}, "Closeup"),
        ({"shot": "TiltedRubicCube", "fov_deg": 200.0}, "MediumShot"),  # malformed shot
        (
            {
                "shot": "Dutch",
                "eye": [0.0, 1.65, 2.4],
                "target": [0.0, 1.5, 0.0],
                "up": [0.0, 1.0, 0.0],
                "fov_deg": 38.0,
                "roll_deg": 22.0,
                "dof": {"focus_distance_m": 1.6, "aperture": 1.8},
                "lights": [
                    {"role": "Key",  "direction": [-0.5, -0.7, -0.4], "colour": [1.0, 0.94, 0.9],   "intensity": 4.2},
                    {"role": "Fill", "direction": [0.5, -0.3, -0.2],  "colour": [0.84, 0.93, 1.0], "intensity": 1.6},
                    {"role": "Rim",  "direction": [0.1, -0.2, 0.95],  "colour": [1.0, 1.0, 1.0],   "intensity": 2.1},
                ],
            },
            "Dutch",
        ),
        (
            {"shot": "FullShot", "eye": [1.0, 2.0]},  # malformed eye, should fall back to preset
            "FullShot",
        ),
        (
            # rejected light entries — wrong role + missing colour → default
            # 3-point lighting kicks in.
            {"shot": "BirdsEye", "lights": [{"role": "Spot", "direction": [0, 0, 1]}]},
            "BirdsEye",
        ),
    ],
)
def test_camera_plan_parity(raw, fallback_shot) -> None:
    """`tool_validate_camera_plan(raw, fallback_shot)` and the Phase A
    cinematography step (mocked LLM returning `raw`) must produce the
    same `camera_plan` channel value."""

    tool_out = tools_mod.tool_validate_camera_plan(
        camera_plan_raw=raw, fallback_shot=fallback_shot
    )["camera_plan"]

    # Drive Phase A's `_step_cinematography` by short-circuiting the LLM
    # call so it returns `raw` verbatim.
    async def _fake_llm_json(system, user, **_kw):
        return raw

    # Use a context-local monkeypatch via attribute assignment + restore.
    saved = csd._llm.llm_json
    try:
        csd._llm.llm_json = _fake_llm_json
        state = {
            "panel_plan": {"shot": fallback_shot},
            "iteration": 0,
        }
        phase_a = _run(csd._step_cinematography(state))["camera_plan"]
    finally:
        csd._llm.llm_json = saved

    assert phase_a == tool_out, (
        f"camera_plan drift: phase_a={phase_a!r} vs tool={tool_out!r}"
    )


# ── critique-aggregation parity ───────────────────────────────────────────


def _hume_stub(score: float, family: str, primary_name: str = "joy"):
    """Returns a deterministic (score, evidence) the same way Phase A
    `_score_one_render` would receive from `score_emotion_alignment`."""
    def _fn(png, mood):
        return (
            score,
            {
                "family": family,
                "primary": {"name": primary_name, "score": score},
                "topEmotions": [{"name": primary_name, "score": score}],
                "imageFeatures": {"luminance": 0.5},
                "algorithm": "visual_heuristic_v1",
                "source": "hume_image_head",
            },
        )

    return _fn


def test_critique_aggregate_parity_for_real_blobs(monkeypatch) -> None:
    """For 3 renders with real (non-pending) blob keys + LLM axes already
    written on each render's `.critique.axes`, both paths must:
      • clamp + fill the same axes,
      • overlay the same Hume `emotionAlignment` score,
      • pick the same `selected` render and aggregate `score`.
    """
    monkeypatch.setattr(csd._blob, "is_configured", lambda: True)
    monkeypatch.setattr(csd._blob, "get", lambda key, **_: b"\x89PNG-fake")
    monkeypatch.setattr(csd._hume, "score_emotion_alignment", _hume_stub(0.7, "joy"))
    monkeypatch.setattr(tools_mod._blob if hasattr(tools_mod, "_blob") else csd._blob,  # type: ignore[attr-defined]
                        "is_configured", lambda: True)

    # The tool imports `lg_mangaka.blob` + `lg_mangaka.hume_emotion` lazily
    # at call time. Patch them at module scope so both paths agree.
    from lg_mangaka import blob as blob_mod
    from lg_mangaka import hume_emotion as hume_mod
    monkeypatch.setattr(blob_mod, "is_configured", lambda: True)
    monkeypatch.setattr(blob_mod, "get", lambda key, **_: b"\x89PNG-fake")
    monkeypatch.setattr(hume_mod, "score_emotion_alignment", _hume_stub(0.7, "joy"))

    # Pin the LLM critic so Phase A produces deterministic axes.
    async def fake_vision(*a, **k):
        return {ax: 0.8 for ax in csd._VISION_AXES}

    monkeypatch.setattr(csd._llm, "llm_vision_score", fake_vision)

    state = {
        "renders": [
            {"blobKey": "blobs/anonymous/aaa", "angle": "FullShot"},
            {"blobKey": "blobs/anonymous/bbb", "angle": "Closeup"},
            {"blobKey": "blobs/anonymous/ccc", "angle": "OverShoulder"},
        ],
        "panel_plan": {"shot": "FullShot", "mood": "triumph"},
        "camera_plan": {"camera": {"shot": "FullShot"}},
    }

    phase_a_out = _run(csd._step_critique_and_select(state))

    # Build the equivalent Phase C input — renders already carry the
    # raw LLM axes under .critique.axes (the shape `tool_aggregate_critique`
    # expects after a future foreach + llm_vision node lands).
    seed_renders = []
    for r in state["renders"]:
        seed_renders.append({
            **r,
            "critique": {
                "axes": {ax: 0.8 for ax in csd._VISION_AXES},
                "notes": None,
            },
        })
    phase_c_out = tools_mod.tool_aggregate_critique(
        renders=seed_renders, target_mood="triumph",
    )

    # The two paths agree on aggregate score (to numeric tolerance) and on
    # which render they pick.
    assert phase_c_out["selected"]["blobKey"] == phase_a_out["selected"]["blobKey"]
    assert phase_c_out["score"] == pytest.approx(phase_a_out["score"], abs=1e-9)
    # And the per-render scores match render-by-render.
    a_by_blob = {r["blobKey"]: r["score"] for r in phase_a_out["renders"]}
    c_by_blob = {r["blobKey"]: r["score"] for r in phase_c_out["renders"]}
    assert a_by_blob == c_by_blob, (a_by_blob, c_by_blob)


def test_critique_aggregate_parity_for_placeholder_blobs(monkeypatch) -> None:
    """`pending-*` renders carry no real blob — Phase A bypasses LLM + Hume
    and returns the fallback score. The tool must mirror that exactly when
    handed a render with no `critique` field."""
    monkeypatch.setattr(csd, "_FALLBACK_SCORE", 0.6)

    state = {
        "renders": [
            {"blobKey": "pending-foo-i1-a0", "angle": "FullShot"},
            {"blobKey": "pending-foo-i1-a1", "angle": "Closeup"},
        ],
        "panel_plan": {"shot": "FullShot", "mood": "calm"},
        "camera_plan": {"camera": {"shot": "FullShot"}},
    }
    phase_a_out = _run(csd._step_critique_and_select(state))

    seed_renders = [{**r} for r in state["renders"]]  # no .critique
    phase_c_out = tools_mod.tool_aggregate_critique(
        renders=seed_renders, target_mood="calm", fallback_score=0.6,
    )

    assert phase_c_out["score"] == pytest.approx(phase_a_out["score"], abs=1e-9)
    assert all(r.get("score") == 0.6 for r in phase_c_out["renders"])


# ── topology shape sanity ─────────────────────────────────────────────────


def test_topology_wires_both_post_processors_after_their_llm_nodes() -> None:
    """Catch wiring drift early: cinematography must feed validate_camera_plan
    before simulate_one fan-out, and critique_and_select must feed
    aggregate_critique before the DMN refinement edge."""
    import yaml as _yaml
    raw = (_LG_DIR / "lg_mangaka" / "graphs" / "compose_scene_3d.topology.yaml").read_text(
        encoding="utf-8"
    )
    spec = _yaml.safe_load(raw)

    node_ids = {n["id"] for n in spec["nodes"]}
    assert {"validate_camera_plan", "aggregate_critique"} <= node_ids

    # validate_camera_plan must be on a linear edge from cinematography.
    edges = spec.get("edges") or []
    edge_pairs = {(e["from"], e["to"]) for e in edges}
    assert ("cinematography", "validate_camera_plan") in edge_pairs
    # aggregate_critique must consume critique_and_select output.
    assert ("critique_and_select", "aggregate_critique") in edge_pairs

    # Send fan-out should originate from validate_camera_plan (not from
    # cinematography directly anymore) so the validated camera plan is in
    # state before any per-character render dispatches.
    sends = [ce for ce in (spec.get("conditional_edges") or [])
             if ce.get("router") == "send_fanout"]
    assert any(ce["from"] == "validate_camera_plan" for ce in sends), (
        "send_fanout should originate from validate_camera_plan post-P10.2"
    )

    # DMN refinement edge originates from the aggregator, not the LLM node.
    refine = [ce for ce in (spec.get("conditional_edges") or [])
              if ce.get("condition_ref", "").startswith("dmn:")]
    assert refine, "DMN refinement conditional_edge missing"
    assert refine[0]["from"] == "aggregate_critique", (
        f"refinement edge should originate from aggregate_critique, "
        f"got {refine[0]['from']!r}"
    )
