"""mangaka `analyze_character_graph` — Pregel-style multi-step graph that
derives character relationship analytics from kind=panel vertices, and writes
results back to kind=character and kind=environment vertex.props.

4 super-steps (each is a LangGraph node):

  1. load_panels       — SELECT kind=panel, props from vertex_mangaka
  2. extract_refs      — parse props.characters[] / props.environment per panel
  3. compute_metrics   — aggregate co-occurrence, influence (sum of weighted
                         co-occurrence), environment affinity
  4. write_back        — UPDATE kind=character / kind=environment props

Pregel idiom: each step reads upstream channel (state field), writes its own
output channel; downstream picks it up. Single ainvoke kicks off the cascade.

Input (optional):
    workRkey   str — limit to one work (default: "gh-work-ghost-hacker")
    dryRun     bool — skip write_back (for inspection)

Output:
    status            "analyzed" | "error"
    counts            { panels, characters, environments, edges }
    topInfluence      list[ {charRkey, score, appearances} ] (top 10)
    error             str | null
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

_log = logging.getLogger(__name__)

_APP_DID = os.environ.get("MANGAKA_APP_DID", "did:web:mangaka.etzhayyim.com")
_DEFAULT_WORK = "gh-work-ghost-hacker"


class _State(TypedDict, total=False):
    work_rkey: str
    dry_run: bool
    # super-step output channels:
    panels: list             # [{ rkey, characters[], environment, page_number, panel_number }]
    char_appearances: dict   # charRkey -> [panel_rkey, ...]
    co_occur: dict           # charRkey -> { otherCharRkey: count }
    char_envs: dict          # charRkey -> { envRkey: count }
    env_panels: dict         # envRkey -> [panel_rkey, ...]
    metrics: dict            # charRkey -> { score, appearances, topCo[], topEnvs[] }
    env_metrics: dict        # envRkey -> { panelCount, topChars[] }
    # final output:
    status: str
    counts: dict
    topInfluence: list
    error: str | None


# Super-step 1: load all panels
async def _step_load_panels(state: _State) -> dict[str, Any]:
    work_rkey = state.get("work_rkey") or _DEFAULT_WORK
    from kotodama.kotoba_datomic import get_kotoba_client
    import asyncio
    client = get_kotoba_client()
    try:
        rows = await asyncio.to_thread(
            client.select_where,
            "vertex_mangaka",
            "kind",
            "panel",
            ["rkey", "page_number", "panel_number", "props"],
            limit=1000000
        )
        panels: list[dict[str, Any]] = []
        for r in rows:
            rkey = r.get("rkey", "")
            if not rkey.startswith("gh-panel-"):
                continue
            page_n = r.get("page_number")
            panel_n = r.get("panel_number")
            props_str = r.get("props")
            try:
                p = json.loads(props_str or "{}") if isinstance(props_str, str) else (props_str or {})
            except Exception:
                p = {}
            panels.append({
                "rkey": rkey,
                "page_number": page_n,
                "panel_number": panel_n,
                "characters": p.get("characters") or [],
                "environment": p.get("environment"),
            })
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
    _log.info("analyze_character_graph: loaded %d panels", len(panels))
    return {"panels": panels}

# Super-step 2: extract cross-refs into adjacency-style dicts
async def _step_extract_refs(state: _State) -> dict[str, Any]:
    panels = state.get("panels") or []
    char_appearances: dict[str, list[str]] = defaultdict(list)
    co_occur: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    char_envs: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    env_panels: dict[str, list[str]] = defaultdict(list)

    for p in panels:
        rkey = p["rkey"]
        chars = [c for c in (p.get("characters") or []) if c]
        env = p.get("environment")
        if env:
            env_panels[env].append(rkey)
        for c in chars:
            char_appearances[c].append(rkey)
            if env:
                char_envs[c][env] += 1
        # co-occurrence: every pair in this panel
        for i, a in enumerate(chars):
            for b in chars[i + 1:]:
                if a == b: continue
                co_occur[a][b] += 1
                co_occur[b][a] += 1

    return {
        "char_appearances": {k: v for k, v in char_appearances.items()},
        "co_occur": {k: dict(v) for k, v in co_occur.items()},
        "char_envs": {k: dict(v) for k, v in char_envs.items()},
        "env_panels": dict(env_panels),
    }


# Super-step 3: compute final per-character / per-environment metrics
async def _step_compute_metrics(state: _State) -> dict[str, Any]:
    char_appearances = state.get("char_appearances") or {}
    co_occur = state.get("co_occur") or {}
    char_envs = state.get("char_envs") or {}
    env_panels = state.get("env_panels") or {}

    metrics: dict[str, dict[str, Any]] = {}
    # Frequency-based influence: appearances × 1 + sum(co_occur counts) × 0.3
    for c, panels in char_appearances.items():
        co = co_occur.get(c, {})
        top_co = sorted(co.items(), key=lambda kv: kv[1], reverse=True)[:5]
        envs = char_envs.get(c, {})
        top_envs = sorted(envs.items(), key=lambda kv: kv[1], reverse=True)[:3]
        score = float(len(panels)) + 0.3 * float(sum(co.values()))
        metrics[c] = {
            "appearances": len(panels),
            "score": round(score, 2),
            "topCo": [{"char": k, "count": v} for k, v in top_co],
            "topEnvs": [{"env": k, "count": v} for k, v in top_envs],
        }

    env_metrics: dict[str, dict[str, Any]] = {}
    # Per-environment: panel count + top characters appearing in it
    for env, panels in env_panels.items():
        char_counts: dict[str, int] = defaultdict(int)
        for c, m in char_envs.items():
            if env in m:
                char_counts[c] = m[env]
        top_chars = sorted(char_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
        env_metrics[env] = {
            "panelCount": len(panels),
            "topChars": [{"char": k, "count": v} for k, v in top_chars],
        }

    # Top-10 influence ranking for the response
    top_influence = sorted(
        ({"charRkey": k, **v} for k, v in metrics.items()),
        key=lambda x: x["score"], reverse=True,
    )[:10]

    return {
        "metrics": metrics,
        "env_metrics": env_metrics,
        "topInfluence": top_influence,
    }


# Super-step 4: write metrics back into vertex_mangaka.props
async def _step_write_back(state: _State) -> dict[str, Any]:
    if state.get("dry_run"):
        return {
            "status": "analyzed",
            "counts": {
                "panels": len(state.get("panels") or []),
                "characters": len(state.get("metrics") or {}),
                "environments": len(state.get("env_metrics") or {}),
                "edges": sum(len(v) for v in (state.get("co_occur") or {}).values()) // 2,
            },
            "error": None,
        }

    metrics = state.get("metrics") or {}
    env_metrics = state.get("env_metrics") or {}

    from kotodama.kotoba_datomic import get_kotoba_client
    import asyncio
    client = get_kotoba_client()
    written = {"character": 0, "environment": 0}
    
    for char_rkey, m in metrics.items():
        rows = await asyncio.to_thread(client.select_where, "vertex_mangaka", "rkey", char_rkey, limit=100)
        row = next((r for r in rows if r.get("kind") == "character"), None)
        if not row:
            continue
        props_str = row.get("props")
        try:
            p = json.loads(props_str or "{}") if isinstance(props_str, str) else (props_str or {})
        except Exception:
            p = {}
        p["analytics"] = m
        row["props"] = json.dumps(p, ensure_ascii=False) if isinstance(props_str, str) else p
        await asyncio.to_thread(client.insert_row, "vertex_mangaka", row)
        written["character"] += 1

    for env_rkey, m in env_metrics.items():
        rows = await asyncio.to_thread(client.select_where, "vertex_mangaka", "rkey", env_rkey, limit=100)
        row = next((r for r in rows if r.get("kind") == "environment"), None)
        if not row:
            continue
        props_str = row.get("props")
        try:
            p = json.loads(props_str or "{}") if isinstance(props_str, str) else (props_str or {})
        except Exception:
            p = {}
        p["analytics"] = m
        row["props"] = json.dumps(p, ensure_ascii=False) if isinstance(props_str, str) else p
        await asyncio.to_thread(client.insert_row, "vertex_mangaka", row)
        written["environment"] += 1

    return {
        "status": "analyzed",
        "counts": {
            "panels": len(state.get("panels") or []),
            "characters": written["character"],
            "environments": written["environment"],
            "edges": sum(len(v) for v in (state.get("co_occur") or {}).values()) // 2,
        },
        "error": None,
    }


def _build():
    g: StateGraph = StateGraph(_State)
    g.add_node("load_panels",     _step_load_panels,     retry_policy=RetryPolicy(max_attempts=2))
    g.add_node("extract_refs",    _step_extract_refs)
    g.add_node("compute_metrics", _step_compute_metrics)
    g.add_node("write_back",      _step_write_back,      retry_policy=RetryPolicy(max_attempts=2))
    g.add_edge(START,             "load_panels")
    g.add_edge("load_panels",     "extract_refs")
    g.add_edge("extract_refs",    "compute_metrics")
    g.add_edge("compute_metrics", "write_back")
    g.add_edge("write_back",      END)
    return g


GRAPH = _build().compile(name="analyze_character_graph")
