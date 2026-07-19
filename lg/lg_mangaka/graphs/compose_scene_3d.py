"""mangaka `compose_scene_3d` graph — 3D scene composition Pregel pipeline.

ADR-2605141200. Drives `kami-mangaka-scene` (Rust crate, PyO3 wheel
`kami_mangaka_scene` installed in this pod image) from a `vertex_mangaka
kind='panel'` row through:

  1. load_panel_plan        — SELECT the panel + page + chapter context
  2. resolve_assets         — character VRM / env / prop glTF blob_key lookup
  3. pose_characters        — LLM → bone rotations + ARKit expression
  4. place_scene            — build kami-scene-graph DAG (ground + props + chars)
  5. cinematography         — LLM → camera (shot grammar) + 3-point lighting
  6. simulate               — spring bone / cloth / particles / DEC field settle
                              (Send-fan-out per character for parallel spring solve)
  7. render_keyframes       — headless wgpu PNG + depth + outline + toon
  8. critique_and_select    — gpt-4o-mini-vision 7-axis score, route low → step 5
  9. persist                — INSERT vertex_mangaka_scene_3d + B2 PUT blobs

Pregel idiom (matches backfill_mangaka_edges.py / analyze_character_graph.py):
each step reads upstream channels and writes its own; conditional edge from
step 8 re-enters step 5 while iteration < max_iter and score < 0.75.

Send-based fan-out (ADR-2605131600 §2) requires the shallow-dict reducer
applied to `sim_result` because step 6 dispatches one Send per character.

Input:
    panel_rkey         str — required, rkey of kind='panel' vertex
    refine_from_rkey   str — optional, prior vertex_mangaka_scene_3d to refine
    max_iter           int — 1..5 (default 3)
    sim_seed           int — deterministic seed (default 0)
    render_angles      int — alternate camera count per iteration (default 3)
    dry_run            bool — skip persist + B2 PUT (inspection only)

Output:
    status        "rendered" | "error"
    scene_rkey    str | None
    renders       list[{ blobKey, depthBlobKey, outlineBlobKey, score, angle }]
    iterations    int
    error         str | None
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Annotated, Any, Dict, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy, Send

from lg_mangaka import blob as _blob
from lg_mangaka import hume_emotion as _hume
from lg_mangaka import llm as _llm
from lg_mangaka import tools as _tools

_log = logging.getLogger(__name__)

_APP_DID = os.environ.get("MANGAKA_APP_DID", "did:web:mangaka.etzhayyim.com")
_RW_URL = os.environ.get("RW_URL", "")
_B2_BUCKET = os.environ.get("B2_BUCKET", "etzhayyim-pds-prod")
_MAX_ITER_CAP = 5


def _merge_dict(a: Dict[str, Any] | None, b: Dict[str, Any] | None) -> Dict[str, Any]:
    """Shallow dict reducer for Send-based parallel writes (ADR-2605131600 §2)."""
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
    panel_rkey: str
    refine_from_rkey: str
    max_iter: int
    sim_seed: int
    render_angles: int
    dry_run: bool

    # super-step channels
    panel_plan: dict
    asset_refs: dict
    pose_plan: Annotated[Dict[str, Any], _merge_dict]
    scene_dag: dict
    camera_plan: dict
    sim_result: Annotated[Dict[str, Any], _merge_dict]
    renders: Annotated[list, _merge_list]
    selected: dict
    score: float
    iteration: int

    # output
    status: str
    scene_rkey: str
    error: str | None


def _vid(coll: str, rkey: str) -> str:
    return f"at://{_APP_DID}/com.etzhayyim.mangaka.{coll}/{rkey}"


# -----------------------------------------------------------------------------
# Step 1 — load_panel_plan
# -----------------------------------------------------------------------------
async def _step_load_panel_plan(state: _State) -> dict[str, Any]:
    out = await _tools.tool_load_panel_plan(
        panel_rkey=state.get("panel_rkey") or "",
        rw_url=_RW_URL or None,
    )
    if "error" in out:
        return {"status": "error", "error": out["error"]}
    return {"panel_plan": out["panel_plan"], "iteration": 0}


# -----------------------------------------------------------------------------
# Step 2 — resolve_assets
# -----------------------------------------------------------------------------
async def _step_resolve_assets(state: _State) -> dict[str, Any]:
    out = await _tools.tool_resolve_assets(
        panel_plan=state.get("panel_plan") or {},
        rw_url=_RW_URL or None,
    )
    if "error" in out:
        return {"status": "error", "error": out["error"]}
    return {"asset_refs": out["asset_refs"]}


# -----------------------------------------------------------------------------
# Step 3 — pose_characters
# -----------------------------------------------------------------------------
# Pose lexicon labels supported by kami_mangaka_scene::lexicon::pose_preset
# (kept in lockstep with kami-mangaka-scene/src/lexicon.rs).
_POSE_LEXICON = (
    "action.rest",
    "action.idle",
    "action.dash",
    "action.run",
    "action.walk",
    "action.swing",
    "action.attack",
    "action.hit",
    "action.impact",
    "action.fall",
    "action.cower",
    "action.flinch",
    "action.shout",
    "action.yell",
    "action.point",
    "action.reach",
    "action.stand_proud",
    "action.heroic",
)

_ACTION_KEYWORD_MAP = (
    ("dash", "action.dash"), ("run", "action.run"), ("sprint", "action.dash"),
    ("walk", "action.walk"),
    ("swing", "action.swing"), ("slash", "action.swing"), ("attack", "action.attack"),
    ("punch", "action.attack"), ("strike", "action.attack"),
    ("hit", "action.hit"), ("impact", "action.impact"), ("recoil", "action.hit"),
    ("fall", "action.fall"), ("collapse", "action.fall"),
    ("cower", "action.cower"), ("flinch", "action.flinch"), ("hide", "action.cower"),
    ("shout", "action.shout"), ("yell", "action.yell"), ("scream", "action.shout"),
    ("point", "action.point"),
    ("reach", "action.reach"), ("grab", "action.reach"),
    ("stand", "action.stand_proud"), ("heroic", "action.heroic"), ("pose", "action.stand_proud"),
)

_EXPRESSION_KEYWORDS = (
    ("happy", "Happy"), ("joy", "Happy"), ("smile", "Happy"),
    ("angry", "Angry"), ("rage", "Angry"), ("furious", "Angry"),
    ("sad", "Sad"), ("sorrow", "Sad"), ("cry", "Sad"),
    ("surprise", "Surprised"), ("shock", "Surprised"), ("startle", "Surprised"),
    ("determined", "Determined"), ("resolute", "Determined"), ("focus", "Determined"),
    ("pain", "Pained"), ("hurt", "Pained"),
    ("smirk", "Smirk"), ("smug", "Smirk"),
)


def _resolve_pose_label(action_text: str | None) -> str:
    """Map free-form action text → lexicon label. Default action.idle."""
    if not action_text:
        return "action.idle"
    lower = action_text.lower()
    # Direct hit on lexicon label?
    if lower in _POSE_LEXICON:
        return lower
    for kw, label in _ACTION_KEYWORD_MAP:
        if kw in lower:
            return label
    return "action.idle"


def _resolve_expression(mood_text: str | None) -> str:
    if not mood_text:
        return "Neutral"
    lower = mood_text.lower()
    for kw, label in _EXPRESSION_KEYWORDS:
        if kw in lower:
            return label
    return "Neutral"


async def _step_pose_characters(state: _State) -> dict[str, Any]:
    """Resolve per-character pose label + expression from panel_plan.

    P1: deterministic keyword routing (`plan.action` / `plan.mood`) onto
    `kami_mangaka_scene::lexicon` preset names. P3 swaps the routing for an
    LLM call that can emit nuanced explicit bone overrides via `pose.bones`.
    """
    plan = state.get("panel_plan") or {}
    refs = state.get("asset_refs") or {}
    chars = refs.get("characters") or {}

    label = _resolve_pose_label(plan.get("action"))
    expression = _resolve_expression(plan.get("mood"))

    poses: dict[str, Any] = {}
    for ck in chars:
        poses[ck] = {
            "label": label,
            "expression": expression,
            "bones": [],           # explicit overrides (LLM-emitted in P3)
            "ik_targets": [],
            "root_xform": {
                "translation": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0, 1.0],
                "scale": [1.0, 1.0, 1.0],
            },
        }
    return {"pose_plan": poses}


# -----------------------------------------------------------------------------
# Step 4 — place_scene
# -----------------------------------------------------------------------------
def _step_place_scene(state: _State) -> dict[str, Any]:
    """Compose a kami-mangaka-scene JSON-LD descriptor.

    Delegates to `tools.tool_place_scene` (lexicon com.etzhayyim.mangaka.tools.placeScene)
    so the LangGraph node and the future MCP `mcp_tool` resolver share the
    same body. Pure CPU.
    """
    out = _tools.tool_place_scene(
        panel_plan=state.get("panel_plan") or {},
        asset_refs=state.get("asset_refs") or {},
        pose_plan=state.get("pose_plan") or {},
    )
    return {"scene_dag": out["scene_dag"]}


# -----------------------------------------------------------------------------
# Step 5 — cinematography
# -----------------------------------------------------------------------------
# Camera presets + 3-point default lighting + LLM-reply validators live in
# `lg_mangaka.tools` as the Phase C SSoT. Phase A re-uses them through
# `tool_validate_camera_plan` so a tweak to the manga-grammar enum or the
# fov clamp lands in both the in-tree path and the topology MCP tool with a
# single edit.

_CINEMATOGRAPHY_SYSTEM = (
    "You are a manga storyboard cinematographer. Given a panel plan, emit a "
    "JSON object describing the camera and 3-point lighting that best serves "
    "the action and mood. Honour Japanese manga grammar (read right-to-left, "
    "OverShoulder for dialogue, Closeup for impact, WormsEye for heroic "
    "stand, BirdsEye for spatial reveal). Field schema:\n"
    "  shot         (string, one of FullShot/MediumShot/Closeup/OverShoulder/Dutch/BirdsEye/WormsEye)\n"
    "  eye          ([x,y,z] floats, camera position in metres; characters stand near origin, head at y≈1.6)\n"
    "  target       ([x,y,z] floats, look-at point)\n"
    "  up           ([x,y,z] floats, usually [0,1,0])\n"
    "  fov_deg      (float, 22..80; tighter for closeups, wider for action)\n"
    "  roll_deg     (float, default 0; ±15 for Dutch tilts; sign indicates angle direction)\n"
    "  dof          ({focus_distance_m: float, aperture: float} | null; non-null for closeup intimacy)\n"
    "  lights       (array of 3 objects with role∈{Key,Fill,Rim}, direction:[x,y,z] (toward subject), colour:[r,g,b], intensity:float)\n"
    "Return ONLY the JSON object — no commentary."
)


async def _step_cinematography(state: _State) -> dict[str, Any]:
    """LLM → CameraSpec + 3-point lighting respecting manga shot grammar.

    P1: deterministic preset routing from `plan.shot`.
    P3: LLM emits the full CameraSpec (shot/eye/target/fov/roll/dof/lights)
        from richer panel context. Deterministic preset still serves as
        fallback when the LLM is unavailable or emits malformed JSON.
    """
    plan = state.get("panel_plan") or {}
    shot = (plan.get("shot") or "MediumShot").strip()
    iteration = int(state.get("iteration") or 0)

    user_msg = json.dumps(
        {
            "shot": shot,
            "action": plan.get("action"),
            "mood": plan.get("mood"),
            "characters": plan.get("characters"),
            "environment": plan.get("environment"),
            "iteration": iteration,
            "previous_score": state.get("score"),
            "previous_critique": (state.get("selected") or {}).get("critique"),
        },
        ensure_ascii=False,
    )

    parsed: dict[str, Any] | None = None
    try:
        parsed = await _llm.llm_json(
            _CINEMATOGRAPHY_SYSTEM,
            user_msg,
            temperature=0.6 if iteration > 0 else 0.4,
            max_tokens=512,
        )
    except Exception as e:  # defence-in-depth — should already be swallowed
        _log.warning("cinematography LLM raised: %s", e)
        parsed = None

    return _tools.tool_validate_camera_plan(
        camera_plan_raw=parsed, fallback_shot=shot,
    )


# -----------------------------------------------------------------------------
# Step 6 — simulate (Send-based fan-out per character)
# -----------------------------------------------------------------------------
def _step_simulate_dispatch(state: _State) -> list[Send]:
    poses = state.get("pose_plan") or {}
    if not poses:
        return [Send("simulate_one", {"char_rkey": None})]
    return [Send("simulate_one", {"char_rkey": ck, "pose": poses[ck]}) for ck in poses.keys()]


async def _step_simulate_one(payload: dict[str, Any]) -> dict[str, Any]:
    """Per-character spring-bone + cloth settle. Returns merged into `sim_result`.

    Delegates to `tools.tool_simulate_character`
    (lexicon com.etzhayyim.mangaka.tools.simulateCharacter). GPU-side settling
    via the kami-mangaka-scene PyO3 wheel happens in the render step.
    """
    out = await _tools.tool_simulate_character(
        char_rkey=payload.get("char_rkey"),
        pose=payload.get("pose"),
    )
    return out


# -----------------------------------------------------------------------------
# Step 7 — render_keyframes
# -----------------------------------------------------------------------------
async def _step_render_keyframes(state: _State) -> dict[str, Any]:
    """Headless render via `kami_mangaka_scene` wheel + B2 content-addressed PUT.

    - When the PyO3 wheel is installed and B2 is configured, performs the
      actual render and uploads `(base, outline, depth)` PNGs to B2 with
      `blob_key = blobs/anonymous/{sha256hex}`. Identical bytes dedup.
    - When the wheel is absent (dev / staging without GPU pod), falls back to
      pending placeholders so the rest of the Pregel still progresses for
      schema + persistence smoke testing.
    """
    iteration = int(state.get("iteration") or 0) + 1
    angles = max(1, min(int(state.get("render_angles") or 3), 5))
    cam_plan = state.get("camera_plan") or {}
    cam = cam_plan.get("camera") or {}
    shot = cam.get("shot", "MediumShot")
    panel_rkey = state.get("panel_rkey") or "?"

    rendered = _try_real_render(state, angles)
    if rendered is not None:
        return {"renders": rendered, "iteration": iteration}

    # Fallback path — pending placeholders.
    placeholders = []
    for i in range(angles):
        placeholders.append(
            {
                "blobKey": f"pending-{panel_rkey}-i{iteration}-a{i}",
                "depthBlobKey": None,
                "outlineBlobKey": None,
                "score": 0.0,
                "angle": shot,
            }
        )
    return {"renders": placeholders, "iteration": iteration}


def _try_real_render(state: _State, angles: int) -> list[dict[str, Any]] | None:
    """Best-effort: import `kami_mangaka_scene` and render. Returns `None` if
    the wheel is missing, the GPU init fails, or B2 is not configured."""
    try:
        import kami_mangaka_scene as kms  # type: ignore[import-not-found]
    except Exception:
        return None
    if not _blob.is_configured():
        _log.info("B2 not configured; skipping real render PUT")
        return None

    cam_plan = state.get("camera_plan") or {}
    scene_dag = state.get("scene_dag") or {}
    cam = cam_plan.get("camera") or {}
    lights = cam_plan.get("lights") or []

    # Variants — primary camera + ±20° yaw swing for alt angles, capped by `angles`.
    cam_variants = [_camera_variant(cam, idx) for idx in range(angles)]

    try:
        scene = kms.MangakaScene()
        # `set_background_json` accepts the env spec; missing keys are tolerated.
        env = scene_dag.get("environment")
        if env:
            scene.set_background_json(json.dumps(env))
        # P3 ships sky+silhouette renders; full character VRM upload lands in
        # P3.1 alongside com.etzhayyim.mangaka.character.vrmBlobKey ingestion.
        opts_json = json.dumps(
            {"width": 1024, "height": 1448, "passes": 0b0111, "seed": int(state.get("sim_seed") or 0)}
        )
        cams_json = json.dumps(cam_variants)
        pngs: list[bytes] = scene.render_multi_json(cams_json, opts_json)
    except Exception as e:
        _log.warning("kami_mangaka_scene render failed: %s; falling back", e)
        return None

    out: list[dict[str, Any]] = []
    for i, png in enumerate(pngs):
        try:
            key, _ = _blob.put_content_addressed(bytes(png), content_type="image/png")
        except _blob.B2NotConfigured:
            return None
        except Exception as e:
            _log.warning("B2 PUT failed for render %d: %s", i, e)
            return None
        out.append(
            {
                "blobKey": key,
                "depthBlobKey": None,    # depth pass plumbed in P3.1
                "outlineBlobKey": None,  # outline pass plumbed in P3.1
                "score": 0.0,            # filled in critique
                "angle": cam_variants[i].get("shot", "MediumShot"),
            }
        )
    return out


def _camera_variant(base: dict[str, Any], idx: int) -> dict[str, Any]:
    """Derive `idx`-th camera angle from base. idx=0 → as-is; idx>=1 → yaw
    nudge ±20° around target so the critique gets a real choice set without
    asking the LLM for N variants."""
    cam = dict(base)
    if idx == 0:
        return cam
    eye = cam.get("eye") or [0.0, 1.6, 3.0]
    target = cam.get("target") or [0.0, 1.4, 0.0]
    # Rotate eye around target on the XZ plane by ±deg.
    deg = 20.0 if idx == 1 else -20.0
    import math
    theta = math.radians(deg)
    dx = eye[0] - target[0]
    dz = eye[2] - target[2]
    nx = dx * math.cos(theta) - dz * math.sin(theta)
    nz = dx * math.sin(theta) + dz * math.cos(theta)
    cam["eye"] = [target[0] + nx, eye[1], target[2] + nz]
    return cam


# -----------------------------------------------------------------------------
# Step 8 — critique_and_select (P4 — vision 7-axis scoring)
# -----------------------------------------------------------------------------
_VISION_AXES = (
    "composition",
    "silhouette",
    "characterRecognizability",
    "framing",
    "mangaShotGrammar",
    "lightingDrama",
    "actionClarity",
    # `emotionAlignment` is always overridden by the Hume image-head score in
    # `_score_one_render` (see lg_mangaka.hume_emotion). The LLM is still asked
    # to score it so the prompt shape stays self-describing and so we have a
    # cross-check when Hume is unavailable.
    "emotionAlignment",
)

_VISION_SYSTEM = (
    "You score a manga panel render on eight axes. Each axis is a float in "
    "[0, 1] where 1 is publication-ready and 0 is unusable. Be ruthless — "
    "0.5 is the working baseline, 0.75 is the acceptance bar, 0.9+ is rare. "
    "Axes:\n"
    "  composition              — overall layout balance, focal hierarchy, negative space\n"
    "  silhouette               — character readability when reduced to a solid silhouette\n"
    "  characterRecognizability — do faces / body language / costumes match the brief?\n"
    "  framing                  — rule-of-thirds, headroom, leading lines\n"
    "  mangaShotGrammar         — does it serve the declared shot type (FullShot/Closeup/OverShoulder/...)\n"
    "  lightingDrama            — chiaroscuro, rim light, mood\n"
    "  actionClarity            — is the action / emotion legible at-a-glance\n"
    "  emotionAlignment         — does the rendered expression / palette match the brief's declared mood\n"
    "Return ONLY a JSON object with the eight float fields above, plus `notes` "
    "(<=240 chars on the biggest problem) and `improvements` (<=240 chars actionable directives "
    "for the cinematographer next iteration). Do not include any other keys."
)


async def _step_critique_and_select(state: _State) -> dict[str, Any]:
    """7-axis vision critique via OpenAI gpt-4o-mini, with deterministic
    fallback when OPENAI_API_KEY or B2 are missing (dev path / placeholder
    renders). Picks the highest-aggregate render and returns its score so
    `_route_after_critique` can decide whether to refine."""
    renders = state.get("renders") or []
    if not renders:
        return {"status": "error", "error": "no renders produced"}

    plan = state.get("panel_plan") or {}
    cam_plan = state.get("camera_plan") or {}
    cam = cam_plan.get("camera") or {}
    user_prompt = _vision_user_prompt(plan, cam)
    target_mood = plan.get("mood") if isinstance(plan.get("mood"), str) else None

    scored: list[dict[str, Any]] = []
    used_vision = False
    for r in renders:
        score, axes, notes, hume_evidence = await _score_one_render(r, user_prompt, target_mood)
        entry = dict(r)
        if axes is not None:
            used_vision = True
            entry["critique"] = {
                "axes": axes,
                "notes": notes,
                "score": score,
            }
        entry["score"] = score
        # Hume evidence is retained on every entry (even fallback-scored ones,
        # where it's None) so `_step_persist` can store the full distillation
        # corpus per ADR-2604300135 / training pipeline.
        if hume_evidence is not None:
            entry["humeEvidence"] = hume_evidence
        scored.append(entry)

    best = max(scored, key=lambda x: float(x.get("score") or 0.0))
    return {
        "renders": scored,            # propagate scores back into channel
        "selected": best,
        "score": float(best.get("score") or 0.0),
        # `iteration` is set by the renderer; do not overwrite here.
    }


async def _score_one_render(
    render: dict[str, Any],
    user_prompt: str,
    target_mood: str | None = None,
) -> tuple[float, dict[str, float] | None, str | None, dict[str, Any] | None]:
    """Return `(score, axes, notes, hume_evidence)` for a single render.

    `axes` is `None` when vision was unavailable; caller then uses the fallback
    score. `hume_evidence` is the full Hume image-head reply (imageFeatures +
    topEmotions + primary + family + source) and is `None` only when the PNG
    is unavailable — even when the LLM critic fails we still emit Hume
    evidence so the distillation corpus survives critic outages.

    `emotionAlignment` in `axes` is always overridden by the Hume image-head
    score (`lg_mangaka.hume_emotion`) when the PNG is fetchable — the LLM's
    self-report for that axis is treated as a hint, not a measurement."""
    blob_key = (render.get("blobKey") or "").strip()
    # Placeholder renders (pending-*) → deterministic fallback score so the
    # Pregel still progresses for schema smoke tests. No PNG → no Hume row.
    if not blob_key or blob_key.startswith("pending-"):
        return _FALLBACK_SCORE, None, None, None
    if not _blob.is_configured():
        return _FALLBACK_SCORE, None, None, None

    png = _blob.get(blob_key)
    if not png:
        return _FALLBACK_SCORE, None, None, None

    # Image-feature-grounded emotion signal, independent of the LLM critic.
    hume_score, hume_evidence = _hume.score_emotion_alignment(png, target_mood)
    # Retain hume_score on the evidence dict so the persist step doesn't need
    # to re-derive it from the axes (axes may be missing if the LLM critic
    # is unavailable).
    if isinstance(hume_evidence, dict):
        hume_evidence = dict(hume_evidence)
        hume_evidence["humeScore"] = float(hume_score)
        hume_evidence["targetMood"] = target_mood

    import base64
    b64 = base64.b64encode(png).decode("ascii")

    parsed = await _llm.llm_vision_score(
        prompt=f"{_VISION_SYSTEM}\n\nPanel brief:\n{user_prompt}",
        images_b64=[b64],
        max_tokens=384,
    )
    if not parsed:
        return _FALLBACK_SCORE, None, None, hume_evidence

    axes = _axes_from_parsed(parsed)
    if not axes:
        return _FALLBACK_SCORE, None, None, hume_evidence
    axes["emotionAlignment"] = hume_score
    score = sum(axes.values()) / len(axes)
    notes_field = parsed.get("notes") if isinstance(parsed.get("notes"), str) else None
    improvements = parsed.get("improvements") if isinstance(parsed.get("improvements"), str) else None
    note_parts: list[str | None] = [notes_field, improvements]
    primary = hume_evidence.get("primary") or {}
    if primary.get("name") is not None:
        note_parts.append(
            f"hume:{primary.get('name')}@{float(primary.get('score') or 0.0):.2f}"
            f"→{hume_evidence.get('family')}={hume_score:.2f}"
        )
    notes = " / ".join(filter(None, note_parts)) or None
    return float(score), axes, notes, hume_evidence


def _vision_user_prompt(plan: dict[str, Any], cam: dict[str, Any]) -> str:
    return json.dumps(
        {
            "shot_intent": plan.get("shot") or cam.get("shot"),
            "action": plan.get("action"),
            "mood": plan.get("mood"),
            "characters": plan.get("characters"),
            "environment": plan.get("environment"),
            "camera": {
                "shot": cam.get("shot"),
                "fov_deg": cam.get("fov_deg"),
                "roll_deg": cam.get("roll_deg"),
                "dof": cam.get("dof"),
            },
        },
        ensure_ascii=False,
    )


def _axes_from_parsed(parsed: dict[str, Any]) -> dict[str, float] | None:
    """Pick the axes from the LLM reply, clamping each to [0, 1]. Returns
    `None` when fewer than half of `_VISION_AXES` are present (treats the
    reply as malformed so the caller falls back to the deterministic score).

    The caller still overrides `emotionAlignment` with the Hume score after
    this returns, but a malformed LLM reply is a stronger signal that the
    whole panel critique is unreliable, so we keep the same gate."""
    out: dict[str, float] = {}
    for axis in _VISION_AXES:
        v = parsed.get(axis)
        if isinstance(v, (int, float)):
            out[axis] = max(0.0, min(1.0, float(v)))
    if len(out) * 2 < len(_VISION_AXES):
        return None
    # Fill remaining axes with 0.5 (neutral) so the mean stays comparable.
    for axis in _VISION_AXES:
        out.setdefault(axis, 0.5)
    return out


_FALLBACK_SCORE = float(os.environ.get("MANGAKA_CRITIQUE_FALLBACK_SCORE", "0.6"))


def _route_after_critique(state: _State) -> str:
    score = float(state.get("score") or 0.0)
    iteration = int(state.get("iteration") or 0)
    max_iter = min(int(state.get("max_iter") or 3), _MAX_ITER_CAP)
    if score < 0.75 and iteration < max_iter:
        return "cinematography"
    return "persist"


# -----------------------------------------------------------------------------
# Step 9 — persist
# -----------------------------------------------------------------------------
async def _step_persist(state: _State) -> dict[str, Any]:
    """Delegates to `tools.tool_persist_scene_3d`
    (lexicon com.etzhayyim.mangaka.tools.persistScene3d). RisingWave INSERT
    must run on a pod (ADR-2605111200) — never inside the CF Worker.

    Also fans out one `tool_persist_hume_emotion_observation` call per render
    so the full Hume distillation corpus (imageFeatures + topEmotions +
    author-intended mood label) lands in `vertex_vector_emotion_signal` for
    later student-model centroid training (`hume_image_head.train_image_centroid`)."""
    out = await _tools.tool_persist_scene_3d(
        panel_rkey=state.get("panel_rkey") or "unknown",
        iteration=int(state.get("iteration") or 0),
        selected=state.get("selected") or {},
        scene_dag=state.get("scene_dag") or {},
        camera_plan=state.get("camera_plan") or {},
        pose_plan=state.get("pose_plan") or {},
        score=float(state.get("score") or 0.0),
        sim_seed=int(state.get("sim_seed") or 0),
        dry_run=bool(state.get("dry_run")),
        rw_url=_RW_URL or None,
    )
    if "error" in out:
        return {"status": "error", "error": out["error"]}

    scene_rkey = out.get("scene_rkey")
    panel_rkey = state.get("panel_rkey") or "unknown"
    iteration = int(state.get("iteration") or 0)
    selected_blob = (state.get("selected") or {}).get("blobKey")
    dry_run = bool(state.get("dry_run"))

    # Persist every render's Hume observation, not just the selected one.
    # Rejected candidates are valuable negative examples for the centroid
    # trainer. Failures are logged but do not fail the Pregel — the scene
    # row above is already durable.
    hume_persisted = 0
    for r in state.get("renders") or []:
        evidence = r.get("humeEvidence") if isinstance(r, dict) else None
        if not isinstance(evidence, dict):
            continue
        try:
            res = await _tools.tool_persist_hume_emotion_observation(
                panel_rkey=panel_rkey,
                scene_rkey=scene_rkey,
                iteration=iteration,
                angle=r.get("angle"),
                blob_key=r.get("blobKey"),
                target_mood=evidence.get("targetMood"),
                target_family=evidence.get("family"),
                hume_score=float(evidence.get("humeScore") or 0.0),
                primary=evidence.get("primary"),
                top_emotions=evidence.get("topEmotions"),
                image_features=evidence.get("imageFeatures") if isinstance(evidence.get("imageFeatures"), dict) else None,
                algorithm=evidence.get("algorithm") or "visual_heuristic_v1",
                source="compose_scene_3d",
                selected=(r.get("blobKey") == selected_blob),
                dry_run=dry_run,
                rw_url=_RW_URL or None,
            )
            if isinstance(res, dict) and res.get("status") == "stored":
                hume_persisted += 1
        except Exception as exc:  # noqa: BLE001
            _log.warning("hume observation persist failed: %s", exc)

    return {
        "status": out.get("status") or "rendered",
        "scene_rkey": scene_rkey,
        "error": None,
        "hume_persisted": hume_persisted,
    }


# -----------------------------------------------------------------------------
# Graph build
# -----------------------------------------------------------------------------
def _build() -> StateGraph:
    g: StateGraph = StateGraph(_State)
    g.add_node("load_panel_plan",     _step_load_panel_plan,     retry_policy=RetryPolicy(max_attempts=2))
    g.add_node("resolve_assets",      _step_resolve_assets,      retry_policy=RetryPolicy(max_attempts=2))
    g.add_node("pose_characters",     _step_pose_characters)
    g.add_node("place_scene",         _step_place_scene)
    g.add_node("cinematography",      _step_cinematography)
    g.add_node("simulate_one",        _step_simulate_one)
    g.add_node("render_keyframes",    _step_render_keyframes,    retry_policy=RetryPolicy(max_attempts=2))
    g.add_node("critique_and_select", _step_critique_and_select)
    g.add_node("persist",             _step_persist,             retry_policy=RetryPolicy(max_attempts=2))

    g.add_edge(START,                "load_panel_plan")
    g.add_edge("load_panel_plan",    "resolve_assets")
    g.add_edge("resolve_assets",     "pose_characters")
    g.add_edge("pose_characters",    "place_scene")
    g.add_edge("place_scene",        "cinematography")
    # Send fan-out: cinematography → simulate_one (one per character) → render_keyframes
    g.add_conditional_edges("cinematography", _step_simulate_dispatch, ["simulate_one"])
    g.add_edge("simulate_one",       "render_keyframes")
    g.add_edge("render_keyframes",   "critique_and_select")
    # Conditional refinement loop (score<0.75 ∧ iter<max_iter)
    g.add_conditional_edges("critique_and_select", _route_after_critique,
                            {"cinematography": "cinematography", "persist": "persist"})
    g.add_edge("persist",            END)
    return g


def build_graph():
    """Factory entry for `vertex_langgraph_assistant.kind='py_factory'` resolution.

    The RW-resident loader (`kotodama.langgraph_loader`) imports this dotted
    path (`lg_mangaka.graphs.compose_scene_3d:build_graph`) and invokes it
    to produce the compiled StateGraph at deployment activation time. The
    module-level `GRAPH` constant remains for direct in-process imports from
    `lg_mangaka.server` until every dispatch path goes through the loader.
    """
    return _build().compile(name="compose_scene_3d")


GRAPH = build_graph()
