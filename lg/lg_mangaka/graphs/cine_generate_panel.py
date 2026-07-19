"""mangaka `cine_generate_panel` — stages 5-6 of the kami-cine pipeline.

Consumes the scene artifacts produced by `cine_generate_scene` (looked up
via pipeline_run_id) and emits one rendered + diffusion-refined image per
panel. Designed to be invoked once per page with the full list of panels
on that page; the per-panel work fan-outs in parallel via Pregel Send.

WIT: `etzhayyim:kami-cine@1.0.0` interfaces neural-render / diffusion-pass.

Pregel super-steps:

  ┌─ start
  │
  ├─ load_scene           — SELECT vertex_mangaka_cine_run → asset CIDs
  ├─ plan_panels          — normalize input panels[] (framing, characters)
  │
  ├─ panel_dispatch       — Send(per_panel_render) × N
  ├─ per_panel_render(×N) — stage 5 (neuralRender) + stage 6 (diffusionPass)
  │                         + persist both stage rows + persist panel binding
  │
  ├─ aggregate            — collect panel results
  ├─ finalize             — vertex_mangaka_cine_run.status = panels_rendered
  │                         + audit emit
  └─ END

Inputs:
    pipeline_run_id        str    — required, must match a scene_ready run
    page_rkey              str?   — optional page binding for vertex rows
    panels                 list[{panel_rkey, framing, charactersAppearing,
                                 cameraHint, diffusionModel, refImageCids}]
    samples_per_pixel      int    — 1..256 (default 16)
    diffusion_model        str    — default "sdxl-refiner-1.0"
    sampler_steps          int    — default 28
    cfg_scale_x10          int    — CFG×10, default 75 (= 7.5)
    denoise_permille       int    — 0..1000, default 350 (= 0.35)
    seed                   int    — default 0 (= random per-panel)
    dry_run                bool   — skip persists

Output:
    status     "panels_rendered" | "error"
    panels     list[{panel_rkey, render_cid, refined_cid, panel_blob_key, score}]
    error      str | None
"""

from __future__ import annotations

import logging
import os
import secrets
import asyncio
from typing import Annotated, Any, Dict, TypedDict

from kotodama.kotoba_datomic import get_kotoba_client
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy, Send

from lg_mangaka import blob as _blob
from lg_mangaka import cine as _cine
from lg_mangaka import comfy as _comfy
from lg_mangaka import llm as _llm
from lg_mangaka.audit import emit_audit_bg

_log = logging.getLogger(__name__)

_APP_DID = os.environ.get("MANGAKA_APP_DID", "did:web:mangaka.etzhayyim.com")
_DEFAULT_RESOLUTION = (1080, 1920)  # portrait, mangaka standard


def _merge_dict(a: Dict[str, Any] | None, b: Dict[str, Any] | None) -> Dict[str, Any]:
    if not a:
        return dict(b or {})
    if not b:
        return dict(a)
    out = dict(a)
    out.update(b)
    return out


def _merge_list(a: list | None, b: list | None) -> list:
    if not a:
        return list(b or [])
    if not b:
        return list(a)
    return list(a) + list(b)


class _State(TypedDict, total=False):
    # input
    pipeline_run_id: str
    page_rkey: str
    panels: list[dict]
    samples_per_pixel: int
    diffusion_model: str
    sampler_steps: int
    cfg_scale_x10: int
    denoise_permille: int
    seed: int
    dry_run: bool

    # loaded scene context
    scene: dict             # {worldModel, usdScene, neuralGeom, temporalField}
    panel_plan: list        # normalized panel specs

    # super-step output (fan-in via list reducer)
    panel_results: Annotated[list, _merge_list]
    stage_records: Annotated[Dict[str, Any], _merge_dict]

    # output
    status: str
    error: str | None


# ── load scene ─────────────────────────────────────────────────────────────

async def _load_scene(state: _State) -> dict[str, Any]:
    run_id = state.get("pipeline_run_id") or ""
    if not run_id:
        # Even without a real prior run, dry_run should let panel rendering
        # exercise — synthesize a sentinel run_id so downstream steps key.
        if state.get("dry_run"):
            return {
                "pipeline_run_id": "dry-run-no-scene",
                "scene": _synthetic_scene("dry-run-no-scene"),
            }
        return {"error": "pipeline_run_id required"}

    # dry_run skips the lookup entirely — we fabricate a plausible scene
    # so the per-panel render fan-out can demo the SSE + image preview path
    # without requiring cine_generate_scene to have been run.
    if state.get("dry_run"):
        return {"scene": _synthetic_scene(run_id)}

    vid = _cine.mangaka_vertex_id("cineRun", run_id)
    client = get_kotoba_client()
    row = await asyncio.to_thread(
        client.select_first_where,
        "vertex_mangaka_cine_run",
        "vertex_id",
        vid,
        ["scene_world_cid", "scene_usd_cid", "scene_geom_cid", "scene_temporal_cid", "status"]
    )

    if not row:
        return {"error": f"scene run not found: {run_id}"}

    scene = {
        "worldModelCid":   row.get("scene_world_cid"),
        "usdSceneCid":     row.get("scene_usd_cid"),
        "neuralGeomCid":   row.get("scene_geom_cid"),
        "temporalFieldCid": row.get("scene_temporal_cid"),
        "priorStatus":     row.get("status"),
    }
    if not scene["temporalFieldCid"]:
        return {"error": f"scene run {run_id} not ready (status={row.get('status')})"}
    return {"scene": scene}


def _synthetic_scene(run_id: str) -> dict[str, str]:
    return {
        "worldModelCid":   f"stub/world/{run_id}.json",
        "usdSceneCid":     f"stub/usd/{run_id}.usdc",
        "neuralGeomCid":   f"stub/geom/{run_id}.manifest.json",
        "temporalFieldCid": f"stub/temporal/{run_id}.flow",
        "priorStatus":     "scene_ready_synthetic",
    }


# ── plan panels ────────────────────────────────────────────────────────────

async def _plan_panels(state: _State) -> dict[str, Any]:
    """Validate + default-fill incoming panel specs."""
    panels_in = state.get("panels") or []
    if not panels_in:
        return {"error": "panels[] required (at least one)"}

    plan = []
    for i, p in enumerate(panels_in):
        plan.append({
            "panel_rkey":       p.get("panel_rkey") or f"panel-{i}",
            "framing":          p.get("framing") or "MediumShot",
            "charactersAppearing": p.get("charactersAppearing") or [],
            "cameraHint":       p.get("cameraHint") or "static",
            "diffusionModel":   p.get("diffusionModel") or state.get("diffusion_model") or "sdxl-refiner-1.0",
            "refImageCids":     p.get("refImageCids") or [],
            "framePattern":     f"panel-{i:03d}.####.exr",
        })
    return {"panel_plan": plan}


# ── per-panel render (Send fan-out) ────────────────────────────────────────

def _panel_dispatch(state: _State) -> list[Send]:
    plan = state.get("panel_plan") or []
    return [
        Send("per_panel_render", {
            "panel": p,
            "scene": state["scene"],
            "pipeline_run_id": state["pipeline_run_id"],
            "page_rkey": state.get("page_rkey"),
            "samples_per_pixel": state.get("samples_per_pixel") or 16,
            "sampler_steps": state.get("sampler_steps") or 28,
            "cfg_scale_x10": state.get("cfg_scale_x10") or 75,
            "denoise_permille": state.get("denoise_permille") or 350,
            "seed": state.get("seed") or 0,
            "dry_run": state.get("dry_run") or False,
        })
        for p in plan
    ]


async def _per_panel_render(payload: dict[str, Any]) -> dict[str, Any]:
    """Stages 5 (neuralRender) + 6 (diffusionPass) for one panel.

    Real impl dispatches to a rendering pod and a diffusion pod; this stub
    produces deterministic placeholder CIDs while exercising the full
    persistence path so downstream code (subscribeRepos handlers, panel
    binders) can be wired and tested end-to-end.
    """
    panel = payload["panel"]
    scene = payload["scene"]
    rid = payload["pipeline_run_id"]
    panel_rkey = panel["panel_rkey"]
    width, height = _DEFAULT_RESOLUTION
    seed = int(payload.get("seed") or 0) or secrets.randbelow(2**32)

    # ── stage 5: neural render ──
    render_seq_cid = f"stub/render/{rid}/{panel_rkey}.exr.manifest"
    render_payload = {
        "temporalFieldCid": scene.get("temporalFieldCid"),
        "cameraPathCid": f"stub/camera/{rid}/{panel_rkey}.usda",
        "resolution": {"width": width, "height": height},
        "aovs": ["beauty", "depth", "normal", "albedo"],
        "frameStart": 0,
        "frameEnd": 0,
        "samplesPerPixel": int(payload["samples_per_pixel"]),
        "sequenceCid": render_seq_cid,
        "framePattern": panel["framePattern"],
        "frameCount": 1,
    }
    render_receipt: dict = {}
    if not payload["dry_run"]:
        render_receipt = await _cine.record_stage(
            stage="neuralRender",
            pipeline_run_id=rid,
            subject_kind="mangaka.panel",
            subject_ref=f"at://{_APP_DID}/com.etzhayyim.mangaka.panel/{panel_rkey}",
            payload=render_payload,
            asset_cid=render_seq_cid,
        )

    # ── stage 6: diffusion refine (ComfyUI when COMFY_POD_URL set, else stub SVG) ──
    chars = ", ".join(panel.get("charactersAppearing") or []) or "scene"
    diffusion_prompt = (
        f"manga inked panel, {panel['framing']}, characters: {chars}, "
        f"sharp ink lines, screentone, monochrome, dynamic composition"
    )
    comfy_result = await _comfy.refine(
        prompt=diffusion_prompt,
        seed=seed,
        steps=int(payload["sampler_steps"]),
        cfg_x10=int(payload["cfg_scale_x10"]),
    )
    # Upload to B2 when configured; otherwise use a stub CID so the row still keys.
    if comfy_result.get("image_bytes") and _blob.is_configured():
        refined_cid = _blob.put_content_addressed(
            comfy_result["image_bytes"],
            prefix=f"cine/diffusion/{rid}",
            content_type=comfy_result.get("image_mime", "image/png"),
        )
    else:
        refined_cid = f"stub/diffusion/{rid}/{panel_rkey}.png"

    diffusion_payload = {
        "neuralRenderCid": render_seq_cid,
        "model": comfy_result.get("model") or panel["diffusionModel"],
        "samplerSteps": int(payload["sampler_steps"]),
        "cfgScaleX10": int(payload["cfg_scale_x10"]),
        "denoisePermille": int(payload["denoise_permille"]),
        "refImageCids": panel["refImageCids"],
        "seed": int(comfy_result.get("seed") or seed),
        "sequenceCid": refined_cid,
        "framePattern": panel["framePattern"].replace(".exr", ".png"),
        "frameCount": 1,
        "promptUsed": diffusion_prompt,
        # Inline base64 so Studio UI can <img> it without a /blob proxy.
        # ~1 MB per panel; OK for inspection, strip for prod when needed.
        "imageInlineB64": comfy_result.get("image_b64"),
        "imageMime": comfy_result.get("image_mime"),
        "source": comfy_result.get("source"),
        "latencyMs": comfy_result.get("latency_ms"),
        "comfyError": comfy_result.get("error"),
    }
    diffusion_receipt: dict = {}
    if not payload["dry_run"]:
        # Don't fat the DB row with the base64 blob — keep the asset_cid for retrieval.
        db_payload = {k: v for k, v in diffusion_payload.items() if k != "imageInlineB64"}
        diffusion_receipt = await _cine.record_stage(
            stage="diffusionPass",
            pipeline_run_id=rid,
            subject_kind="mangaka.panel",
            subject_ref=f"at://{_APP_DID}/com.etzhayyim.mangaka.panel/{panel_rkey}",
            payload=db_payload,
            asset_cid=refined_cid,
        )

    # ── critique (lightweight, optional vision score) ──
    score = 75  # default mid-range integer score in 0..100
    crit = await _llm.llm_json(
        system="Score this manga panel render on composition, ink quality, "
               "and storytelling 0-100. Return {score:int, notes:str}.",
        user=f"panel framing={panel['framing']} chars={chars}",
        max_tokens=128,
    )
    if isinstance(crit, dict) and isinstance(crit.get("score"), (int, float)):
        score = max(0, min(100, int(crit["score"])))

    # ── persist panel binding ──
    panel_binding: dict = {}
    if not payload["dry_run"]:
        panel_binding = await _cine.record_panel(
            panel_rkey=panel_rkey,
            page_rkey=payload.get("page_rkey"),
            pipeline_run_id=rid,
            render_blob_key=render_seq_cid,
            refined_blob_key=refined_cid,
            panel_blob_key=refined_cid,
            score=score,
        )

    return {
        "panel_results": [{
            "panel_rkey": panel_rkey,
            "render_cid": render_seq_cid,
            "refined_cid": refined_cid,
            "panel_blob_key": refined_cid,
            "score": score,
            "imageInlineB64": comfy_result.get("image_b64"),
            "imageMime": comfy_result.get("image_mime"),
            "source": comfy_result.get("source"),
            "binding": panel_binding,
        }],
        "stage_records": {
            f"neuralRender:{panel_rkey}": render_receipt,
            f"diffusionPass:{panel_rkey}": diffusion_receipt,
        },
    }


# ── aggregate + finalize ───────────────────────────────────────────────────

async def _aggregate(state: _State) -> dict[str, Any]:
    results = state.get("panel_results") or []
    return {"panel_results": []}  # no-op; reducer already collected, this just gates finalize


async def _finalize(state: _State) -> dict[str, Any]:
    if state.get("dry_run"):
        return {"status": "panels_rendered"}

    rid = state["pipeline_run_id"]
    # promote the run status forward
    await _cine.record_run(
        pipeline_run_id=rid,
        subject_kind="mangaka.page" if state.get("page_rkey") else "mangaka.panel",
        subject_ref=(
            _cine.mangaka_vertex_id("page", state["page_rkey"])
            if state.get("page_rkey") else ""
        ),
        stages_completed=list(_cine.STAGE_NAMES[:6]),
        scene_cids={
            "worldModel": (state.get("scene") or {}).get("worldModelCid"),
            "usdScene":   (state.get("scene") or {}).get("usdSceneCid"),
            "neuralGeom": (state.get("scene") or {}).get("neuralGeomCid"),
            "temporalField": (state.get("scene") or {}).get("temporalFieldCid"),
        },
        status="panels_rendered",
    )

    results = state.get("panel_results") or []
    emit_audit_bg(
        actor=_APP_DID,
        activity="mangaka.cine.panelsRendered",
        object_id=rid,
        object_type="mangaka.cineRun",
        attributes={
            "panelCount": len(results),
            "avgScore": sum(int(r.get("score") or 0) for r in results) // max(len(results), 1),
            "pageRkey": state.get("page_rkey"),
        },
    )
    return {"status": "panels_rendered"}


# ── build ──────────────────────────────────────────────────────────────────

def _build() -> StateGraph:
    g: StateGraph = StateGraph(_State)
    g.add_node("load_scene",        _load_scene,
               retry_policy=RetryPolicy(max_attempts=2))
    g.add_node("plan_panels",       _plan_panels)
    g.add_node("per_panel_render",  _per_panel_render,
               retry_policy=RetryPolicy(max_attempts=2))
    g.add_node("aggregate",         _aggregate)
    g.add_node("finalize",          _finalize)

    g.add_edge(START, "load_scene")
    g.add_edge("load_scene", "plan_panels")
    g.add_conditional_edges("plan_panels", _panel_dispatch, ["per_panel_render"])
    g.add_edge("per_panel_render", "aggregate")
    g.add_edge("aggregate", "finalize")
    g.add_edge("finalize", END)
    return g


GRAPH = _build().compile(name="cine_generate_panel")
