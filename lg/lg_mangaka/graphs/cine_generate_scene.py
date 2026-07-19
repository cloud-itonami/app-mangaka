"""mangaka `cine_generate_scene` — stages 1-4 of the kami-cine pipeline.

WIT: `etzhayyim:kami-cine@1.0.0` interfaces world-model / usd-scene / neural-geom
/ temporal-field. Produces a reusable 4D scene asset that the sibling
`cine_generate_panel` graph then renders + diffuses into individual panels.

Pregel super-steps (BSP, LangGraph idiom):

  ┌─ start
  │
  ├─ s1_world_model         — LLM-expand prompt + refs → latent world artifact
  │
  ├─ s2_usd_scene           — compose USD layers from the world artifact
  │
  ├─ s3_partition_geom      — split USD bbox into N reconstruction regions
  ├─ s3_neural_geom (×N)    — Send fan-out, one neural-geom node per region
  ├─ s3_merge_geom          — reduce regional splats into one geom CID
  │
  ├─ s4_temporal_field      — evolve geom across the requested frame range
  │
  ├─ finalize               — write vertex_mangaka_cine_run + emit audit
  │
  └─ END

Inputs:
    subject_kind          str   — "mangaka.page" | "mangaka.panel"
    subject_ref           str   — at-uri of the originating record
    prompt                str   — natural-language scene description
    style                 str?  — e.g. "shonen-jump-inked", "ghibli-watercolour"
    reference_cids        list[str]? — style / character / location refs
    world_kind            str   — "threeD" | "twoD" | "twoPointFiveD"
    extents_cm            dict? — {minX,minY,minZ,maxX,maxY,maxZ}
    frame_start, frame_end, fps  — temporal range for stage 4
    geom_regions          int   — fan-out width for stage 3 (default 4)
    pipeline_run_id       str?  — pass in to resume; auto-generated otherwise
    dry_run               bool  — skip persists (inspection only)

Output:
    status              "scene_ready" | "error"
    pipeline_run_id     str
    stage_records       dict[stage → {vertex_id, asset_cid, ...}]
    error               str | None

Persistence per ADR-2605111200: every super-step writes its stage artifact
to graphar.vertex_cine_stage via asyncpg from this pod. CF Workers never
touch RW directly. Finalize step writes the run summary to
graphar.vertex_mangaka_cine_run.
"""

from __future__ import annotations

import logging
import os
from typing import Annotated, Any, Dict, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy, Send

from lg_mangaka import blob as _blob
from lg_mangaka import cine as _cine
from lg_mangaka import comfy as _comfy
from lg_mangaka import llm as _llm
from lg_mangaka.audit import emit_audit_bg

_log = logging.getLogger(__name__)

_APP_DID = os.environ.get("MANGAKA_APP_DID", "did:web:mangaka.etzhayyim.com")
_RW_URL = os.environ.get("RW_URL", "")
_DEFAULT_FPS = 24
_DEFAULT_GEOM_REGIONS = 4


# ── reducers (Send-based fan-out requires mergeable channels) ──────────────

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


# ── state ──────────────────────────────────────────────────────────────────

class _State(TypedDict, total=False):
    # input
    subject_kind: str
    subject_ref: str
    prompt: str
    style: str
    reference_cids: list[str]
    world_kind: str
    extents_cm: dict
    frame_start: int
    frame_end: int
    fps: int
    geom_regions: int
    pipeline_run_id: str
    dry_run: bool

    # super-step outputs
    world_artifact: dict        # {modelCid, seed, tokenCount, previewImageInlineB64?}
    scene_preview: dict         # {imageInlineB64, imageMime, source, prompt, latencyMs}
    usd_artifact: dict          # {usdaCid, usdcCid, bbox, layerCount}
    geom_regions_plan: list     # [{regionId, bbox}]
    geom_fragments: Annotated[Dict[str, Any], _merge_dict]   # {regionId → {assetCid, points}}
    geom_artifact: dict         # merged {assetCid, format, pointCount, bbox}
    temporal_artifact: dict     # {assetCid, format, frameStart, frameEnd, fps}

    # stage record receipts (vertex_cine_stage rows)
    stage_records: Annotated[Dict[str, Any], _merge_dict]

    # output
    status: str
    error: str | None


# ── stage 1: world model ───────────────────────────────────────────────────

async def _s1_world_model(state: _State) -> dict[str, Any]:
    """LLM-expand the user prompt into a structured world description, then
    persist a placeholder world-model artifact blob (the latent payload is a
    JSON envelope; a real implementation hooks into a world-model service)."""
    prompt = state.get("prompt") or ""
    if not prompt:
        return {"error": "prompt required"}

    style = state.get("style") or ""
    refs = state.get("reference_cids") or []
    world_kind = state.get("world_kind") or "threeD"

    expanded = await _llm.llm_json(
        system=(
            "You are a manga storyboard world-architect. Given a panel prompt, "
            "return a JSON object describing the world to generate. Keys: "
            "summary (string), keyAssets (list of strings), lighting (string), "
            "moodPalette (list of color names), cameraHint (string)."
        ),
        user=f"prompt: {prompt}\nstyle: {style}\nworldKind: {world_kind}",
        max_tokens=512,
    ) or {
        "summary": prompt,
        "keyAssets": [],
        "lighting": "ambient",
        "moodPalette": ["neutral"],
        "cameraHint": "MediumShot",
    }

    envelope = {
        "@type": "com.etzhayyim.apps.cine.worldModel",
        "prompt": prompt,
        "style": style,
        "referenceCids": refs,
        "worldKind": world_kind,
        "extentsCm": state.get("extents_cm"),
        "expansion": expanded,
    }
    import json as _json
    body = _json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    blob_key = _blob.put_content_addressed(
        body, prefix=f"cine/world/{_APP_DID}",
        content_type="application/json",
    ) if _blob.is_configured() else f"stub/world/{_cine.new_rkey()}.json"

    # Scene preview image — ask ComfyUI for one representative still of the
    # world we just expanded. Fast path through lg_mangaka.comfy (gateway or
    # raw pod); falls back to an SVG stub when neither is reachable.
    preview_prompt = (
        f"{expanded.get('summary') or prompt}, "
        f"lighting: {expanded.get('lighting') or 'cinematic'}, "
        f"mood: {', '.join(expanded.get('moodPalette') or ['neutral'])}, "
        f"{expanded.get('cameraHint') or 'establishing shot'}, "
        f"style: {style or 'manga inked'}, sharp ink lines, screentone, monochrome"
    )
    comfy_result = await _comfy.refine(
        prompt=preview_prompt, seed=0, steps=12, cfg_x10=70, size="832x1216",
    )
    preview_b64 = comfy_result.get("image_b64") or ""
    preview_cid = (
        _blob.put_content_addressed(
            comfy_result["image_bytes"],
            prefix=f"cine/world-preview/{_APP_DID}",
            content_type=comfy_result.get("image_mime", "image/png"),
        )
        if comfy_result.get("image_bytes") and _blob.is_configured()
        else f"stub/world-preview/{_cine.new_rkey()}.png"
    )

    artifact = {
        "modelCid": blob_key,
        "seed": 0,
        "tokenCount": len(body),
        "expansion": expanded,
        "previewImageCid": preview_cid,
        "previewImageInlineB64": preview_b64,
        "previewImageMime": comfy_result.get("image_mime"),
        "previewSource": comfy_result.get("source"),
    }
    scene_preview = {
        "imageInlineB64": preview_b64,
        "imageMime": comfy_result.get("image_mime"),
        "source": comfy_result.get("source"),
        "prompt": preview_prompt,
        "latencyMs": comfy_result.get("latency_ms"),
    }

    run_id = state.get("pipeline_run_id") or _cine.new_run_id()
    receipt = {}
    if not state.get("dry_run"):
        # Don't write the inline base64 into the DB row — keep CIDs only.
        db_payload = {
            "prompt": prompt, "style": style,
            "referenceCids": refs, "worldKind": world_kind,
            "extentsCm": state.get("extents_cm"),
            "modelCid": blob_key, "seed": 0, "tokenCount": len(body),
            "previewImageCid": preview_cid,
        }
        receipt = await _cine.record_stage(
            stage="worldModel",
            pipeline_run_id=run_id,
            subject_kind=state.get("subject_kind") or "mangaka.page",
            subject_ref=state.get("subject_ref") or "",
            payload=db_payload,
            asset_cid=blob_key,
        )

    return {
        "pipeline_run_id": run_id,
        "world_artifact": artifact,
        "scene_preview": scene_preview,
        "stage_records": {"worldModel": receipt},
    }


# ── stage 2: usd scene ─────────────────────────────────────────────────────

async def _s2_usd_scene(state: _State) -> dict[str, Any]:
    """Compose a USD scene envelope from the world artifact. Real impl
    invokes a USD authoring service; here we serialize the world expansion
    into a USDA-shaped JSON-LD and a tiny binary USDC stub."""
    world = state.get("world_artifact") or {}
    expansion = world.get("expansion") or {}

    usda = (
        "#usda 1.0\n"
        "(\n  defaultPrim = \"World\"\n  metersPerUnit = 1.0\n)\n\n"
        "def Xform \"World\" {\n"
        f"  string mangaka:summary = \"{(expansion.get('summary') or '')[:200]}\"\n"
        "}\n"
    ).encode("utf-8")
    usdc_stub = b"USDC\x00\x00\x00\x01" + usda[:64]

    usda_cid = _blob.put_content_addressed(usda, prefix="cine/usd",
                                            content_type="text/plain") if _blob.is_configured() \
        else f"stub/usd/{_cine.new_rkey()}.usda"
    usdc_cid = _blob.put_content_addressed(usdc_stub, prefix="cine/usd",
                                            content_type="application/octet-stream") if _blob.is_configured() \
        else f"stub/usd/{_cine.new_rkey()}.usdc"

    extents = state.get("extents_cm") or {
        "minX": -500, "minY": -500, "minZ": -500,
        "maxX": 500, "maxY": 500, "maxZ": 500,
    }

    artifact = {
        "usdaCid": usda_cid,
        "usdcCid": usdc_cid,
        "unitsCmPerUnit": 100,
        "bbox": extents,
        "layerCount": 1,
        "defaultPrim": "/World",
    }

    receipt = {}
    if not state.get("dry_run"):
        receipt = await _cine.record_stage(
            stage="usdScene",
            pipeline_run_id=state["pipeline_run_id"],
            subject_kind=state.get("subject_kind") or "mangaka.page",
            subject_ref=state.get("subject_ref") or "",
            payload={**artifact, "worldModelCid": world.get("modelCid")},
            asset_cid=usdc_cid,
        )

    return {"usd_artifact": artifact, "stage_records": {"usdScene": receipt}}


# ── stage 3: neural geom (Send fan-out) ────────────────────────────────────

async def _s3_partition_geom(state: _State) -> dict[str, Any]:
    """Split the USD bbox into geom_regions sub-bboxes for parallel
    reconstruction. Each region becomes one Send target."""
    n = max(1, int(state.get("geom_regions") or _DEFAULT_GEOM_REGIONS))
    bbox = (state.get("usd_artifact") or {}).get("bbox") or {
        "minX": 0, "minY": 0, "minZ": 0, "maxX": 100, "maxY": 100, "maxZ": 100,
    }
    span_x = (bbox["maxX"] - bbox["minX"]) // n or 1
    regions = []
    for i in range(n):
        regions.append({
            "regionId": f"r{i}",
            "bbox": {
                "minX": bbox["minX"] + i * span_x,
                "minY": bbox["minY"],
                "minZ": bbox["minZ"],
                "maxX": bbox["minX"] + (i + 1) * span_x if i < n - 1 else bbox["maxX"],
                "maxY": bbox["maxY"],
                "maxZ": bbox["maxZ"],
            },
        })
    return {"geom_regions_plan": regions}


def _s3_dispatch(state: _State) -> list[Send]:
    regions = state.get("geom_regions_plan") or []
    return [
        Send("s3_neural_geom_one", {
            "region": r,
            "pipeline_run_id": state["pipeline_run_id"],
            "usd_cid": (state.get("usd_artifact") or {}).get("usdcCid"),
        })
        for r in regions
    ]


async def _s3_neural_geom_one(payload: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct one region's neural geometry. Stub: emits a deterministic
    placeholder CID per region; real impl calls splatfacto / instant-ngp."""
    region = payload["region"]
    rid = region["regionId"]
    stub_cid = f"stub/geom/{payload['pipeline_run_id']}/{rid}.splat"
    return {
        "geom_fragments": {
            rid: {
                "assetCid": stub_cid,
                "pointCount": 100_000,
                "bbox": region["bbox"],
            }
        }
    }


async def _s3_merge_geom(state: _State) -> dict[str, Any]:
    """Merge per-region fragments into one neural-geom artifact + persist."""
    frags: dict = state.get("geom_fragments") or {}
    merged_cids = [f["assetCid"] for f in frags.values()]
    total_points = sum(int(f.get("pointCount") or 0) for f in frags.values())
    bbox = (state.get("usd_artifact") or {}).get("bbox") or {}

    manifest = {"format": "gaussianSplat", "fragments": merged_cids}
    import json as _json
    body = _json.dumps(manifest, separators=(",", ":")).encode("utf-8")
    merged_cid = _blob.put_content_addressed(
        body, prefix="cine/geom", content_type="application/json"
    ) if _blob.is_configured() else f"stub/geom/{_cine.new_rkey()}.manifest.json"

    artifact = {
        "assetCid": merged_cid,
        "format": "gaussianSplat",
        "pointCount": total_points,
        "bbox": bbox,
    }

    receipt = {}
    if not state.get("dry_run"):
        receipt = await _cine.record_stage(
            stage="neuralGeom",
            pipeline_run_id=state["pipeline_run_id"],
            subject_kind=state.get("subject_kind") or "mangaka.page",
            subject_ref=state.get("subject_ref") or "",
            payload={
                **artifact,
                "usdSceneCid": (state.get("usd_artifact") or {}).get("usdcCid"),
            },
            asset_cid=merged_cid,
        )

    return {"geom_artifact": artifact, "stage_records": {"neuralGeom": receipt}}


# ── stage 4: temporal field ────────────────────────────────────────────────

async def _s4_temporal_field(state: _State) -> dict[str, Any]:
    geom = state.get("geom_artifact") or {}
    fs = int(state.get("frame_start") or 0)
    fe = int(state.get("frame_end") or 0)
    fps = int(state.get("fps") or _DEFAULT_FPS)
    # Manga still-panel default: 1-frame degenerate temporal field.
    if fe < fs:
        fe = fs

    asset_cid = f"stub/temporal/{state['pipeline_run_id']}.flow"
    artifact = {
        "assetCid": asset_cid,
        "format": "neuralFlow" if fe > fs else "gaussian4d",
        "frameStart": fs,
        "frameEnd": fe,
        "fps": fps,
    }

    receipt = {}
    if not state.get("dry_run"):
        receipt = await _cine.record_stage(
            stage="temporalField",
            pipeline_run_id=state["pipeline_run_id"],
            subject_kind=state.get("subject_kind") or "mangaka.page",
            subject_ref=state.get("subject_ref") or "",
            payload={
                **artifact,
                "neuralGeomCid": geom.get("assetCid"),
                "durationMs": ((fe - fs + 1) * 1000) // max(fps, 1),
            },
            asset_cid=asset_cid,
        )

    return {"temporal_artifact": artifact, "stage_records": {"temporalField": receipt}}


# ── finalize ───────────────────────────────────────────────────────────────

async def _finalize(state: _State) -> dict[str, Any]:
    if state.get("dry_run"):
        return {"status": "scene_ready"}

    records = state.get("stage_records") or {}
    scene_cids = {
        "worldModel": (state.get("world_artifact") or {}).get("modelCid"),
        "usdScene":   (state.get("usd_artifact") or {}).get("usdcCid"),
        "neuralGeom": (state.get("geom_artifact") or {}).get("assetCid"),
        "temporalField": (state.get("temporal_artifact") or {}).get("assetCid"),
    }
    await _cine.record_run(
        pipeline_run_id=state["pipeline_run_id"],
        subject_kind=state.get("subject_kind") or "mangaka.page",
        subject_ref=state.get("subject_ref") or "",
        stages_completed=[s for s in ("worldModel", "usdScene", "neuralGeom", "temporalField")
                          if s in records],
        scene_cids=scene_cids,
        status="scene_ready",
    )

    emit_audit_bg(
        actor=_APP_DID,
        activity="mangaka.cine.sceneReady",
        object_id=state["pipeline_run_id"],
        object_type="mangaka.cineRun",
        attributes={
            "subjectKind": state.get("subject_kind"),
            "subjectRef": state.get("subject_ref"),
            "stages": list(records.keys()),
        },
    )
    return {"status": "scene_ready"}


# ── build ──────────────────────────────────────────────────────────────────

def _build() -> StateGraph:
    g: StateGraph = StateGraph(_State)
    g.add_node("s1_world_model",     _s1_world_model,
               retry_policy=RetryPolicy(max_attempts=2))
    g.add_node("s2_usd_scene",       _s2_usd_scene,
               retry_policy=RetryPolicy(max_attempts=2))
    g.add_node("s3_partition_geom",  _s3_partition_geom)
    g.add_node("s3_neural_geom_one", _s3_neural_geom_one,
               retry_policy=RetryPolicy(max_attempts=2))
    g.add_node("s3_merge_geom",      _s3_merge_geom)
    g.add_node("s4_temporal_field",  _s4_temporal_field,
               retry_policy=RetryPolicy(max_attempts=2))
    g.add_node("finalize",           _finalize)

    g.add_edge(START, "s1_world_model")
    g.add_edge("s1_world_model", "s2_usd_scene")
    g.add_edge("s2_usd_scene", "s3_partition_geom")
    g.add_conditional_edges("s3_partition_geom", _s3_dispatch, ["s3_neural_geom_one"])
    g.add_edge("s3_neural_geom_one", "s3_merge_geom")
    g.add_edge("s3_merge_geom", "s4_temporal_field")
    g.add_edge("s4_temporal_field", "finalize")
    g.add_edge("finalize", END)
    return g


GRAPH = _build().compile(name="cine_generate_scene")
