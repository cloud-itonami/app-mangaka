"""Structural validator for `compose_scene_3d.topology.yaml`.

Phase B regression guard. The YAML is the SSoT for the Phase C topology
assistant row (ADR-2605082000 §2 / ADR-2605141200 §P6) — once Phase C flips
`vertex_langgraph_deployment` to v2, the bpmn-dispatcher /runs router
materialises this YAML into a LangGraph at request time via
`kotodama.langgraph_loader._compile_topology`. Any drift in the YAML
that breaks the resolver contract will surface as a hung XRPC call.

This test runs offline (no DB, no LangGraph import) and only asserts the
structural invariants `_compile_topology` actually relies on:

- `entry` references a defined node
- every `edges[].from` / `edges[].to` references a defined node or "END"
- every `conditional_edges[].from` references a defined node
- every `conditional_edges[].paths.*` target references a defined node or "END"
- every `conditional_edges` row has exactly one of `router` / `field`
  (per ADR-2605082000 Phase D)
- `state_keys` is non-empty (required by the TypedDict synthesis)
- `pending_mcp_tools[]` aligns 1:1 with `mcp_tool` `mcp://...` refs
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_LG_DIR = Path(__file__).resolve().parents[1]
_TOPOLOGY_PATH = (
    _LG_DIR / "lg_mangaka" / "graphs" / "compose_scene_3d.topology.yaml"
)


@pytest.fixture(scope="module")
def spec() -> dict:
    raw = _TOPOLOGY_PATH.read_text(encoding="utf-8")
    return yaml.safe_load(raw)


def test_topology_file_exists() -> None:
    assert _TOPOLOGY_PATH.is_file(), f"missing: {_TOPOLOGY_PATH}"


def test_top_level_required_fields(spec: dict) -> None:
    for key in ("assistant_id", "version", "kind", "entry", "state_keys", "nodes", "edges"):
        assert key in spec, f"topology missing required top-level key: {key}"
    assert spec["kind"] == "topology"
    assert isinstance(spec["version"], int) and spec["version"] >= 1


def test_assistant_id_matches_nsid_convention(spec: dict) -> None:
    assistant_id = spec["assistant_id"]
    assert isinstance(assistant_id, str)
    assert assistant_id.startswith("com.etzhayyim.mangaka."), assistant_id
    # 4-segment minimum (NSID rule from root CLAUDE.md / ADR-2604231811).
    assert len(assistant_id.split(".")) >= 4


def test_state_keys_non_empty(spec: dict) -> None:
    """`_compile_topology` raises if `state_keys` is empty — every channel
    that nodes write must be declared here."""
    keys = spec.get("state_keys") or []
    assert isinstance(keys, list)
    assert len(keys) > 0


def test_state_keys_cover_channels_used_in_send_fanout(spec: dict) -> None:
    """`pose_plan` / `sim_result` are the Send-fan-out channels; they must
    be present in `state_keys` so the per-key reducer attaches."""
    keys = set(spec.get("state_keys") or [])
    assert "pose_plan" in keys, "Send fan-out channel `pose_plan` missing"
    assert "sim_result" in keys, "Send fan-out channel `sim_result` missing"
    assert "renders" in keys, "list-concat channel `renders` missing"


def test_nodes_have_unique_ids(spec: dict) -> None:
    ids = [n["id"] for n in spec["nodes"]]
    assert len(ids) == len(set(ids)), f"duplicate node ids: {ids}"


def test_every_node_has_required_keys(spec: dict) -> None:
    for n in spec["nodes"]:
        for key in ("id", "kind", "ref"):
            assert key in n, f"node {n.get('id')!r} missing required key {key}"
        assert n["kind"] in {
            "mcp_tool", "sql_udf", "py_ext_udf", "llm", "llm_vision", "py_primitive", "foreach",
        }, (
            f"node {n['id']}: unsupported kind {n['kind']!r}"
        )


def test_entry_node_is_defined(spec: dict) -> None:
    node_ids = {n["id"] for n in spec["nodes"]}
    assert spec["entry"] in node_ids, (
        f"entry={spec['entry']!r} not in node set {sorted(node_ids)}"
    )


def test_every_edge_endpoint_is_defined(spec: dict) -> None:
    node_ids = {n["id"] for n in spec["nodes"]}
    for e in spec.get("edges") or []:
        assert e["from"] in node_ids, f"edge.from {e['from']!r} not defined"
        assert e["to"] == "END" or e["to"] in node_ids, (
            f"edge.to {e['to']!r} not defined"
        )


def test_every_conditional_edge_endpoint_is_defined(spec: dict) -> None:
    node_ids = {n["id"] for n in spec["nodes"]}
    for ce in spec.get("conditional_edges") or []:
        assert ce["from"] in node_ids, f"cond.from {ce['from']!r} not defined"
        paths = ce.get("paths") or {}
        for label, target in paths.items():
            assert target == "END" or target in node_ids, (
                f"cond.paths[{label!r}]={target!r} not defined"
            )


def test_conditional_edges_have_single_routing_source(spec: dict) -> None:
    """Per ADR-2605082000 Phase D: exactly one of router / field / condition_ref
    per conditional_edge. The current YAML uses `router` (send_fanout) for
    the cinematography fan-out and `condition_ref` (DMN) for the critique
    refinement loop."""
    for ce in spec.get("conditional_edges") or []:
        signals = sum(1 for k in ("router", "field", "condition_ref") if ce.get(k))
        assert signals == 1, (
            f"conditional_edge from={ce.get('from')!r}: "
            f"expected exactly one of router/field/condition_ref, got {signals}"
        )


def test_send_fanout_router_has_required_fields(spec: dict) -> None:
    for ce in spec.get("conditional_edges") or []:
        if ce.get("router") != "send_fanout":
            continue
        fan = ce.get("fanout") or {}
        for key in ("from_state", "to_node", "payload_keys"):
            assert key in fan, (
                f"send_fanout from={ce['from']!r} missing fanout.{key}"
            )
        assert fan["from_state"] in (spec.get("state_keys") or []), (
            f"send_fanout.from_state={fan['from_state']!r} not in state_keys"
        )


def test_pending_mcp_tools_match_actual_refs(spec: dict) -> None:
    """`pending_mcp_tools[]` is the human-maintained checklist of MCP tools
    that must exist before Phase C activation. Drift between this list and
    the actual `mcp_tool` refs in `nodes[]` is exactly the kind of bug
    Phase C is most likely to hit."""
    declared = sorted(spec.get("pending_mcp_tools") or [])
    referenced = sorted(
        {
            n["ref"].removeprefix("mcp://")
            for n in spec["nodes"]
            if n["kind"] == "mcp_tool" and isinstance(n.get("ref"), str) and n["ref"].startswith("mcp://")
        }
    )
    assert declared == referenced, (
        f"pending_mcp_tools out of sync with mcp_tool refs.\n"
        f"  declared but not in nodes[]: {sorted(set(declared) - set(referenced))}\n"
        f"  in nodes[] but not declared: {sorted(set(referenced) - set(declared))}"
    )


def test_pending_mcp_tools_have_matching_lexicons() -> None:
    """Each `pending_mcp_tools[]` entry must have a lexicon JSON at
    `00-contracts/lexicons/com/etzhayyim/apps/mangaka/tools/<action>.json`.
    The seed migration 20260514140000 inserts vertex_mcp_tool_def rows
    pointing at these paths; missing files would make `etzhayyim contract sync`
    fail."""
    # _LG_DIR = `<repo>/60-apps/etzhayyim-project-mangaka/lg` → repo is 3 up.
    repo_root = _LG_DIR.parents[2]
    lex_dir = repo_root / "00-contracts" / "lexicons" / "com" / "etzhayyim" / "apps" / "mangaka" / "tools"
    raw = _TOPOLOGY_PATH.read_text(encoding="utf-8")
    spec = yaml.safe_load(raw)
    for nsid in spec.get("pending_mcp_tools") or []:
        # nsid = com.etzhayyim.mangaka.tools.<action> → <action>.json
        action = nsid.rsplit(".", 1)[-1]
        path = lex_dir / f"{action}.json"
        assert path.is_file(), f"missing lexicon for {nsid}: {path}"


def test_text_llm_nodes_use_inline_args_shape(spec: dict) -> None:
    """P10.1: pose_characters + cinematography must use the `make_llm_node`
    contract (input_keys + result_key + args.system + args.user_template).
    `prompt_ref` / `response_schema_ref` were placeholder hints from Phase A
    drafting; the canonical resolver doesn't dereference them yet.

    `critique_and_select` is exempt from this test — it's multimodal
    (vision) and needs `make_llm_vision_node` (sub-blocker P10.1b)."""
    text_llm_ids = {"pose_characters", "cinematography"}
    by_id = {n["id"]: n for n in spec["nodes"]}
    state_keys = set(spec.get("state_keys") or [])

    for nid in text_llm_ids:
        assert nid in by_id, f"text-LLM node {nid} missing from topology"
        n = by_id[nid]
        assert n["kind"] == "llm", f"{nid}: expected kind=llm, got {n['kind']!r}"
        cfg = n.get("config") or {}

        # Contract guards.
        assert "input_keys" in cfg, f"{nid}: config.input_keys required"
        assert isinstance(cfg["input_keys"], list) and cfg["input_keys"], (
            f"{nid}: config.input_keys must be a non-empty list"
        )
        assert "result_key" in cfg, f"{nid}: config.result_key required"
        assert isinstance(cfg["result_key"], str) and cfg["result_key"], (
            f"{nid}: config.result_key must be a non-empty string"
        )
        args = cfg.get("args") or {}
        for arg_key in ("system", "user_template"):
            assert arg_key in args and isinstance(args[arg_key], str) and args[arg_key].strip(), (
                f"{nid}: config.args.{arg_key} required non-empty string"
            )
        # Numeric clamps must be present + sane (or absent, in which case the
        # resolver uses its defaults). When present, sanity-check.
        if "max_tokens" in args:
            assert isinstance(args["max_tokens"], int) and 64 <= args["max_tokens"] <= 8192
        if "temperature" in args:
            assert isinstance(args["temperature"], (int, float)) and 0.0 <= args["temperature"] <= 2.0

        # state_keys must declare the result_key (per _compile_topology).
        assert cfg["result_key"] in state_keys, (
            f"{nid}: result_key {cfg['result_key']!r} not in state_keys"
        )

        # input_keys must each be declared state channels.
        for ik in cfg["input_keys"]:
            assert ik in state_keys, (
                f"{nid}: input_keys[{ik!r}] not in state_keys"
            )


def test_critique_node_is_flagged_as_vision_blocker(spec: dict) -> None:
    """P10.1b — `critique_and_select` still carries the `prompt_ref` /
    `response_schema_ref` shape because `make_llm_node` can't dispatch
    multimodal yet. The follow-up resolver `make_llm_vision_node` will
    flip this to the `args.image_keys` shape. Surface that as an explicit
    structural assertion so the gap is visible to a reviewer."""
    by_id = {n["id"]: n for n in spec["nodes"]}
    crit = by_id.get("critique_and_select")
    assert crit is not None
    # P10.1b closed: kind moves from `llm` (placeholder) to `llm_vision`
    # (canonical multimodal kind). Both are still valid migration states
    # because the in-tree Phase A path is independent until deployment flips.
    assert crit["kind"] in {"llm", "llm_vision"}, (
        f"critique_and_select kind must be llm (pre-P10.1b) or llm_vision (post-P10.1b), got {crit['kind']!r}"
    )
    assert crit["ref"] == "vision", "critique must declare the vision tier"
    cfg = crit.get("config") or {}
    # Either still on the placeholder shape (pre-P10.1b) OR on the inline
    # shape (post-P10.1b). Both states are valid; the test asserts the
    # contract gap is annotated.
    has_placeholder = "prompt_ref" in cfg or "response_schema_ref" in cfg
    has_inline = "args" in cfg and isinstance(cfg.get("args"), dict)
    assert has_placeholder or has_inline, (
        "critique_and_select: config must declare either the placeholder "
        "prompt_ref shape (with P10.1b sub-blocker TODO comment) or the "
        "inline args.* shape once make_llm_vision_node lands"
    )
    # When kind is the canonical `llm_vision`, `image_keys` must be a
    # non-empty list of dotted paths so the vision resolver knows which
    # blob references to materialise.
    if crit["kind"] == "llm_vision":
        image_keys = cfg.get("image_keys")
        assert isinstance(image_keys, list) and image_keys, (
            "llm_vision critique node must declare a non-empty config.image_keys list"
        )
        for ik in image_keys:
            assert isinstance(ik, str) and ik.strip(), (
                f"image_keys entry must be a non-empty string, got {ik!r}"
            )


def test_every_referenced_node_has_no_orphan_bindings(spec: dict) -> None:
    """If a node id appears in `nodes[]` but never in an edge, entry, or
    conditional_edge path, `_compile_topology` skips it silently.
    Flag the orphan so the YAML stays consistent with the deployed graph."""
    node_ids = {n["id"] for n in spec["nodes"]}
    referenced: set[str] = set()
    referenced.add(spec["entry"])
    for e in spec.get("edges") or []:
        if e["from"] in node_ids:
            referenced.add(e["from"])
        if e["to"] in node_ids:
            referenced.add(e["to"])
    for ce in spec.get("conditional_edges") or []:
        if ce["from"] in node_ids:
            referenced.add(ce["from"])
        for tgt in (ce.get("paths") or {}).values():
            if tgt in node_ids:
                referenced.add(tgt)
        # Send fan-out target also counts.
        fan = ce.get("fanout") or {}
        if fan.get("to_node") in node_ids:
            referenced.add(fan["to_node"])
    orphans = node_ids - referenced
    assert not orphans, f"nodes defined but never referenced: {sorted(orphans)}"


def test_deployments_section_exists(spec: dict) -> None:
    """`deployments[]` drives the bpmn-dispatcher /runs router NSID lookup.
    Missing this section would activate the assistant but leave it
    unreachable from the Worker."""
    deps = spec.get("deployments") or []
    assert isinstance(deps, list) and len(deps) >= 1
    for d in deps:
        for key in ("nsid", "status", "replicas"):
            assert key in d, f"deployment missing {key}: {d}"
        assert d["status"] in {"active", "inactive", "draft"}
