"""Hume-derived emotion alignment for compose_scene_3d critique.

Wraps `kotodama.primitives.hume_image_head.predict_image_emotion` (pure
stdlib PNG decode + visual-feature heuristic / student centroid) and projects
its top emotions onto a small set of mood families used by mangaka panel
manifests (`plan.mood` / `gh:tone`). The result is a single [0, 1] score that
slots into compose_scene_3d's `_VISION_AXES` as `emotionAlignment`.

This stays local-only: no Hume API call. The hume_image_head primitive is the
distilled student model trained from Hume Expression Measurement signals (see
`60-apps/etzhayyim-project-hume/`), so adding it here gives the critique an
image-feature-grounded emotion signal independent of the LLM's self-report.
"""

from __future__ import annotations

from typing import Any

try:
    from kotodama.primitives.hume_image_head import predict_image_emotion
except Exception:  # pragma: no cover — kotodama optional at import time
    predict_image_emotion = None  # type: ignore[assignment]


# Mood family → emotion labels emitted by hume_image_head._HEURISTIC.
# Keys are the canonical family names; values are the emotion labels that count
# as a "hit" when the target mood resolves to that family.
_FAMILY_EMOTIONS: dict[str, frozenset[str]] = {
    "joy":        frozenset({"joy", "excitement", "gratitude", "relief"}),
    "calm":       frozenset({"calm", "relief", "gratitude"}),
    "sad":        frozenset({"sadness"}),
    "fear":       frozenset({"anxiety", "doubt"}),
    "anger":      frozenset({"anger"}),
    "excitement": frozenset({"excitement", "anger", "joy"}),
}

# Free-text mood synonyms → family. Match is case-insensitive substring on
# tokens; the longest match wins so "ominous tension" resolves to "fear".
_MOOD_SYNONYMS: tuple[tuple[str, str], ...] = (
    # joy family
    ("triumph", "joy"), ("triumphant", "joy"), ("victory", "joy"),
    ("joy", "joy"), ("joyful", "joy"), ("happy", "joy"), ("glad", "joy"),
    ("celebrat", "joy"), ("relief", "joy"), ("grateful", "joy"),
    # calm family
    ("calm", "calm"), ("quiet", "calm"), ("contemplat", "calm"),
    ("serene", "calm"), ("peaceful", "calm"), ("still", "calm"),
    ("reflect", "calm"), ("melanchol", "sad"),
    # sad family
    ("sad", "sad"), ("sorrow", "sad"), ("grief", "sad"), ("mourn", "sad"),
    ("despair", "sad"), ("loss", "sad"),
    # fear family
    ("fear", "fear"), ("afraid", "fear"), ("dread", "fear"),
    ("ominous", "fear"), ("foreboding", "fear"), ("anxi", "fear"),
    ("tense", "fear"), ("tension", "fear"), ("uneasy", "fear"),
    ("doubt", "fear"), ("suspicious", "fear"),
    # anger family
    ("anger", "anger"), ("angry", "anger"), ("rage", "anger"),
    ("furi", "anger"), ("hostile", "anger"), ("wrath", "anger"),
    # excitement family
    ("excit", "excitement"), ("action", "excitement"), ("dynamic", "excitement"),
    ("intense", "excitement"), ("energetic", "excitement"), ("frantic", "excitement"),
    ("emotional", "excitement"),  # generic "emotional" → energetic by convention
)


def resolve_mood_family(mood_text: str | None) -> str | None:
    """Return the mood family for free-text manifest mood, or None if unknown."""
    if not mood_text or not isinstance(mood_text, str):
        return None
    lowered = mood_text.lower()
    # Longest-prefix-match wins so "ominous tension" → fear, not sad-from-"melanchol".
    best: tuple[int, str] | None = None
    for needle, family in _MOOD_SYNONYMS:
        if needle in lowered:
            if best is None or len(needle) > best[0]:
                best = (len(needle), family)
    return best[1] if best else None


def score_emotion_alignment(
    png_bytes: bytes,
    target_mood: str | None,
    mime_type: str = "image/png",
    *,
    model: dict[str, Any] | None = None,
) -> tuple[float, dict[str, Any]]:
    """Score how well `png_bytes` expresses the manifest's `target_mood`.

    Returns `(score, evidence)`:
      * `score ∈ [0, 1]` — Hume top-emotion mass that lands in the target
        family. 0.5 (neutral) when target mood is unknown or hume isn't
        available, so this axis never penalises a panel for a missing brief.
      * `evidence` — `{family, primary, topEmotions, imageFeatures, algorithm,
        source}` for logging + downstream centroid distillation. The
        `imageFeatures` block is the student-model input shape (6 floats:
        luminance / r_weight / g_weight / b_weight / saturation / contrast)
        and `algorithm` is the hume_image_head variant that produced it
        (`visual_heuristic_v1` for the stdlib fallback, `visual_bootstrap_v1`
        for the trained centroid path).
    """
    family = resolve_mood_family(target_mood)
    if predict_image_emotion is None or not png_bytes:
        return 0.5, {"family": family, "source": "unavailable"}

    try:
        result = predict_image_emotion(png_bytes, mime_type, model=model)
    except Exception as e:  # pragma: no cover — defensive
        return 0.5, {"family": family, "source": "error", "error": str(e)[:120]}

    primary = result.get("primary") or {}
    top = result.get("topEmotions") or []
    # `predict_image_emotion` packs the 6-feature student input under
    # `evidence.imageFeatures` per the `normalizedExpression.v1` schema; pull
    # it out here so the persist step doesn't need to know that shape.
    image_features = ((result.get("evidence") or {}).get("imageFeatures") or {})
    teacher = result.get("teacher") or {}
    algorithm = teacher.get("algorithm") or "visual_heuristic_v1"

    common = {
        "primary": primary,
        "topEmotions": top[:5],
        "imageFeatures": image_features,
        "algorithm": algorithm,
    }

    if family is None:
        # Unknown target — fall back to neutral so the panel isn't penalised
        # for a brief that doesn't declare a tone.
        return 0.5, {"family": None, "source": "no_target", **common}

    hits = _FAMILY_EMOTIONS.get(family, frozenset())
    # Sum top-emotion scores that belong to the target family. hume_image_head
    # already normalises top scores to [0, 1] with the strongest at 1.0.
    matched = 0.0
    for item in top[:8]:
        name = str(item.get("name", "")).lower()
        if name in hits:
            matched += float(item.get("score") or 0.0)

    # Soft cap: a single perfect hit in family = 1.0. Multiple hits don't blow up.
    score = max(0.0, min(1.0, matched))
    return score, {
        "family": family,
        "source": "hume_image_head",
        **common,
    }
