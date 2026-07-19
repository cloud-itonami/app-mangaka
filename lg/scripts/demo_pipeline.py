"""Run the compose_scene_3d MCP tools end-to-end with mocked deps.

No RW / B2 / OpenAI required — every external is monkey-patched so the
output is fully deterministic. Produces JSON snapshots under
`scripts/demo_outputs/` that show exactly what the topology nodes write
into the LangGraph state channel at each step.

    cd 60-apps/etzhayyim-project-mangaka/lg
    python3 scripts/demo_pipeline.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

_HERE = Path(__file__).resolve().parent
_LG_DIR = _HERE.parent
_OUT_DIR = _HERE / "demo_outputs"
sys.path.insert(0, str(_LG_DIR))


def _dump(name: str, payload: object) -> None:
    _OUT_DIR.mkdir(exist_ok=True)
    p = _OUT_DIR / name
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    print(f"  → {p.relative_to(_LG_DIR)}")


_SYNTHETIC_PANEL = {
    "rkey": "gh-panel-arc01-p1n1",
    "kind": "panel",
    "page_number": 1,
    "panel_number": 1,
    "props_json": json.dumps(
        {
            "shot": "Closeup",
            "characters": ["Chise", "Kota"],
            "environment": "shrine-courtyard-night",
            "action": "Chise dashes toward the laptop, eyes locked on the screen",
            "mood": "urgent, determined",
            "props": [],
        }
    ),
}

_SYNTHETIC_CHARACTERS = {
    "Chise": {
        "vrmBlobKey": "blobs/mangaka/vrm/sha256-chise-demo",
        "name": "赤羽 千世",
        "appearance": {"hair": "long black, red highlights"},
    },
    "Kota": {
        "vrmBlobKey": "blobs/mangaka/vrm/sha256-kota-demo",
        "name": "幸太",
        "appearance": {"hair": "short black"},
    },
}

_SYNTHETIC_ENV = {
    "rkey": "shrine-courtyard-night",
    "biome": "Plains",
    "weather": "clear",
    "basePrompt": "night shrine courtyard, lantern light, stone pavement",
    "layout": [{"name": "lantern", "xform": {}}],
    "seed": 42,
}


# ── mocked RW + B2 ────────────────────────────────────────────────────────


class _FakeCursor:
    def __init__(self, plan: dict) -> None:
        self._plan = plan
        self._result: list | None = None

    async def execute(self, sql: str, params: tuple | list = ()) -> None:
        s = sql.lower()
        if "kind = 'panel'" in s:
            self._result = [
                (
                    _SYNTHETIC_PANEL["rkey"],
                    _SYNTHETIC_PANEL["page_number"],
                    _SYNTHETIC_PANEL["panel_number"],
                    _SYNTHETIC_PANEL["props_json"],
                )
            ]
        elif "kind = 'character'" in s:
            rows = []
            for rk in params:
                if rk in _SYNTHETIC_CHARACTERS:
                    rows.append((rk, json.dumps(_SYNTHETIC_CHARACTERS[rk])))
            self._result = rows
        elif "kind = 'environment'" in s:
            self._result = [(_SYNTHETIC_ENV["rkey"], json.dumps(_SYNTHETIC_ENV))]
        elif "kind = 'asset'" in s:
            self._result = []
        else:
            self._result = []

    async def fetchone(self):
        return self._result[0] if self._result else None

    async def fetchall(self):
        return list(self._result or [])


class _FakeConn:
    def __init__(self, plan: dict) -> None:
        self._cur = _FakeCursor(plan)

    def cursor(self) -> _FakeCursor:
        return self._cur

    async def close(self) -> None:
        pass


async def _fake_connect(url: str, autocommit: bool = True):
    return _FakeConn(plan={})


# ── runner ────────────────────────────────────────────────────────────────


async def main() -> None:
    print("=== compose_scene_3d demo (mocked deps) ===\n")
    os.environ["RW_URL"] = "postgresql://stub"

    # Patch psycopg.AsyncConnection.connect inside the tools module.
    import psycopg as _psycopg_mod  # type: ignore[import-not-found]

    # Import the tools module after path setup so `lg_mangaka.tools` resolves.
    from lg_mangaka import tools

    with patch.object(_psycopg_mod.AsyncConnection, "connect", _fake_connect):
        # ── Step 1: loadPanelPlan ──
        print("[1/8] tool_load_panel_plan")
        plan = await tools.tool_load_panel_plan(panel_rkey="gh-panel-arc01-p1n1")
        _dump("01_panel_plan.json", plan)

        # ── Step 2: resolveAssets ──
        print("[2/8] tool_resolve_assets")
        refs = await tools.tool_resolve_assets(panel_plan=plan["panel_plan"])
        _dump("02_asset_refs.json", refs)

        # ── Step 3: pose_characters (LLM synthesis stubbed) ──
        # Phase A keyword routing → action.dash + Determined for "dashes" + "determined".
        print("[3/8] pose plan (keyword routing demo)")
        pose_plan = {
            ck: {
                "label": "action.dash",
                "expression": "Determined",
                "bones": [],
                "ik_targets": [],
                "root_xform": {
                    "translation": [0.0, 0.0, 0.0],
                    "rotation": [0.0, 0.0, 0.0, 1.0],
                    "scale": [1.0, 1.0, 1.0],
                },
            }
            for ck in refs["asset_refs"]["characters"].keys()
        }
        _dump("03_pose_plan.json", pose_plan)

        # ── Step 4: placeScene ──
        print("[4/8] tool_place_scene")
        scene = tools.tool_place_scene(
            panel_plan=plan["panel_plan"],
            asset_refs=refs["asset_refs"],
            pose_plan=pose_plan,
        )
        _dump("04_scene_dag.json", scene)

        # ── Step 5: cinematography (LLM output stubbed) ──
        # What the cinematography LLM would emit for a tight emotional closeup.
        print("[5/8] camera_plan_raw (stubbed LLM output)")
        camera_plan_raw = {
            "shot": "Closeup",
            "eye": [0.4, 1.65, 1.6],
            "target": [0.0, 1.6, 0.0],
            "up": [0.0, 1.0, 0.0],
            "fov_deg": 38.0,
            "roll_deg": 4.0,
            "dof": {"focus_distance_m": 1.7, "aperture": 1.8},
            "lights": [
                {
                    "role": "Key",
                    "direction": [-0.7, -0.6, -0.3],
                    "colour": [1.0, 0.94, 0.88],
                    "intensity": 4.5,
                },
                {
                    "role": "Fill",
                    "direction": [0.6, -0.3, 0.1],
                    "colour": [0.78, 0.86, 1.0],
                    "intensity": 1.3,
                },
                {
                    "role": "Rim",
                    "direction": [0.2, -0.1, 0.97],
                    "colour": [1.0, 1.0, 1.0],
                    "intensity": 2.3,
                },
            ],
        }
        _dump("05_camera_plan_raw.json", camera_plan_raw)

        # ── Step 6: validateCameraPlan ──
        print("[6/8] tool_validate_camera_plan")
        camera_plan = tools.tool_validate_camera_plan(
            camera_plan_raw=camera_plan_raw,
            fallback_shot="Closeup",
        )
        _dump("06_camera_plan.json", camera_plan)

        # ── Step 7: renderKeyframes (placeholder path — no kami wheel) ──
        print("[7/8] render_keyframes (placeholder — kami wheel not installed)")
        renders_out = await tools.tool_render_keyframes(
            camera_plan=camera_plan["camera_plan"],
            scene_dag=scene["scene_dag"],
            panel_rkey="gh-panel-arc01-p1n1",
            iteration=0,
            render_angles=3,
            sim_seed=42,
        )
        # Sprinkle stub axes so the aggregator has something to chew on.
        for i, r in enumerate(renders_out["renders"]):
            r["critique"] = {
                "axes": {
                    "composition": 0.78 - i * 0.06,
                    "silhouette": 0.82 - i * 0.05,
                    "characterRecognizability": 0.71 + i * 0.03,
                    "framing": 0.74,
                    "mangaShotGrammar": 0.86,
                    "lightingDrama": 0.80,
                    "actionClarity": 0.83 - i * 0.04,
                    "emotionAlignment": 0.55,
                }
            }
        _dump("07_renders.json", renders_out)

        # ── Step 8: aggregateCritique (Hume mocked) ──
        print("[8/8] tool_aggregate_critique (Hume mocked → 0.91 emotion)")
        with patch.object(
            tools, "tool_aggregate_critique", tools.tool_aggregate_critique
        ):
            from lg_mangaka import hume_emotion as _hume

            with patch.object(
                _hume,
                "score_emotion_alignment",
                lambda png, mood: (0.91, {"primary": "determination", "score": 0.91}),
            ):
                # `tool_aggregate_critique` only fires Hume when blobKey is not
                # `pending-*` AND `_blob.is_configured()`. For the demo we
                # short-circuit by skipping Hume (target_mood=None) and rely
                # purely on the LLM axes → keeps the run reproducible.
                aggregated = tools.tool_aggregate_critique(
                    renders=renders_out["renders"],
                    target_mood=None,
                    fallback_score=0.6,
                )
        _dump("08_aggregated.json", aggregated)

        # ── Summary ──
        print()
        print("=== summary ===")
        print(f"panel       : {plan['panel_plan']['rkey']}")
        print(f"characters  : {list(refs['asset_refs']['characters'].keys())}")
        print(f"camera shot : {camera_plan['camera_plan']['camera']['shot']}")
        print(f"camera fov  : {camera_plan['camera_plan']['camera']['fov_deg']}°")
        print(f"camera roll : {camera_plan['camera_plan']['camera']['roll_deg']}°")
        print(f"lights      : {[l['role'] for l in camera_plan['camera_plan']['lights']]}")
        print(f"renders     : {len(renders_out['renders'])} (placeholder — kami wheel missing)")
        print(f"selected    : {aggregated['selected']['blobKey']}")
        print(f"score       : {aggregated['score']:.3f}")
        print()
        print(f"All artefacts under: {_OUT_DIR.relative_to(_LG_DIR)}/")


if __name__ == "__main__":
    asyncio.run(main())
