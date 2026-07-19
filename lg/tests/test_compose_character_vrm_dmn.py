"""Structural validator + behavioural parity for the `vrmBindRetry` DMN.

P16-e of ADR-2605141200. Locks consistency across four SSoT artifacts
that must agree for Phase C activation of compose_character_vrm:

  1. DMN XML at `00-contracts/dmn/com/etzhayyim/policies/mangaka/vrmBindRetry.dmn`
  2. Seed migration at
     `30-graph/graph-schema/sql_migrations/20260514200000_seed_mangaka_vrm_bind_retry_dmn.up.sql`
     (decision_key + version + structured rules_json + embedded dmn_xml)
  3. Topology YAML `compose_character_vrm.topology.yaml` `condition_ref`
  4. The real DMN evaluator at
     `kotodama.langgraph_node_resolvers.make_dmn_condition_router` —
     when the resolver is importable in the test env, we drive it
     against the seeded rules to assert end-to-end parity.

The behavioural section locks the three routing paths:

  | input                       | expected route |
  |-----------------------------|----------------|
  | valid=true,  iteration=0    | accept         |
  | valid=true,  iteration=5    | accept         |
  | valid=false, iteration=0    | retry          |
  | valid=false, iteration=1    | retry          |
  | valid=false, iteration=2    | reject         |
  | valid=false, iteration=99   | reject         |

Pure-CPU, offline — no DB. The DMN evaluator is exercised directly
against parsed `rules_json` via the FIRST hit-policy oracle below;
when `kotodama` is importable, the real `_eval_dmn_rule` runs too.
"""

from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

_LG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_LG_DIR))

_REPO_ROOT = _LG_DIR.parents[2]
_DMN_PATH = (
    _REPO_ROOT
    / "00-contracts" / "dmn" / "com" / "etzhayyim" / "policies" / "mangaka"
    / "vrmBindRetry.dmn"
)
_MIGRATION_PATH = (
    _REPO_ROOT
    / "30-graph" / "graph-schema" / "sql_migrations"
    / "20260514200000_seed_mangaka_vrm_bind_retry_dmn.up.sql"
)
_TOPOLOGY_PATH = (
    _LG_DIR / "lg_mangaka" / "graphs" / "compose_character_vrm.topology.yaml"
)
_DMN_NS = {"dmn": "https://www.omg.org/spec/DMN/20191111/MODEL/"}
_DECISION_KEY = "com.etzhayyim.policies.mangaka.vrmBindRetry"
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
    table = dmn_tree.getroot().find(".//dmn:decisionTable", _DMN_NS)
    assert table is not None
    assert table.get("hitPolicy") == "FIRST"


def test_dmn_inputs_match_expected(dmn_tree: ET.ElementTree) -> None:
    """The Pregel reads `valid` (from validate_vrm result) + `iteration`
    (from state). DMN must expose them in that order."""
    table = dmn_tree.getroot().find(".//dmn:decisionTable", _DMN_NS)
    input_exprs = [
        ie.find("dmn:text", _DMN_NS).text
        for ie in table.findall("dmn:input/dmn:inputExpression", _DMN_NS)
    ]
    assert input_exprs == ["valid", "iteration"], input_exprs


def test_dmn_output_is_route_and_reason(dmn_tree: ET.ElementTree) -> None:
    table = dmn_tree.getroot().find(".//dmn:decisionTable", _DMN_NS)
    outputs = [o.get("name") for o in table.findall("dmn:output", _DMN_NS)]
    assert outputs == ["route", "reason"]


def test_dmn_has_three_rules_in_order(dmn_tree: ET.ElementTree) -> None:
    """Rule order matters under FIRST: accept (valid=true) MUST come
    before retry (valid=false, iter<2) MUST come before the catch-all
    reject. Reordering would silently break the retry budget."""
    rules = dmn_tree.getroot().findall(".//dmn:rule", _DMN_NS)
    assert len(rules) == 3
    routes = []
    for r in rules:
        oe_texts = [oe.find("dmn:text", _DMN_NS).text for oe in r.findall("dmn:outputEntry", _DMN_NS)]
        routes.append(oe_texts[0])
    assert routes == ['"accept"', '"retry"', '"reject"'], routes


def test_dmn_retry_rule_caps_iteration_under_two(dmn_tree: ET.ElementTree) -> None:
    """The retry rule must encode `valid=false AND iteration < 2`. The
    `< 2` cap is the explicit one-retry budget — changing it changes
    the Pregel's worst-case GPU cost per character."""
    rules = dmn_tree.getroot().findall(".//dmn:rule", _DMN_NS)
    retry = rules[1]
    inputs = [ie.find("dmn:text", _DMN_NS).text for ie in retry.findall("dmn:inputEntry", _DMN_NS)]
    inputs = [s.strip() for s in inputs]
    assert inputs == ["false", "< 2"], inputs


# ── Seed migration ────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def migration_sql() -> str:
    assert _MIGRATION_PATH.is_file(), f"missing migration: {_MIGRATION_PATH}"
    return _MIGRATION_PATH.read_text(encoding="utf-8")


def test_migration_inserts_into_vertex_dmn_model(migration_sql: str) -> None:
    assert "INSERT INTO vertex_dmn_model" in migration_sql


def test_migration_is_idempotent(migration_sql: str) -> None:
    assert "NOT EXISTS" in migration_sql
    assert f"decision_key = '{_DECISION_KEY}'" in migration_sql
    assert re.search(r"AND\s+version\s*=\s*1", migration_sql)


def test_migration_hit_policy_is_first(migration_sql: str) -> None:
    assert "'FIRST'" in migration_sql


def test_migration_embeds_dmn_xml_body(migration_sql: str) -> None:
    """The `dmn_xml` column carries the canonical XML so audit tooling
    reading vertex_dmn_model.dmn_xml sees the same decision id, the
    escaped `< 2` operator, and the three rules."""
    assert f'id="{_DECISION_KEY}"' in migration_sql
    assert "&lt; 2" in migration_sql, "missing escaped < operator in embedded XML"


def test_migration_rules_json_matches_dmn_rules(migration_sql: str) -> None:
    bodies = re.findall(r"\$\$(.*?)\$\$", migration_sql, flags=re.DOTALL)
    rules_body = next(
        (
            b for b in bodies
            if "VrmBindRule_accept" in b
            and "VrmBindRule_retry" in b
            and "VrmBindRule_reject" in b
        ),
        None,
    )
    assert rules_body is not None, "rules_json block not found in migration"
    rules = json.loads(rules_body)
    assert isinstance(rules, list) and len(rules) == 3
    accept, retry, reject = rules

    assert accept["id"] == "VrmBindRule_accept"
    assert accept["outputEntries"][0] == "accept"
    assert accept["inputEntries"][0].strip() == "true"

    assert retry["id"] == "VrmBindRule_retry"
    assert retry["outputEntries"][0] == "retry"
    assert retry["inputEntries"][0].strip() == "false"
    assert retry["inputEntries"][1].strip() == "< 2"

    assert reject["id"] == "VrmBindRule_reject"
    assert reject["outputEntries"][0] == "reject"
    assert all(e == "-" for e in reject["inputEntries"])


def test_migration_has_paired_down_file() -> None:
    down = _MIGRATION_PATH.with_name(_MIGRATION_PATH.name.replace(".up.sql", ".down.sql"))
    assert down.is_file(), f"missing rollback: {down}"
    body = down.read_text(encoding="utf-8")
    assert "DELETE FROM vertex_dmn_model" in body
    assert f"'{_DECISION_KEY}'" in body


# ── Behavioural parity ────────────────────────────────────────────────────


def _evaluate_dmn_first(rules: list[dict], state: dict) -> str:
    """Tiny FIRST-hit evaluator. Mirrors langgraph_node_resolvers
    semantics for the operators this DMN uses (`true` / `false` / `< N` /
    `-`). Kept here as an in-test oracle so the test is meaningful even
    when kotodama isn't importable in the venv."""
    inputs_meta = [{"name": "valid"}, {"name": "iteration"}]
    for rule in rules:
        match = True
        for entry, meta in zip(rule["inputEntries"], inputs_meta):
            entry = entry.strip()
            value = state.get(meta["name"])
            if entry == "-":
                continue
            if entry in ("true", "false"):
                if value != (entry == "true"):
                    match = False
                    break
                continue
            if entry.startswith("< "):
                try:
                    rhs = float(entry[2:].strip())
                    if not (float(value) < rhs):
                        match = False
                        break
                except (TypeError, ValueError):
                    match = False
                    break
                continue
            # Bare literal numeric equality fallback.
            try:
                if float(value) != float(entry):
                    match = False
                    break
            except (TypeError, ValueError):
                if value != entry:
                    match = False
                    break
        if match:
            return rule["outputEntries"][0]
    raise AssertionError(f"no rule matched state {state!r}")


_CASES = [
    # (valid, iteration, expected_route)
    (True,  0,  "accept"),
    (True,  5,  "accept"),
    (False, 0,  "retry"),
    (False, 1,  "retry"),
    (False, 2,  "reject"),
    (False, 99, "reject"),
]


@pytest.mark.parametrize("valid,iteration,expected", _CASES)
def test_oracle_evaluator_matches_expected(migration_sql: str, valid: bool, iteration: int, expected: str) -> None:
    """In-test FIRST-hit oracle against the migration's rules_json.
    Locks the seeded rules' behaviour even when the langgraph resolver
    isn't importable in this venv."""
    bodies = re.findall(r"\$\$(.*?)\$\$", migration_sql, flags=re.DOTALL)
    rules = json.loads(next(b for b in bodies if "VrmBindRule_accept" in b))
    state = {"valid": valid, "iteration": iteration}
    assert _evaluate_dmn_first(rules, state) == expected


@pytest.mark.parametrize("valid,iteration,expected", _CASES)
def test_real_resolver_matches_expected(valid: bool, iteration: int, expected: str) -> None:
    """When `kotodama.langgraph_node_resolvers` is importable, drive
    the real `_eval_dmn_rule` evaluator over the seeded rules. This is
    the canonical Phase C correctness check — it exercises the same
    code the LangGraph runtime calls."""
    try:
        from kotodama.langgraph_node_resolvers import (
            _eval_dmn_rule,
        )
    except Exception:
        pytest.skip("kotodama not importable in this environment")

    # Parse the migration's rules_json once and feed each rule into the
    # real evaluator. The first rule that matches wins (FIRST hit).
    body = _MIGRATION_PATH.read_text(encoding="utf-8")
    bodies = re.findall(r"\$\$(.*?)\$\$", body, flags=re.DOTALL)
    rules = json.loads(next(b for b in bodies if "VrmBindRule_accept" in b))
    inputs_meta = [{"name": "valid", "typeRef": "boolean"},
                   {"name": "iteration", "typeRef": "number"}]
    state = {"valid": valid, "iteration": iteration}
    chosen: str | None = None
    for rule in rules:
        if _eval_dmn_rule(rule, inputs_meta, state):
            chosen = rule["outputEntries"][0]
            break
    assert chosen == expected, f"resolver picked {chosen!r}, expected {expected!r}"
