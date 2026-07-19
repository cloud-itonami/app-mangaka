"""LLM-prompt drift guard between `compose_scene_3d.topology.yaml` and the
Phase A Python source constants.

Phase C blocker #1 closure: the 3 LLM nodes (`pose_characters`,
`cinematography`, `critique_and_select`) advertise inlined `args.system` +
`args.user_template` so `kotodama.langgraph_node_resolvers.make_llm_node`
can consume them directly (no `prompt:`/`lex:` dereferencing needed).

Until the resolver flips to the topology path at deployment, both sources
must stay in lockstep — if a maintainer tweaks `_CINEMATOGRAPHY_SYSTEM` /
`_VISION_SYSTEM` in `compose_scene_3d.py` without updating the YAML, the
behaviour drifts silently between Phase A (runtime today) and Phase C
(runtime after flip). This test fails in that window.

Comparison is content-anchored rather than text-equivalent because the
YAML representation drops parenthesised type hints and reformats the
field/axis table for block-style readability. The anchors are the
identifying tokens — axis names, field schema keys, output schema keys,
and the "Return ONLY ..." invariant — which any prompt rewrite that
preserves semantics must keep.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

_LG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_LG_DIR))

_TOPOLOGY_PATH = (
    _LG_DIR / "lg_mangaka" / "graphs" / "compose_scene_3d.topology.yaml"
)


@pytest.fixture(scope="module")
def topology() -> dict:
    return yaml.safe_load(_TOPOLOGY_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def llm_nodes(topology: dict) -> dict[str, dict]:
    """Both `llm` (text) and `llm_vision` (multimodal) kinds carry inlined
    prompts via the same `args.system` / `args.user_template` contract —
    pick up both so the drift guard catches a `kind: llm → kind: llm_vision`
    flip without losing critique coverage."""
    return {
        n["id"]: n for n in topology["nodes"] if n.get("kind") in ("llm", "llm_vision")
    }


def _norm(s: str) -> str:
    """Collapse runs of whitespace (incl. newlines) to single spaces for
    substring tolerance against YAML-reformatted prompts."""
    return re.sub(r"\s+", " ", s or "").strip()


# ── structural ────────────────────────────────────────────────────────────


def test_all_llm_nodes_have_inlined_args(llm_nodes: dict[str, dict]) -> None:
    """All 3 LLM nodes must carry `args.system` + `args.user_template`
    matching the `make_llm_node` resolver contract."""
    expected = {"pose_characters", "cinematography", "critique_and_select"}
    assert set(llm_nodes) == expected, sorted(llm_nodes)
    for node_id, node in llm_nodes.items():
        cfg = node.get("config") or {}
        args = cfg.get("args") or {}
        assert isinstance(args.get("system"), str) and args["system"].strip(), (
            f"{node_id}: missing args.system"
        )
        assert isinstance(args.get("user_template"), str) and args["user_template"].strip(), (
            f"{node_id}: missing args.user_template"
        )
        # result_key is required by make_llm_node — its absence raises at
        # node-compile time, but better to catch it here.
        assert cfg.get("result_key"), f"{node_id}: missing config.result_key"


def test_llm_input_keys_are_subset_of_state_keys(topology: dict, llm_nodes: dict[str, dict]) -> None:
    state_keys = set(topology.get("state_keys") or [])
    for node_id, node in llm_nodes.items():
        ik = node.get("config", {}).get("input_keys") or []
        missing = set(ik) - state_keys
        assert not missing, (
            f"{node_id}: input_keys {sorted(missing)} not declared in state_keys"
        )


def test_no_unresolved_prompt_or_schema_refs_at_args_level(llm_nodes: dict[str, dict]) -> None:
    """`prompt_ref` / `response_schema_ref` MAY appear at config level as
    forward-looking documentation, but never inside `args` — `make_llm_node`
    does not dereference those keys, so a typo there would silently nullify
    the resolver."""
    for node_id, node in llm_nodes.items():
        args = (node.get("config") or {}).get("args") or {}
        for ghost in ("prompt_ref", "response_schema_ref"):
            assert ghost not in args, (
                f"{node_id}.args.{ghost}: stale prompt-ref leaked into args namespace"
            )


# ── content-anchor drift guard: cinematography ────────────────────────────


def test_cinematography_system_prompt_anchors(llm_nodes: dict[str, dict]) -> None:
    cfg = llm_nodes["cinematography"]["config"]
    system = _norm(cfg["args"]["system"])
    # Role + manga-grammar anchor
    assert "manga storyboard cinematographer" in system
    assert "Japanese manga grammar" in system or "manga grammar" in system
    # Shot-type vocabulary must be exhaustive — drift here means the
    # validator-side `_camera_from_llm` will start receiving unmapped tags.
    for shot in ("FullShot", "MediumShot", "Closeup", "OverShoulder",
                 "Dutch", "BirdsEye", "WormsEye"):
        assert shot in system, f"missing shot tag: {shot}"
    # Field schema keys — must match `_camera_from_llm` consumer.
    for field in ("shot", "eye", "target", "up", "fov_deg", "roll_deg", "dof", "lights"):
        assert field in system, f"missing camera field: {field}"
    # Lighting role vocabulary — must match `_lights_from_llm` consumer.
    for role in ("Key", "Fill", "Rim"):
        assert role in system, f"missing lighting role: {role}"
    assert "Return ONLY" in system or "ONLY the JSON" in system


def test_cinematography_runtime_temperature_matches_phase_a(llm_nodes: dict[str, dict]) -> None:
    """Phase A `_step_cinematography` uses temperature=0.4 on iteration 0
    (and 0.6 on refine). Phase C inlines a single temperature; document the
    baseline so the YAML stays the migration target for iteration 0."""
    args = llm_nodes["cinematography"]["config"]["args"]
    assert float(args["temperature"]) == pytest.approx(0.4)
    assert int(args["max_tokens"]) == 512


# ── content-anchor drift guard: critique_and_select ───────────────────────


_VISION_AXES = (
    "composition",
    "silhouette",
    "characterRecognizability",
    "framing",
    "mangaShotGrammar",
    "lightingDrama",
    "actionClarity",
    "emotionAlignment",
)


def test_critique_system_prompt_lists_all_eight_axes(llm_nodes: dict[str, dict]) -> None:
    system = _norm(llm_nodes["critique_and_select"]["config"]["args"]["system"])
    for axis in _VISION_AXES:
        assert axis in system, f"critique prompt missing axis: {axis}"


def test_critique_system_prompt_keeps_acceptance_bar_invariants(llm_nodes: dict[str, dict]) -> None:
    """The 0.75 acceptance bar drives the DMN refinement policy
    (`composeScene3dRefinement.dmn`); the prompt must keep the same bar
    advertised to the LLM. 0.5 baseline is doc-only but easy to keep."""
    system = _norm(llm_nodes["critique_and_select"]["config"]["args"]["system"])
    assert "0.75" in system
    assert "0.5" in system
    # Output schema: notes + improvements are how the cinematographer
    # iteration loop picks up refinement directives.
    assert "notes" in system
    assert "improvements" in system
    assert "Return ONLY" in system or "JSON object" in system


def test_critique_axes_align_with_python_vision_axes() -> None:
    """The canonical 8-axis list in `compose_scene_3d._VISION_AXES` must
    be the same tuple as the one this test enforces against the topology —
    otherwise we lose end-to-end coverage of a new axis."""
    try:
        from lg_mangaka.graphs.compose_scene_3d import _VISION_AXES as PY_AXES
    except Exception:
        pytest.skip("compose_scene_3d not importable (langgraph dep missing in env)")
    assert PY_AXES == _VISION_AXES


def test_critique_runtime_temperature_matches_phase_a(llm_nodes: dict[str, dict]) -> None:
    """Phase A `_score_one_render` calls `llm_vision_score(... max_tokens=384)`
    with default low temperature (the helper defaults to 0.1). Pin the
    same in the inlined args."""
    args = llm_nodes["critique_and_select"]["config"]["args"]
    assert int(args["max_tokens"]) == 384
    assert float(args["temperature"]) == pytest.approx(0.1)


# ── content-anchor drift guard: pose_characters ───────────────────────────


def test_pose_system_prompt_carries_pose_lexicon_action_labels(llm_nodes: dict[str, dict]) -> None:
    """`kami_mangaka_scene::lexicon::pose_preset` accepts `action.*` labels.
    The pose prompt must instruct the LLM to emit those labels — otherwise
    Phase C simulate_one rejects the pose plan."""
    system = _norm(llm_nodes["pose_characters"]["config"]["args"]["system"])
    # action.* lexicon — must include both the example labels and the
    # idle fallback used for non-focal characters.
    for label in ("action.dash", "action.swing", "action.shout", "action.idle"):
        assert label in system, f"pose prompt missing action label: {label}"
    # ARKit expression vocabulary — kept narrow so the
    # `pose_preset → ARKit expression` mapping stays exhaustive.
    for expr in ("Happy", "Angry", "Sad", "Surprised", "Determined",
                 "Pained", "Smirk", "Neutral"):
        assert expr in system, f"pose prompt missing expression: {expr}"


# ── critique vision-plumbing CAVEAT is documented but result_key still set ─


def test_critique_documents_vision_plumbing_subblocker() -> None:
    """The vision sub-blocker (P10.1b — make_llm_vision_node) must remain
    explicitly documented in the YAML so a maintainer flipping the
    deployment doesn't accidentally route critique through text-only
    `make_llm_node` and get a degraded critic."""
    raw = _TOPOLOGY_PATH.read_text(encoding="utf-8")
    assert "P10.1b" in raw, "vision sub-blocker tag P10.1b missing from YAML docs"
    assert "make_llm_vision_node" in raw
    # The `vision` ref tier signals the gpt-4o-mini-vision route.
    topology = yaml.safe_load(raw)
    crit = next(n for n in topology["nodes"] if n["id"] == "critique_and_select")
    assert crit["ref"] == "vision"
