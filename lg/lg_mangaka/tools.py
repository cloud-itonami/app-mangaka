"""Pure-input/output tool functions for the `compose_scene_3d` Pregel.

These are the runtime implementations referenced by the 6 MCP tool lexicons
under `00-contracts/lexicons/com/etzhayyim/apps/mangaka/tools/`. They are pure
functions of their input — they do NOT touch the LangGraph `_State` channel
— so the same body serves both:

  • Phase A in-tree path: `compose_scene_3d._step_*` nodes call these and
    wrap the result back into the channel.
  • Phase C activation: `vertex_langgraph_assistant.kind='topology'` resolves
    each `mcp://com.etzhayyim.mangaka.tools.*` node by calling the
    corresponding lexicon-validated endpoint, which dispatches here.

Splitting the body off the Pregel node keeps the lexicon contract honest:
the input/output shapes in the lexicon JSONs match these signatures 1:1.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

_log = logging.getLogger(__name__)

_APP_DID = os.environ.get("MANGAKA_APP_DID", "did:web:mangaka.etzhayyim.com")


# -----------------------------------------------------------------------------
# kami-mangaka-scene wheel availability (P11)
# -----------------------------------------------------------------------------
# The PyO3 wheel built by maturin (`40-engine/kami-engine/kami-mangaka-scene/
# pyproject.toml`) ships the headless wgpu renderer that lets
# `tool_render_keyframes` produce real PNG content. When the wheel isn't in
# the pod image (dev mode, partial Dockerfile build), the renderer falls
# back to placeholder blob keys. `is_kami_wheel_available()` is the single
# probe used by tests and health endpoints to surface which path is live.
_KAMI_WHEEL_ERR: str | None = None


def is_kami_wheel_available() -> bool:
    """Return True when the `kami_mangaka_scene` PyO3 wheel is importable.

    Cached after the first call to keep `/health` cheap. Failures are
    recorded in module-level `_KAMI_WHEEL_ERR` so the lifecycle endpoint
    can surface the underlying ImportError message.
    """
    global _KAMI_WHEEL_ERR
    try:
        import kami_mangaka_scene  # type: ignore[import-not-found]  # noqa: F401
    except Exception as exc:
        _KAMI_WHEEL_ERR = f"{type(exc).__name__}: {exc!s}"
        return False
    _KAMI_WHEEL_ERR = None
    return True


def kami_wheel_status() -> dict[str, Any]:
    """Probe + summary for the health endpoint."""
    available = is_kami_wheel_available()
    return {
        "available": available,
        "module": "kami_mangaka_scene",
        "error": _KAMI_WHEEL_ERR if not available else None,
    }


# Manga-grammar camera presets — fallback when the LLM picks an unknown shot
# tag or returns malformed numeric fields. Kept in lockstep with the camera
# defaults used inside `compose_scene_3d.py::_step_cinematography` so the
# in-tree Pregel path (Phase A) and the topology validator node (Phase C)
# produce identical camera plans for the same panel.
_CAMERA_PRESETS: dict[str, dict[str, Any]] = {
    "FullShot":     {"eye": [0.0, 1.6, 5.0],  "target": [0.0, 1.1, 0.0], "fov_deg": 32.0},
    "MediumShot":   {"eye": [0.0, 1.6, 3.0],  "target": [0.0, 1.4, 0.0], "fov_deg": 35.0},
    "Closeup":      {"eye": [0.0, 1.65, 1.4], "target": [0.0, 1.6, 0.0], "fov_deg": 40.0},
    "OverShoulder": {"eye": [-0.6, 1.55, 1.8],"target": [0.0, 1.55, 0.2],"fov_deg": 38.0},
    "Dutch":        {"eye": [0.0, 1.6, 3.0],  "target": [0.0, 1.4, 0.0], "fov_deg": 35.0, "roll_deg": 12.0},
    "BirdsEye":     {"eye": [0.0, 8.0, 2.0],  "target": [0.0, 0.0, 0.0], "fov_deg": 40.0},
    "WormsEye":     {"eye": [0.0, 0.3, 2.5],  "target": [0.0, 1.5, 0.0], "fov_deg": 50.0},
}

_DEFAULT_LIGHTS = [
    {"role": "Key",  "direction": [-0.6, -0.8, -0.4], "colour": [1.0, 0.96, 0.92], "intensity": 4.0},
    {"role": "Fill", "direction": [0.7, -0.4, -0.2],  "colour": [0.86, 0.92, 1.0], "intensity": 1.4},
    {"role": "Rim",  "direction": [0.1, -0.2, 0.95],  "colour": [1.0, 1.0, 1.0],   "intensity": 2.0},
]


# -----------------------------------------------------------------------------
# 1. loadPanelPlan
# -----------------------------------------------------------------------------
async def tool_load_panel_plan(
    *,
    panel_rkey: str,
    rw_url: str | None = None,
) -> dict[str, Any]:
    """Lexicon: com.etzhayyim.mangaka.tools.loadPanelPlan

    Returns `{"panel_plan": {...}}` on success or `{"error": str}` on failure.
    Pure I/O modulo RisingWave read.
    """
    if not panel_rkey:
        return {"error": "panel_rkey required"}

    import asyncio
    from kotodama.kotoba_datomic import get_kotoba_client
    
    def _fetch():
        client = get_kotoba_client()
        res = client.select_where("vertex_mangaka", "rkey", panel_rkey, columns=["rkey", "page_number", "panel_number", "props", "kind"])
        for r in res:
            if r.get("kind") == "panel":
                return r
        return None

    row = await asyncio.to_thread(_fetch)
    if not row:
        return {"error": f"panel {panel_rkey} not found"}
    props: dict[str, Any] = {}
    try:
        props = json.loads(row.get("props") or "{}")
    except Exception:
        props = {}
    plan = {
        "rkey": row.get("rkey"),
        "page_number": row.get("page_number"),
        "panel_number": row.get("panel_number"),
        "shot": props.get("shot") or props.get("cameraAngle") or "MediumShot",
        "characters": props.get("characters") or [],
        "environment": props.get("environment"),
        "action": props.get("action") or "",
        "mood": props.get("mood") or "",
        "props": props.get("props") or [],
    }
    return {"panel_plan": plan}


# -----------------------------------------------------------------------------
# 2. resolveAssets
# -----------------------------------------------------------------------------
async def tool_resolve_assets(
    *,
    panel_plan: dict[str, Any],
    rw_url: str | None = None,
) -> dict[str, Any]:
    """Lexicon: com.etzhayyim.mangaka.tools.resolveAssets

    Resolves character / environment / prop vertex rows referenced by the
    panel_plan, returning their VRM/glTF blob keys + display metadata.
    """
    if not panel_plan:
        return {"error": "panel_plan missing"}

    import asyncio
    from kotodama.kotoba_datomic import get_kotoba_client
    client = get_kotoba_client()

    char_rkeys = list(panel_plan.get("characters") or [])
    env_rkey = panel_plan.get("environment")
    prop_rkeys = [p for p in (panel_plan.get("props") or []) if p]

    refs: dict[str, Any] = {"characters": {}, "environment": None, "props": {}}
    
    def _fetch():
        if char_rkeys:
            for char_rkey in char_rkeys:
                raw = client.q(f'''[:find (pull ?e [:vertex.mangaka/rkey :vertex.mangaka/props])
                                   :where [?e :vertex.mangaka/kind "character"]
                                          [?e :vertex.mangaka/rkey "{char_rkey}"]]''')
                if raw and raw[0]:
                    ent = raw[0][0]
                    try:
                        p = json.loads(ent.get(":vertex.mangaka/props") or "{}")
                    except Exception:
                        p = {}
                    refs["characters"][ent.get(":vertex.mangaka/rkey")] = {
                        "vrm_blob_key": p.get("vrmBlobKey") or p.get("modelBlobKey"),
                        "name": p.get("name"),
                        "appearance": p.get("appearance") or {},
                        "expressions": p.get("expressions") or {},
                    }
        if env_rkey:
            raw = client.q(f'''[:find (pull ?e [:vertex.mangaka/rkey :vertex.mangaka/props])
                               :where [?e :vertex.mangaka/kind "environment"]
                                      [?e :vertex.mangaka/rkey "{env_rkey}"]]''')
            if raw and raw[0]:
                ent = raw[0][0]
                try:
                    p = json.loads(ent.get(":vertex.mangaka/props") or "{}")
                except Exception:
                    p = {}
                refs["environment"] = {
                    "rkey": ent.get(":vertex.mangaka/rkey"),
                    "biome": p.get("biome") or "Plains",
                    "weather": p.get("weather"),
                    "base_prompt": p.get("basePrompt") or "",
                    "layout": p.get("layout") or [],
                    "seed": int(p.get("seed") or 0),
                }
        if prop_rkeys:
            for prop_rkey in prop_rkeys:
                raw = client.q(f'''[:find (pull ?e [:vertex.mangaka/rkey :vertex.mangaka/props])
                                   :where [?e :vertex.mangaka/kind "asset"]
                                          [?e :vertex.mangaka/rkey "{prop_rkey}"]]''')
                if raw and raw[0]:
                    ent = raw[0][0]
                    try:
                        p = json.loads(ent.get(":vertex.mangaka/props") or "{}")
                    except Exception:
                        p = {}
                    refs["props"][ent.get(":vertex.mangaka/rkey")] = {
                        "gltf_blob_key": p.get("gltfBlobKey") or p.get("modelBlobKey"),
                        "name": p.get("name"),
                    }
                    
    await asyncio.to_thread(_fetch)
    return {"asset_refs": refs}


# -----------------------------------------------------------------------------
# 3. placeScene
# -----------------------------------------------------------------------------
def tool_place_scene(
    *,
    panel_plan: dict[str, Any],
    asset_refs: dict[str, Any],
    pose_plan: dict[str, Any],
) -> dict[str, Any]:
    """Lexicon: com.etzhayyim.mangaka.tools.placeScene

    Compose `scene_dag` JSON-LD from plan + refs + poses. Pure CPU.
    """
    env = (asset_refs or {}).get("environment") or {}
    chars = (asset_refs or {}).get("characters") or {}
    props = (asset_refs or {}).get("props") or {}
    poses = pose_plan or {}

    dag: dict[str, Any] = {
        "@context": "https://kami.etzhayyim.com/mangaka-scene/v1",
        "environment": (
            {
                "biome": env.get("biome") or "Plains",
                "weather": env.get("weather"),
                "seed": int(env.get("seed") or 0),
                "ground_size_m": 64.0,
                "layout_anchors": env.get("layout") or [],
            }
            if env
            else None
        ),
        "characters": [
            {
                "rkey": ck,
                "vrm_blob_key": chars.get(ck, {}).get("vrm_blob_key"),
                "pose": poses.get(ck),
            }
            for ck in chars.keys()
        ],
        "props": [
            {"rkey": pk, "gltf_blob_key": props.get(pk, {}).get("gltf_blob_key")}
            for pk in props.keys()
        ],
        "panel": {
            "rkey": (panel_plan or {}).get("rkey"),
            "shot": (panel_plan or {}).get("shot"),
            "action": (panel_plan or {}).get("action"),
            "mood": (panel_plan or {}).get("mood"),
        },
    }
    return {"scene_dag": dag}


# -----------------------------------------------------------------------------
# 4. simulateCharacter (per-character fan-out)
# -----------------------------------------------------------------------------
async def tool_simulate_character(
    *,
    char_rkey: str | None,
    pose: dict[str, Any] | None = None,
    ticks: int = 30,
) -> dict[str, Any]:
    """Lexicon: com.etzhayyim.mangaka.tools.simulateCharacter

    Settle spring bones + cloth for one character. Returns the per-character
    sim slice that the Pregel reducer merges into `sim_result`.

    P1: trivial echo (the heavy lifting lives in the kami-mangaka-scene
    PyO3 wheel — when present in the pod, `_step_render_keyframes` is the
    one that calls `MangakaScene::settle`). This handler exists so the
    Pregel fan-out has a deterministic shape and so the lexicon contract
    is honoured for the future GPU pod path.
    """
    if not char_rkey:
        return {"sim_result": {}}
    return {
        "sim_result": {
            char_rkey: {
                "settled": True,
                "ticks": int(ticks),
                "pose_label": (pose or {}).get("label"),
            }
        }
    }


# -----------------------------------------------------------------------------
# 5. renderKeyframes
# -----------------------------------------------------------------------------
async def tool_render_keyframes(
    *,
    camera_plan: dict[str, Any],
    scene_dag: dict[str, Any],
    panel_rkey: str,
    iteration: int,
    render_angles: int = 3,
    sim_seed: int = 0,
) -> dict[str, Any]:
    """Lexicon: com.etzhayyim.mangaka.tools.renderKeyframes

    Returns `{"renders": [...], "iteration": int}`. When the kami-mangaka-scene
    PyO3 wheel is installed AND B2 is configured, the renders carry real
    content-addressed `blobKey`s. Otherwise `_step_render_keyframes` (the
    caller) inserts pending placeholders — this tool's contract still holds
    in both modes.

    This function is intentionally a thin shim around the inline rendering
    path in `compose_scene_3d._try_real_render` so the lexicon contract is
    stable while the heavier code path stays close to the Pregel state.
    """
    from lg_mangaka.graphs.compose_scene_3d import _try_real_render  # local import — avoid cycle at module load

    # Build a synthetic state dict matching what `_try_real_render` reads.
    synth = {
        "camera_plan": camera_plan,
        "scene_dag": scene_dag,
        "sim_seed": sim_seed,
        "panel_rkey": panel_rkey,
    }
    rendered = _try_real_render(synth, int(render_angles))
    if rendered is None:
        cam = (camera_plan or {}).get("camera") or {}
        shot = cam.get("shot", "MediumShot")
        rendered = [
            {
                "blobKey": f"pending-{panel_rkey}-i{iteration}-a{i}",
                "depthBlobKey": None,
                "outlineBlobKey": None,
                "score": 0.0,
                "angle": shot,
            }
            for i in range(int(render_angles))
        ]
    return {"renders": rendered, "iteration": int(iteration) + 1}


# -----------------------------------------------------------------------------
# 6. validateCameraPlan — P10.2 validator between cinematography & simulate_one
# -----------------------------------------------------------------------------
def tool_validate_camera_plan(
    *,
    camera_plan_raw: dict[str, Any] | None,
    fallback_shot: str = "MediumShot",
) -> dict[str, Any]:
    """Lexicon: com.etzhayyim.mangaka.tools.validateCameraPlan

    Clamp + validate the raw LLM cinematography output. Returns the
    runtime-ready `{camera_plan: {camera, lights, llm}}` channel value.
    Topology placement: `cinematography → validate_camera_plan → simulate_one`.

    Acceptance rules (mirror `_camera_from_llm` + `_lights_from_llm` in
    `compose_scene_3d.py`):
      • `shot` ∈ manga-grammar enum → fallback to MediumShot otherwise
      • numeric `eye/target/up` → 3-float vectors, else preset
      • `fov_deg` clamped to [15, 85]
      • `roll_deg` clamped to [-25, 25]
      • `dof.{focus_distance_m, aperture}` must both be numeric
      • Each light needs role∈{Key,Fill,Rim,Ambient} + 3-float direction +
        3-float colour + numeric intensity; otherwise the LLM's lights are
        replaced by the 3-point default.
    """
    raw = camera_plan_raw or {}
    camera = _camera_from_llm(raw, fallback_shot)
    lights = _lights_from_llm(raw) or list(_DEFAULT_LIGHTS)
    return {
        "camera_plan": {
            "camera": camera,
            "lights": lights,
            "llm": bool(raw),
        }
    }


def _camera_from_llm(parsed: dict[str, Any] | None, fallback_shot: str) -> dict[str, Any]:
    base = dict(_CAMERA_PRESETS.get(fallback_shot) or _CAMERA_PRESETS["MediumShot"])
    base.setdefault("up", [0.0, 1.0, 0.0])
    base.setdefault("roll_deg", 0.0)
    base.setdefault("dof", None)
    base["shot"] = fallback_shot

    if not parsed:
        return base

    shot_val = str(parsed.get("shot") or fallback_shot).strip()
    if shot_val not in _CAMERA_PRESETS:
        shot_val = fallback_shot

    cam = dict(_CAMERA_PRESETS.get(shot_val) or base)
    cam["shot"] = shot_val
    cam.setdefault("up", [0.0, 1.0, 0.0])
    cam.setdefault("roll_deg", 0.0)
    cam.setdefault("dof", None)

    for key in ("eye", "target", "up"):
        vec = parsed.get(key)
        if isinstance(vec, list) and len(vec) == 3 and all(isinstance(v, (int, float)) for v in vec):
            cam[key] = [float(v) for v in vec]
    if isinstance(parsed.get("fov_deg"), (int, float)):
        cam["fov_deg"] = max(15.0, min(85.0, float(parsed["fov_deg"])))
    if isinstance(parsed.get("roll_deg"), (int, float)):
        cam["roll_deg"] = max(-25.0, min(25.0, float(parsed["roll_deg"])))
    dof = parsed.get("dof")
    if isinstance(dof, dict) and isinstance(dof.get("focus_distance_m"), (int, float)) and isinstance(dof.get("aperture"), (int, float)):
        cam["dof"] = {
            "focus_distance_m": float(dof["focus_distance_m"]),
            "aperture": float(dof["aperture"]),
        }
    return cam


def _lights_from_llm(parsed: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    if not parsed:
        return None
    lights = parsed.get("lights")
    if not isinstance(lights, list) or not lights:
        return None
    out: list[dict[str, Any]] = []
    for l in lights:
        if not isinstance(l, dict):
            continue
        role = l.get("role")
        direction = l.get("direction")
        colour = l.get("colour") or l.get("color")
        intensity = l.get("intensity")
        if role not in ("Key", "Fill", "Rim", "Ambient"):
            continue
        if not (isinstance(direction, list) and len(direction) == 3 and all(isinstance(v, (int, float)) for v in direction)):
            continue
        if not (isinstance(colour, list) and len(colour) == 3 and all(isinstance(v, (int, float)) for v in colour)):
            continue
        if not isinstance(intensity, (int, float)):
            continue
        out.append(
            {
                "role": role,
                "direction": [float(v) for v in direction],
                "colour": [float(v) for v in colour],
                "intensity": float(intensity),
            }
        )
    return out or None


# -----------------------------------------------------------------------------
# 6b. aggregateCritique — P10.2b critique-side aggregator
# -----------------------------------------------------------------------------
VISION_AXES = (
    "composition",
    "silhouette",
    "characterRecognizability",
    "framing",
    "mangaShotGrammar",
    "lightingDrama",
    "actionClarity",
    # `emotionAlignment` is data-driven authority via Hume image-head when
    # `target_mood` + B2 + PNG bytes are available; the LLM's self-report
    # is a fallback only.
    "emotionAlignment",
)


def tool_aggregate_critique(
    *,
    renders: list[dict[str, Any]] | None,
    target_mood: str | None = None,
    fallback_score: float = 0.6,
) -> dict[str, Any]:
    """Lexicon: com.etzhayyim.mangaka.tools.aggregateCritique

    Overlays the Hume image-feature `emotionAlignment` score onto each
    render's LLM axes (when target_mood + B2 are available), computes the
    aggregate mean per render, and selects the highest-scoring render.

    Tolerant inputs — operates correctly whether each render carries:
      - a full `critique.axes` dict from an upstream vision LLM (post-P10.1b),
      - only `score` from a deterministic fallback path, or
      - nothing at all (every render falls back to `fallback_score`).

    Returns `{renders: [...scored], selected: <best>, score: <best_score>}`
    so the conditional refinement edge (`score < 0.75 → cinematography`)
    can fire deterministically off the same data the persist step writes.
    """
    if not renders:
        return {"error": "no renders to aggregate"}

    # Import here so the tools.py module remains import-light when the
    # critique path isn't exercised (e.g. unit tests that only touch
    # placeScene / validateCameraPlan).
    from lg_mangaka import blob as _blob
    from lg_mangaka import hume_emotion as _hume

    scored: list[dict[str, Any]] = []
    for render in renders:
        entry = dict(render)
        crit = entry.get("critique") if isinstance(entry.get("critique"), dict) else None
        axes_in = (crit or {}).get("axes") if crit else None
        clean_axes = _clamp_and_fill_axes(axes_in) if isinstance(axes_in, dict) else None

        # Hume overlay — image-feature ground truth for emotionAlignment.
        hume_score: float | None = None
        hume_evidence: dict[str, Any] | None = None
        blob_key = (entry.get("blobKey") or "").strip()
        if (
            target_mood
            and blob_key
            and not blob_key.startswith("pending-")
            and _blob.is_configured()
        ):
            png = _blob.get(blob_key)
            if png:
                try:
                    hume_score, hume_evidence = _hume.score_emotion_alignment(png, target_mood)
                except Exception:
                    hume_score, hume_evidence = None, None

        # Compose final axes + score.
        if clean_axes is not None:
            if isinstance(hume_score, (int, float)):
                clean_axes = dict(clean_axes)
                clean_axes["emotionAlignment"] = max(0.0, min(1.0, float(hume_score)))
            aggregate = sum(clean_axes.values()) / len(clean_axes)
            entry["critique"] = {
                "axes": clean_axes,
                "notes": (crit or {}).get("notes"),
                "score": float(aggregate),
                "humeEvidence": hume_evidence,
            }
            entry["score"] = float(aggregate)
        else:
            # No LLM axes — fall back, but still surface Hume if we have it.
            entry["score"] = float(fallback_score)
            if hume_score is not None:
                entry["critique"] = {
                    "axes": None,
                    "notes": None,
                    "score": float(fallback_score),
                    "humeEvidence": hume_evidence,
                }
        scored.append(entry)

    best = max(scored, key=lambda r: float(r.get("score") or 0.0))
    return {
        "renders": scored,
        "selected": best,
        "score": float(best.get("score") or 0.0),
    }


def _clamp_and_fill_axes(parsed: dict[str, Any]) -> dict[str, float] | None:
    """Pick exactly the canonical axes from `parsed`, clamp each to [0, 1].
    Returns `None` when fewer than half of VISION_AXES are present (treats
    the input as malformed so the caller falls back to fallback_score)."""
    out: dict[str, float] = {}
    for axis in VISION_AXES:
        v = parsed.get(axis)
        if isinstance(v, (int, float)):
            out[axis] = max(0.0, min(1.0, float(v)))
    if len(out) < (len(VISION_AXES) // 2):
        return None
    for axis in VISION_AXES:
        out.setdefault(axis, 0.5)
    return out


# -----------------------------------------------------------------------------
# 6c. attachCharacterVrm — P13 VRM ingestion
# -----------------------------------------------------------------------------
# Glb / VRM 1.0 binary header — every well-formed VRM file starts with
# these four magic bytes (glTF binary container, version 2). We refuse
# uploads that don't carry the magic so a malformed image / random blob
# can't poison the character vertex.
_GLTF_MAGIC = b"glTF"
_VRM_BLOB_PREFIX = "blobs/mangaka/vrm"
_VRM_CONTENT_TYPE = "model/gltf-binary"


async def tool_attach_character_vrm(
    *,
    character_rkey: str,
    vrm_content_b64: str,
    rw_url: str | None = None,
) -> dict[str, Any]:
    """Lexicon: com.etzhayyim.mangaka.tools.attachCharacterVrm

    Validates a VRM 1.0 / glTF binary payload, uploads it to B2 keyed by
    sha256 (`blobs/mangaka/vrm/{sha256hex}`), then writes the resolved
    `vrmBlobKey` back onto the `vertex_mangaka kind='character'` row's
    `props` JSON so the in-tree `resolve_assets` tool + the Phase C
    topology pick it up next render.

    Returns `{blobKey, vertexId}` on success or `{error: str}` otherwise.

    Idempotent — re-uploading the same VRM bytes yields the same blobKey
    (content-addressed) and re-writes the same `vrmBlobKey` on the
    character row. Safe to run from an artist's CLI loop.
    """
    if not character_rkey or not isinstance(character_rkey, str):
        return {"error": "character_rkey required"}
    if not vrm_content_b64 or not isinstance(vrm_content_b64, str):
        return {"error": "vrm_content_b64 required"}

    import base64

    try:
        vrm_bytes = base64.b64decode(vrm_content_b64, validate=True)
    except Exception as exc:
        return {"error": f"vrm_content_b64 not valid base64: {exc!s}"}
    if len(vrm_bytes) < 8 or vrm_bytes[:4] != _GLTF_MAGIC:
        return {"error": "payload missing glTF/VRM magic 'glTF' — reject"}

    # B2 upload (content-addressed → dedup-free re-uploads).
    from lg_mangaka import blob as _blob

    if not _blob.is_configured():
        return {"error": "B2 not configured (B2_ACCESS_KEY_ID / B2_SECRET_ACCESS_KEY)"}
    try:
        blob_key, _ = _blob.put_content_addressed(
            vrm_bytes,
            prefix=_VRM_BLOB_PREFIX,
            content_type=_VRM_CONTENT_TYPE,
        )
    except Exception as exc:
        return {"error": f"B2 PUT failed: {exc!s}"}

    import asyncio
    from kotodama.kotoba_datomic import get_kotoba_client
    
    def _write_back():
        client = get_kotoba_client()
        query_edn = f'''
        [:find (pull ?e [:vertex.mangaka/vertex-id :vertex.mangaka/props])
         :where [?e :vertex.mangaka/kind "character"]
                [?e :vertex.mangaka/rkey "{character_rkey}"]]
        '''
        raw = client.q(query_edn)
        if not raw or not raw[0]:
            return {
                "blobKey": blob_key,
                "vertexId": None,
                "warning": f"character {character_rkey!r} not found in vertex_mangaka",
            }
        
        ent = raw[0][0]
        try:
            existing_props = json.loads(ent.get(":vertex.mangaka/props") or "{}")
        except Exception:
            existing_props = {}
            
        vertex_id = ent.get(":vertex.mangaka/vertex-id")
            
        if existing_props.get("vrmBlobKey") == blob_key:
            return {
                "blobKey": blob_key,
                "vertexId": vertex_id,
                "status": "unchanged",
            }

        existing_props["vrmBlobKey"] = blob_key
        client.insert_row("vertex_mangaka", {
            "vertex_id": vertex_id,
            "props": json.dumps(existing_props, separators=(",", ":"))
        })
        
        return {"blobKey": blob_key, "vertexId": vertex_id, "status": "attached"}
        
    return await asyncio.to_thread(_write_back)


# -----------------------------------------------------------------------------
# 7. persistScene3d
# -----------------------------------------------------------------------------
async def tool_persist_scene_3d(
    *,
    panel_rkey: str,
    iteration: int,
    selected: dict[str, Any],
    scene_dag: dict[str, Any],
    camera_plan: dict[str, Any],
    pose_plan: dict[str, Any],
    score: float,
    sim_seed: int,
    dry_run: bool = False,
    rw_url: str | None = None,
) -> dict[str, Any]:
    """Lexicon: com.etzhayyim.mangaka.tools.persistScene3d

    INSERTs the rendered scene row into `vertex_mangaka_scene_3d` via asyncpg.
    Per ADR-2605111200 this must run on a pod, never on a CF Worker — the
    XRPC handler in the appview routes here through bpmn-dispatcher.
    """
    if dry_run:
        return {"scene_rkey": None, "status": "rendered"}

    import asyncio
    from kotodama.kotoba_datomic import get_kotoba_client
    
    scene_rkey = f"scene3d-{panel_rkey}-{int(time.time())}"
    vertex_id = f"at://{_APP_DID}/com.etzhayyim.mangaka.scene3d/{scene_rkey}"
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    now_date = now_iso[:10]
    
    def _write_back():
        client = get_kotoba_client()
        client.insert_row("vertex_mangaka_scene_3d", {
            "vertex_id": vertex_id,
            "_seq": 0,
            "created_date": now_date,
            "sensitivity_ord": 0,
            "owner_did": _APP_DID,
            "rkey": scene_rkey,
            "panel_id": panel_rkey,
            "scene_jsonld": json.dumps(scene_dag, separators=(",", ":")),
            "camera_jsonld": json.dumps(camera_plan, separators=(",", ":")),
            "pose_jsonld": json.dumps(pose_plan, separators=(",", ":")),
            "render_blob_key": (selected or {}).get("blobKey"),
            "depth_blob_key": (selected or {}).get("depthBlobKey"),
            "outline_blob_key": (selected or {}).get("outlineBlobKey"),
            "score": float(score or 0.0),
            "iteration": int(iteration),
            "sim_seed": int(sim_seed),
            "shot_grammar": (selected or {}).get("angle"),
            "status": "selected",
            "created_at": now_iso,
            "actor_did": _APP_DID,
            "org_did": "anon"
        })
        return {"scene_rkey": scene_rkey, "status": "rendered"}

    return await asyncio.to_thread(_write_back)


# -----------------------------------------------------------------------------
# 7. persistHumeEmotionObservation
# -----------------------------------------------------------------------------
# Hume image-head provides the `emotionAlignment` axis in compose_scene_3d's
# vision critique (lg_mangaka.hume_emotion + ADR-2604300135). Every observation
# is captured here so the dataset can later feed
# `kotodama.primitives.hume_image_head.train_image_centroid` for student-
# model centroid distillation. Target table is the existing
# `vertex_vector_emotion_signal` registry (migration 20260427103000), so no
# new DDL is needed.
async def tool_persist_hume_emotion_observation(
    *,
    panel_rkey: str,
    scene_rkey: str | None,
    iteration: int,
    angle: str | None,
    blob_key: str | None,
    target_mood: str | None,
    target_family: str | None,
    hume_score: float,
    primary: dict[str, Any] | None,
    top_emotions: list[dict[str, Any]] | None,
    image_features: dict[str, float] | None,
    algorithm: str = "visual_heuristic_v1",
    source: str = "compose_scene_3d",
    selected: bool = False,
    dry_run: bool = False,
    rw_url: str | None = None,
) -> dict[str, Any]:
    """Lexicon: com.etzhayyim.mangaka.tools.persistHumeEmotionObservation

    INSERTs one row per render into `vertex_vector_emotion_signal` so the
    full Hume distillation corpus (imageFeatures + topEmotions + author-
    intended mood label) is durable in RisingWave. Per ADR-2605111200 this
    must run on a pod, never on a CF Worker.

    Returns `{"signal_id": str, "status": "stored"}` on success, or
    `{"error": str}` on failure / when `dry_run` skips the write.
    """
    if dry_run:
        return {"signal_id": None, "status": "skipped-dry-run"}
    if not blob_key:
        # Placeholder (pending-*) renders carry no real image — skip.
        return {"signal_id": None, "status": "skipped-no-blob"}

    
    import asyncio
    from kotodama.kotoba_datomic import get_kotoba_client

    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    now_date = now_iso[:10]
    # One row per (panel, iteration, angle/blob) — multiple iterations of the
    # same panel produce a distillation lineage rather than overwriting.
    angle_slug = (angle or "anon").replace("/", "-")
    signal_id = (
        f"hume:{panel_rkey}:i{int(iteration)}:{angle_slug}:{blob_key}"
    )
    source_uri = (
        f"at://{_APP_DID}/com.etzhayyim.mangaka.scene3d/{scene_rkey}"
        if scene_rkey
        else f"at://{_APP_DID}/com.etzhayyim.mangaka.panel/{panel_rkey}"
    )

    # `raw_json` carries everything the centroid trainer needs as a single
    # blob: imageFeatures (the student input) + the author-intended label
    # (target_mood + family). `scores_json` mirrors topEmotions for the
    # top_emotion / top_score index columns to remain queryable.
    raw_payload = {
        "schema": "com.etzhayyim.mangaka.humeObservation.v1",
        "input": {
            "imageFeatures": image_features or {},
        },
        "labels": {
            "targetMood": target_mood,
            "targetFamily": target_family,
        },
        "primary": primary or {},
        "topEmotions": top_emotions or [],
        "humeScore": float(hume_score),
        "algorithm": algorithm,
        "source": source,
        "selected": bool(selected),
        "panelRkey": panel_rkey,
        "sceneRkey": scene_rkey,
        "iteration": int(iteration),
        "angle": angle,
        "blobKey": blob_key,
    }
    primary_name = (primary or {}).get("name")
    primary_score = (primary or {}).get("score")

    def _write_back():
        client = get_kotoba_client()
        client.insert_row("vertex_vector_emotion_signal", {
            "signal_id": signal_id,
            "_seq": 0,
            "created_date": now_date,
            "sensitivity_ord": 0,
            "owner_did": _APP_DID,
            "source_uri": source_uri,
            "source_vertex_id": source_uri,
            "tenant_id": "public",
            "shard_id": 0,
            "modality": "image",
            "provider": "Hume AI",
            "model_id": "hume-image-head",
            "model_version": algorithm,
            "granularity": "panel",
            "language": None,
            "top_emotion": primary_name,
            "top_score": float(primary_score) if isinstance(primary_score, (int, float)) else None,
            "scores_json": json.dumps(top_emotions or [], separators=(",", ":")),
            "raw_json": json.dumps(raw_payload, separators=(",", ":")),
            "analyzed_at": now_iso,
            "org_id": "anon",
            "user_id": "anon",
            "actor_id": "",
            "created_at": now_iso,
        })
        return {"signal_id": signal_id, "status": "stored"}

    return await asyncio.to_thread(_write_back)
