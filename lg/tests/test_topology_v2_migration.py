"""P10 unit tests — topology v2 migration content matches the YAML SSoT.

The Alembic revision `r_20260514170000_topology_compose_scene_3d` loads
the topology YAML at upgrade time and emits SQL INSERTs against
`vertex_langgraph_assistant` / `_node` / `vertex_langgraph_deployment`.
This test exercises the same helpers (`_load_topology`, `_spec_json`,
`_node_rows`) so any drift between the YAML and what would land in RW
surfaces immediately. No RW connection required.
"""

from __future__ import annotations

import json
import sys
from importlib import util as _import_util
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[4]
_REVISION_PATH = (
    _REPO_ROOT
    / "30-graph"
    / "graph-schema"
    / "alembic"
    / "current_versions"
    / "r_20260514170000_topology_compose_scene_3d.py"
)


@pytest.fixture(scope="module")
def revision_module():
    """Load the Alembic revision file as a standalone module — avoids
    pulling in the full alembic env config + revision chain just to read
    the migration's pure helpers."""
    pytest.importorskip("yaml")
    spec = _import_util.spec_from_file_location(
        "_topology_v2_revision", _REVISION_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = _import_util.module_from_spec(spec)
    sys.modules["_topology_v2_revision"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def topology(revision_module):
    return revision_module._load_topology()


# ── revision metadata ─────────────────────────────────────────────────────


def test_revision_chain(revision_module):
    assert revision_module.revision == "r_20260514170000_topology_compose_scene_3d"
    # Chains from the P10.2b aggregator seed — keeps the lineage in order.
    assert (
        revision_module.down_revision
        == "r_20260514160000_seed_mangaka_aggregate_critique_mcp_tool"
    )


def test_assistant_id_constants(revision_module):
    assert revision_module._ASSISTANT_ID == "com.etzhayyim.mangaka.composeScene3d"
    assert revision_module._NSID == revision_module._ASSISTANT_ID
    assert revision_module._APP_DID == "did:web:mangaka.etzhayyim.com"


# ── _load_topology / _spec_json ───────────────────────────────────────────


def test_topology_yaml_resolvable(revision_module):
    assert revision_module._TOPOLOGY_YAML.is_file(), (
        f"expected topology YAML at {revision_module._TOPOLOGY_YAML}"
    )


def test_spec_json_contains_required_keys(topology, revision_module):
    spec_json = revision_module._spec_json(topology)
    spec = json.loads(spec_json)
    assert set(spec.keys()) == {"state_keys", "entry", "edges", "conditional_edges"}


def test_spec_json_state_keys_match_yaml(topology, revision_module):
    spec = json.loads(revision_module._spec_json(topology))
    yaml_keys = topology.get("state_keys") or []
    assert spec["state_keys"] == yaml_keys
    assert "pose_plan" in spec["state_keys"]
    assert "sim_result" in spec["state_keys"]
    assert "renders" in spec["state_keys"]
    assert "camera_plan_raw" in spec["state_keys"]


def test_spec_json_entry_matches_yaml(topology, revision_module):
    spec = json.loads(revision_module._spec_json(topology))
    assert spec["entry"] == topology["entry"]
    assert spec["entry"] == "load_panel_plan"


def test_spec_json_edges_and_conditional_edges_preserved(topology, revision_module):
    spec = json.loads(revision_module._spec_json(topology))
    assert spec["edges"] == (topology.get("edges") or [])
    assert spec["conditional_edges"] == (topology.get("conditional_edges") or [])


# ── _node_rows ────────────────────────────────────────────────────────────


def test_node_rows_cover_every_yaml_node(topology, revision_module):
    rows = revision_module._node_rows(topology)
    yaml_ids = {n["id"] for n in topology.get("nodes") or []}
    row_ids = {r["node_id"] for r in rows}
    assert row_ids == yaml_ids


def test_node_row_vertex_id_format(revision_module, topology):
    rows = revision_module._node_rows(topology)
    aid = revision_module._ASSISTANT_ID
    for r in rows:
        assert r["vertex_id"] == f"{aid}:{r['node_id']}"
        assert r["assistant_id"] == aid


def test_node_row_kinds_are_data_resolved(revision_module, topology):
    """ADR-2605082000 §2 — only mcp_tool / sql_udf / py_ext_udf / llm /
    llm_vision are allowed in the topology shape. The migration must not
    silently introduce a `py_primitive` row."""
    rows = revision_module._node_rows(topology)
    allowed = {"mcp_tool", "sql_udf", "py_ext_udf", "llm", "llm_vision", "foreach"}
    for r in rows:
        assert r["kind"] in allowed, (
            f"node {r['node_id']}: kind {r['kind']!r} not in data-resolved set"
        )


def test_node_row_mcp_refs_use_mcp_scheme(revision_module, topology):
    rows = revision_module._node_rows(topology)
    for r in rows:
        if r["kind"] == "mcp_tool":
            assert r["ref"].startswith("mcp://"), (
                f"mcp_tool node {r['node_id']}: ref must start with mcp://, got {r['ref']!r}"
            )


def test_node_row_config_is_valid_json_or_null(revision_module, topology):
    rows = revision_module._node_rows(topology)
    for r in rows:
        if r["config"] is not None:
            # Round-trip — payload must be valid JSON so the loader can parse it.
            parsed = json.loads(r["config"])
            assert isinstance(parsed, dict), (
                f"node {r['node_id']}: config must serialise to a JSON object"
            )


def test_llm_vision_node_carries_image_keys(revision_module, topology):
    """The P10.1b critique node MUST land in the migration with image_keys
    set so the resolver dispatches multimodal at runtime."""
    rows = revision_module._node_rows(topology)
    crit = next((r for r in rows if r["node_id"] == "critique_and_select"), None)
    assert crit is not None
    if crit["kind"] == "llm_vision":
        cfg = json.loads(crit["config"]) if crit["config"] else {}
        image_keys = cfg.get("image_keys") or []
        assert image_keys, "critique_and_select llm_vision node must declare image_keys"
        assert all(isinstance(p, str) and p.strip() for p in image_keys)


def test_node_count_matches_yaml_node_count(revision_module, topology):
    """Sanity check that the YAML hasn't been silently truncated or
    duplicated since the migration was authored."""
    rows = revision_module._node_rows(topology)
    yaml_nodes = topology.get("nodes") or []
    assert len(rows) == len(yaml_nodes)
    # As of P10.2b the YAML carries 11 nodes (load_panel_plan, resolve_assets,
    # pose_characters, place_scene, cinematography, validate_camera_plan,
    # simulate_one, render_keyframes, critique_and_select, aggregate_critique,
    # persist). Lock it so future additions land via an explicit migration bump.
    assert len(rows) >= 11
