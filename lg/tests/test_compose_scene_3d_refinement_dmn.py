"""Structural validator for the `composeScene3dRefinement` DMN policy.

This test enforces consistency across three SSoT artifacts that must agree
for Phase C activation:

  1. The DMN XML at `00-contracts/dmn/com/etzhayyim/policies/mangaka/composeScene3dRefinement.dmn`
  2. The seed migration at `30-graph/graph-schema/sql_migrations/20260514150000_seed_mangaka_compose_scene_3d_refinement_dmn.up.sql`
     (decision_key + version + structured rules_json + embedded dmn_xml)
  3. The Phase A in-tree Python predicate `compose_scene_3d._route_after_critique`

And consistency with the consumer:

  4. The topology YAML's `condition_ref` value points at the same decision_key.

Behavioural equivalence between the DMN rules table and the Python predicate
is asserted at the score boundary (0.75) and the iteration budget boundary
(iteration < max_iter), since FIRST hit-policy + the catch-all rule
guarantees a defined output for every (score, iteration, max_iter).

Pure-CPU, offline — no DB, no DMN evaluator, no LangGraph.
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

_LG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_LG_DIR))

# `_LG_DIR = <repo>/60-apps/etzhayyim-project-mangaka/lg`, so repo is 3 up.
_REPO_ROOT = _LG_DIR.parents[2]
_DMN_PATH = (
    _REPO_ROOT
    / "00-contracts" / "dmn" / "com" / "etzhayyim" / "policies" / "mangaka"
    / "composeScene3dRefinement.dmn"
)
_MIGRATION_PATH = (
    _REPO_ROOT
    / "30-graph" / "graph-schema" / "sql_migrations"
    / "20260514150000_seed_mangaka_compose_scene_3d_refinement_dmn.up.sql"
)
_TOPOLOGY_PATH = (
    _LG_DIR / "lg_mangaka" / "graphs" / "compose_scene_3d.topology.yaml"
)
_DMN_NS = {"dmn": "https://www.omg.org/spec/DMN/20191111/MODEL/"}
_DECISION_KEY = "com.etzhayyim.policies.mangaka.composeScene3dRefinement"
_DECISION_VERSION = 1


# ── DMN file ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def dmn_tree() -> ET.ElementTree:
    assert _DMN_PATH.is_file(), f"missing DMN file: {_DMN_PATH}"
    return ET.parse(_DMN_PATH)


def test_dmn_file_parses_as_valid_xml(dmn_tree: ET.ElementTree) -> None:
    root = dmn_tree.getroot()
    assert root.tag.endswith("definitions")


def test_dmn_decision_key_matches_topology_condition_ref(dmn_tree: ET.ElementTree) -> None:
    """The topology YAML's `condition_ref: dmn:<key>@<version>` must point at
    the DMN's `<decision id=...>`."""
    decisions = dmn_tree.getroot().findall("dmn:decision", _DMN_NS)
    assert len(decisions) == 1
    assert decisions[0].get("id") == _DECISION_KEY

    topology = yaml.safe_load(_TOPOLOGY_PATH.read_text(encoding="utf-8"))
    refs = [
        ce.get("condition_ref")
        for ce in (topology.get("conditional_edges") or [])
        if ce.get("condition_ref")
    ]
    expected = f"dmn:{_DECISION_KEY}@{_DECISION_VERSION}.0.0"
    assert expected in refs, (
        f"topology condition_ref {refs} does not include {expected!r}"
    )


def test_dmn_hit_policy_is_first(dmn_tree: ET.ElementTree) -> None:
    """FIRST hit policy + catch-all rule = total function. Any other hit
    policy would change the semantics relative to `_route_after_critique`."""
    table = dmn_tree.getroot().find(".//dmn:decisionTable", _DMN_NS)
    assert table is not None
    assert table.get("hitPolicy") == "FIRST"


def test_dmn_inputs_match_python_predicate(dmn_tree: ET.ElementTree) -> None:
    """The Python predicate reads `score` / `iteration` / `max_iter`; the
    DMN exposes those as `score` / `iteration` / `maxIter` (camelCase per
    the topology naming conventions)."""
    table = dmn_tree.getroot().find(".//dmn:decisionTable", _DMN_NS)
    assert table is not None
    input_exprs = [
        ie.find("dmn:text", _DMN_NS).text
        for ie in table.findall("dmn:input/dmn:inputExpression", _DMN_NS)
    ]
    assert input_exprs == ["score", "iteration", "maxIter"], input_exprs


def test_dmn_output_is_route_and_reason(dmn_tree: ET.ElementTree) -> None:
    table = dmn_tree.getroot().find(".//dmn:decisionTable", _DMN_NS)
    outputs = [o.get("name") for o in table.findall("dmn:output", _DMN_NS)]
    assert outputs == ["route", "reason"]


def test_dmn_has_refine_and_persist_rules_in_order(dmn_tree: ET.ElementTree) -> None:
    """Rule order matters under FIRST: the refine rule must come before the
    catch-all so the predicate fires whenever it matches."""
    rules = dmn_tree.getroot().findall(".//dmn:rule", _DMN_NS)
    assert len(rules) == 2
    outputs_by_rule = []
    for r in rules:
        oe_texts = [oe.find("dmn:text", _DMN_NS).text for oe in r.findall("dmn:outputEntry", _DMN_NS)]
        outputs_by_rule.append(oe_texts)
    assert outputs_by_rule[0][0] == '"cinematography"'
    assert outputs_by_rule[1][0] == '"persist"'


def test_dmn_refine_rule_inputs_match_python_predicate(dmn_tree: ET.ElementTree) -> None:
    """The first (refine) rule must encode `score < 0.75 AND iteration < maxIter`,
    matching the Python predicate verbatim."""
    rules = dmn_tree.getroot().findall(".//dmn:rule", _DMN_NS)
    refine = rules[0]
    inputs = [ie.find("dmn:text", _DMN_NS).text for ie in refine.findall("dmn:inputEntry", _DMN_NS)]
    # whitespace-tolerant compare
    inputs = [s.strip() for s in inputs]
    assert inputs == ["< 0.75", "< maxIter", "-"], inputs


# ── Seed migration ────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def migration_sql() -> str:
    assert _MIGRATION_PATH.is_file(), f"missing migration: {_MIGRATION_PATH}"
    return _MIGRATION_PATH.read_text(encoding="utf-8")


def test_migration_inserts_into_vertex_dmn_model(migration_sql: str) -> None:
    assert "INSERT INTO vertex_dmn_model" in migration_sql


def test_migration_is_idempotent(migration_sql: str) -> None:
    """Re-running the seed must not produce a duplicate row — the NOT EXISTS
    guard on (decision_key, version) is the contract."""
    assert "NOT EXISTS" in migration_sql
    assert "decision_key = 'com.etzhayyim.policies.mangaka.composeScene3dRefinement'" in migration_sql
    assert re.search(r"AND\s+version\s*=\s*1", migration_sql)


def test_migration_decision_key_matches_dmn(migration_sql: str) -> None:
    assert f"'{_DECISION_KEY}'" in migration_sql


def test_migration_hit_policy_is_first(migration_sql: str) -> None:
    assert "'FIRST'" in migration_sql


def test_migration_embeds_dmn_xml_body(migration_sql: str) -> None:
    """The `dmn_xml` column must carry the same decision id as the
    standalone DMN file so audit tooling reading vertex_dmn_model.dmn_xml
    sees the canonical XML."""
    assert "&lt; 0.75" in migration_sql, "missing escaped < operator in embedded XML"
    assert f'id="{_DECISION_KEY}"' in migration_sql


def test_migration_rules_json_matches_dmn_rules(migration_sql: str) -> None:
    """The structured `rules_json` must enumerate both rules in the same
    order as the XML, so DMN evaluators that read this column directly
    (without parsing XML) get the same FIRST-hit semantics."""
    import json

    # Pull the rules_json block out of the migration text. It's the third
    # $$-quoted body in the file. Use a non-greedy match and look for
    # the rule ids to be unambiguous.
    bodies = re.findall(r"\$\$(.*?)\$\$", migration_sql, flags=re.DOTALL)
    rules_body = next(
        (b for b in bodies if "RefinementRule_refine" in b and "RefinementRule_persist" in b),
        None,
    )
    assert rules_body is not None, "rules_json block not found"
    rules = json.loads(rules_body)
    assert isinstance(rules, list) and len(rules) == 2

    refine, persist = rules
    assert refine["id"] == "RefinementRule_refine"
    assert refine["outputEntries"][0] == "cinematography"
    assert refine["inputEntries"][0].strip() == "< 0.75"
    assert refine["inputEntries"][1].strip() == "< maxIter"

    assert persist["id"] == "RefinementRule_persist"
    assert persist["outputEntries"][0] == "persist"
    # Catch-all rule: every inputEntry is "-".
    assert all(e == "-" for e in persist["inputEntries"])


def test_migration_has_paired_down_file() -> None:
    down = _MIGRATION_PATH.with_name(_MIGRATION_PATH.name.replace(".up.sql", ".down.sql"))
    assert down.is_file(), f"missing rollback: {down}"
    body = down.read_text(encoding="utf-8")
    assert "DELETE FROM vertex_dmn_model" in body
    assert f"'{_DECISION_KEY}'" in body


# ── Behavioural equivalence with `_route_after_critique` ──────────────────


def _evaluate_dmn_first(score: float, iteration: int, max_iter: int) -> str:
    """Tiny FIRST-hit evaluator for the 2-rule table. Returns the route
    string ("cinematography" or "persist"). Used here as a test oracle —
    NOT a production DMN runtime."""
    if score < 0.75 and iteration < max_iter:
        return "cinematography"
    return "persist"


@pytest.mark.parametrize(
    "score,iteration,max_iter,expected",
    [
        # below acceptance bar, budget remaining → refine
        (0.0, 0, 3, "cinematography"),
        (0.5, 1, 3, "cinematography"),
        (0.7499, 2, 3, "cinematography"),
        # at / above acceptance bar → persist
        (0.75, 0, 3, "persist"),
        (0.9, 0, 3, "persist"),
        (1.0, 0, 3, "persist"),
        # budget exhausted → persist regardless of score
        (0.0, 3, 3, "persist"),
        (0.0, 4, 3, "persist"),
        # both gates failing → persist
        (0.95, 5, 3, "persist"),
    ],
)
def test_dmn_equivalent_to_python_route_after_critique(
    score: float, iteration: int, max_iter: int, expected: str
) -> None:
    """The oracle is what `_route_after_critique` does at the boundaries.
    If you change the predicate in `compose_scene_3d.py`, this test must
    fail, forcing the DMN file + migration to be updated in lockstep."""
    # Import lazily so the DMN test file doesn't pull in langgraph + heavy
    # transitive deps when only structural checks are needed.
    try:
        from lg_mangaka.graphs.compose_scene_3d import _route_after_critique  # noqa: E402
    except Exception:
        pytest.skip("compose_scene_3d not importable (langgraph dep missing in env)")
    route = _route_after_critique(
        {"score": score, "iteration": iteration, "max_iter": max_iter}
    )
    assert route == expected == _evaluate_dmn_first(score, iteration, max_iter)
