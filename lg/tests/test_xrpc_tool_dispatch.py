"""P9 unit tests — pod-side `/xrpc/{nsid}` dispatch to MCP tool handlers.

Validates the routing layer added in `lg_mangaka.server`:

  • `com.etzhayyim.mangaka.tools.*` NSIDs land on the corresponding
    `lg_mangaka.tools.tool_*` function with camelCase → snake_case kwargs.
  • Sync tools (e.g. tool_place_scene) are wrapped via `asyncio.to_thread`
    so the dispatcher stays uniformly awaitable.
  • Unknown / extra kwargs return a 400-style payload, not a 500.
  • Non-tool NSIDs still fall through to the existing Pregel graph path.

Real LangGraph runtime is not exercised — server.py's `xrpc_compat`
imports the full stack at module load, so we restrict assertions to the
new tool-dispatch function `_dispatch_mcp_tool` which is callable
without FastAPI / checkpointer / graphs state.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_LG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_LG_DIR))

import pytest

# Import the server module so we can patch the underlying tools handler.
from lg_mangaka import server as srv
from lg_mangaka import tools as tools_mod


def _run(coro):
    return asyncio.run(coro)


# ── routing table ─────────────────────────────────────────────────────────


def test_routing_table_covers_all_topology_tools():
    """As of P13 the dispatcher routes 9 tools: the 6 Pregel-step bodies,
    the cinematography validator, the critique aggregator, and the VRM
    ingestion helper. The set is asserted exactly so any silent drop /
    dup surfaces here before Phase C activation."""
    assert set(srv._TOOL_NSID_TO_HANDLER.keys()) == {
        "com.etzhayyim.mangaka.tools.loadPanelPlan",
        "com.etzhayyim.mangaka.tools.resolveAssets",
        "com.etzhayyim.mangaka.tools.placeScene",
        "com.etzhayyim.mangaka.tools.simulateCharacter",
        "com.etzhayyim.mangaka.tools.renderKeyframes",
        "com.etzhayyim.mangaka.tools.persistScene3d",
        # P10.2 — cinematography output validator
        "com.etzhayyim.mangaka.tools.validateCameraPlan",
        # P10.2b — critique aggregator (Hume overlay + best-of-N)
        "com.etzhayyim.mangaka.tools.aggregateCritique",
        # P13 — VRM ingestion (B2 upload + character vertex patch)
        "com.etzhayyim.mangaka.tools.attachCharacterVrm",
    }


def test_routing_table_points_at_tools_module():
    # Each handler must be the corresponding `tools.tool_*` function so the
    # MCP contract (lexicon SSoT) and the runtime body (tools.py SSoT) stay
    # in lockstep.
    assert srv._TOOL_NSID_TO_HANDLER["com.etzhayyim.mangaka.tools.loadPanelPlan"] is tools_mod.tool_load_panel_plan
    assert srv._TOOL_NSID_TO_HANDLER["com.etzhayyim.mangaka.tools.resolveAssets"] is tools_mod.tool_resolve_assets
    assert srv._TOOL_NSID_TO_HANDLER["com.etzhayyim.mangaka.tools.placeScene"] is tools_mod.tool_place_scene
    assert srv._TOOL_NSID_TO_HANDLER["com.etzhayyim.mangaka.tools.simulateCharacter"] is tools_mod.tool_simulate_character
    assert srv._TOOL_NSID_TO_HANDLER["com.etzhayyim.mangaka.tools.renderKeyframes"] is tools_mod.tool_render_keyframes
    assert srv._TOOL_NSID_TO_HANDLER["com.etzhayyim.mangaka.tools.persistScene3d"] is tools_mod.tool_persist_scene_3d
    assert srv._TOOL_NSID_TO_HANDLER["com.etzhayyim.mangaka.tools.validateCameraPlan"] is tools_mod.tool_validate_camera_plan
    assert srv._TOOL_NSID_TO_HANDLER["com.etzhayyim.mangaka.tools.aggregateCritique"] is tools_mod.tool_aggregate_critique


# ── _camel_to_snake ───────────────────────────────────────────────────────


def test_camel_to_snake_handles_all_lexicon_field_shapes():
    assert srv._camel_to_snake("panelRkey") == "panel_rkey"
    assert srv._camel_to_snake("renderAngles") == "render_angles"
    assert srv._camel_to_snake("rwUrl") == "rw_url"
    assert srv._camel_to_snake("dryRun") == "dry_run"
    assert srv._camel_to_snake("alreadySnake") == "already_snake"
    assert srv._camel_to_snake("singleword") == "singleword"


# ── _dispatch_mcp_tool — sync handler ─────────────────────────────────────


def test_dispatch_sync_tool_place_scene():
    """tool_place_scene is sync; dispatcher must wrap it via to_thread."""
    body = {
        "panelPlan": {"rkey": "p-1", "shot": "MediumShot"},
        "assetRefs": {"characters": {}, "environment": None, "props": {}},
        "posePlan": {},
    }
    out = _run(srv._dispatch_mcp_tool("com.etzhayyim.mangaka.tools.placeScene", body))
    assert "sceneDag" in out
    assert out["sceneDag"]["panel"]["rkey"] == "p-1"
    assert out["tool"] == "com.etzhayyim.mangaka.tools.placeScene"
    assert isinstance(out["latencyMs"], int)


# ── _dispatch_mcp_tool — async handler ────────────────────────────────────


def test_dispatch_async_tool_simulate_character():
    """tool_simulate_character is async; dispatcher awaits it directly."""
    body = {"charRkey": "ch-honoka", "ticks": 12}
    out = _run(srv._dispatch_mcp_tool("com.etzhayyim.mangaka.tools.simulateCharacter", body))
    assert "simResult" in out
    assert out["simResult"]["ch-honoka"]["settled"] is True
    assert out["simResult"]["ch-honoka"]["ticks"] == 12
    assert out["tool"] == "com.etzhayyim.mangaka.tools.simulateCharacter"


def test_dispatch_simulate_character_empty_charRkey():
    body = {"charRkey": ""}
    out = _run(srv._dispatch_mcp_tool("com.etzhayyim.mangaka.tools.simulateCharacter", body))
    assert out["simResult"] == {}


# ── _dispatch_mcp_tool — error envelopes ──────────────────────────────────


def test_dispatch_unknown_kwarg_returns_400_envelope(monkeypatch):
    """Extra fields in the body surface as TypeError; dispatcher wraps them
    as a structured error instead of raising 500."""
    body = {"panelPlan": {}, "assetRefs": {}, "posePlan": {}, "extraField": 1}
    out = _run(srv._dispatch_mcp_tool("com.etzhayyim.mangaka.tools.placeScene", body))
    assert "error" in out
    assert "MCP tool input mismatch" in out["error"]
    assert out["tool"] == "com.etzhayyim.mangaka.tools.placeScene"


def test_dispatch_handler_exception_returns_error_envelope(monkeypatch):
    """A handler that raises a generic exception lands in the
    `mcp tool <ExcName>` branch with a truncated detail."""

    async def boom(**_):
        raise RuntimeError("upstream pod down for maintenance")

    monkeypatch.setitem(
        srv._TOOL_NSID_TO_HANDLER,
        "com.etzhayyim.mangaka.tools.loadPanelPlan",
        boom,
    )
    out = _run(srv._dispatch_mcp_tool("com.etzhayyim.mangaka.tools.loadPanelPlan", {"panelRkey": "p-x"}))
    assert out["error"].startswith("mcp tool RuntimeError")
    assert "upstream pod" in out["errorDetail"]
    assert out["tool"] == "com.etzhayyim.mangaka.tools.loadPanelPlan"


# ── error path returned by tools.py itself (not exception) ────────────────


def test_dispatch_load_panel_plan_returns_tool_error_when_unconfigured(monkeypatch):
    """`tool_load_panel_plan` returns `{"error": ...}` when RW_URL is empty.
    The dispatcher passes that through unchanged + decorates with tool/latency."""
    monkeypatch.setattr(tools_mod, "_DEFAULT_RW_URL", "")
    out = _run(srv._dispatch_mcp_tool(
        "com.etzhayyim.mangaka.tools.loadPanelPlan",
        {"panelRkey": "p-1"},
    ))
    # Either the tool's own error envelope OR an exception envelope; both
    # carry tool/latencyMs and a stringy error.
    assert "error" in out
    assert out["tool"] == "com.etzhayyim.mangaka.tools.loadPanelPlan"
    assert isinstance(out["latencyMs"], int)


# ── non-tool NSID fallthrough (smoke check on the table only) ─────────────


def test_non_tool_nsid_is_not_in_tool_table():
    """The legacy NSID→assistant table and the tool table do not overlap."""
    legacy_nsids = set(srv._NSID_TO_ASSISTANT.keys())
    tool_nsids = set(srv._TOOL_NSID_TO_HANDLER.keys())
    assert legacy_nsids.isdisjoint(tool_nsids)
