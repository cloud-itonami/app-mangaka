"""Unit tests for `lg_mangaka.hume_distill`.

Covers the persist → train end-to-end loop with synthetic observation
rows so no RW / B2 / OpenAI access is required. The trained centroid is
piped back into `kotodama.primitives.hume_image_head.predict_image_emotion`
so an end-to-end "trained model is loadable" assertion lands.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_LG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_LG_DIR))

from lg_mangaka.hume_distill import (
    DistillationError,
    _per_family_coverage,
    _per_primary_coverage,
    parse_observation_row,
    run_distillation,
)


# ── fixture helpers ───────────────────────────────────────────────────────


def _envelope(
    *,
    blob_key: str = "blobs/anonymous/aaa",
    panel_rkey: str = "panel-001",
    target_mood: str = "triumph",
    target_family: str = "joy",
    primary_name: str = "joy",
    primary_score: float = 0.82,
    image_features: dict[str, float] | None = None,
    top_emotions: list[dict[str, float]] | None = None,
    hume_score: float = 0.7,
    selected: bool = False,
) -> dict:
    return {
        "schema": "com.etzhayyim.mangaka.humeObservation.v1",
        "input": {
            "imageFeatures": image_features or {
                "luminance": 0.6, "r_weight": 0.55, "g_weight": 0.30,
                "b_weight": 0.15, "saturation": 0.7, "contrast": 0.62,
            },
        },
        "labels": {"targetMood": target_mood, "targetFamily": target_family},
        "primary": {"name": primary_name, "score": primary_score},
        "topEmotions": top_emotions or [
            {"name": primary_name, "score": primary_score},
            {"name": "excitement", "score": 0.41},
        ],
        "humeScore": hume_score,
        "algorithm": "visual_heuristic_v1",
        "source": "compose_scene_3d",
        "selected": selected,
        "panelRkey": panel_rkey,
        "blobKey": blob_key,
    }


# ── parse_observation_row ─────────────────────────────────────────────────


def test_parse_observation_rebinds_teacher_signal_under_labels():
    raw = json.dumps(_envelope())
    out = parse_observation_row(raw)
    assert out is not None
    # Teacher distribution (primary + topEmotions) must move under labels —
    # train_image_centroid reads it from labels.primary / labels.topEmotions.
    assert out["labels"]["primary"]["name"] == "joy"
    assert out["labels"]["topEmotions"][0]["name"] == "joy"
    # Author intent retained on labels.author for downstream metrics.
    assert out["labels"]["author"]["targetMood"] == "triumph"
    assert out["labels"]["author"]["targetFamily"] == "joy"
    # imageFeatures preserved verbatim under input.imageFeatures.
    assert out["input"]["imageFeatures"]["b_weight"] == 0.15


def test_parse_observation_preserves_sourceid_and_hume_score():
    """`sourceId` (used by train_image_centroid for training.sourceIds)
    must fall back from blobKey → panelRkey when the blob key is missing."""
    raw = json.dumps(_envelope(blob_key="", panel_rkey="panel-fallback"))
    out = parse_observation_row(raw)
    assert out is not None
    assert out["sourceId"] == "panel-fallback"
    assert out["humeScore"] == 0.7


@pytest.mark.parametrize("bad", [None, "", "{not json", "[]", '"not a dict"'])
def test_parse_observation_returns_none_for_malformed(bad):
    assert parse_observation_row(bad) is None


def test_parse_observation_returns_none_when_image_features_missing():
    raw = json.dumps({**_envelope(), "input": {}})
    assert parse_observation_row(raw) is None
    raw = json.dumps({**_envelope(), "input": {"imageFeatures": {}}})
    assert parse_observation_row(raw) is None


# ── coverage helpers ──────────────────────────────────────────────────────


def test_per_family_and_primary_coverage_count_buckets():
    observations = [
        parse_observation_row(json.dumps(_envelope(target_family="joy", primary_name="joy"))),
        parse_observation_row(json.dumps(_envelope(target_family="joy", primary_name="excitement"))),
        parse_observation_row(json.dumps(_envelope(target_family="fear", primary_name="anxiety"))),
    ]
    assert _per_family_coverage(observations) == {"joy": 2, "fear": 1}
    assert _per_primary_coverage(observations) == {"joy": 1, "excitement": 1, "anxiety": 1}


def test_per_family_coverage_falls_back_to_unknown_when_label_missing():
    raw = json.dumps({**_envelope(), "labels": {}})
    parsed = parse_observation_row(raw)
    assert _per_family_coverage([parsed]) == {"unknown": 1}


# ── run_distillation ──────────────────────────────────────────────────────


def _synthetic_corpus(n: int) -> list[dict]:
    """Build a small, diverse corpus so train_image_centroid produces a
    non-degenerate model. Three families with distinct palettes."""
    out: list[dict] = []
    for i in range(n):
        bucket = i % 3
        if bucket == 0:
            row = _envelope(
                blob_key=f"blobs/anonymous/joy-{i}",
                target_family="joy", primary_name="joy",
                image_features={
                    "luminance": 0.7, "r_weight": 0.5, "g_weight": 0.3,
                    "b_weight": 0.2, "saturation": 0.7, "contrast": 0.5,
                },
                top_emotions=[
                    {"name": "joy", "score": 0.9}, {"name": "excitement", "score": 0.4},
                ],
            )
        elif bucket == 1:
            row = _envelope(
                blob_key=f"blobs/anonymous/fear-{i}",
                target_family="fear", primary_name="anxiety",
                image_features={
                    "luminance": 0.2, "r_weight": 0.2, "g_weight": 0.2,
                    "b_weight": 0.6, "saturation": 0.3, "contrast": 0.8,
                },
                top_emotions=[
                    {"name": "anxiety", "score": 0.85}, {"name": "doubt", "score": 0.45},
                ],
            )
        else:
            row = _envelope(
                blob_key=f"blobs/anonymous/calm-{i}",
                target_family="calm", primary_name="calm",
                image_features={
                    "luminance": 0.5, "r_weight": 0.3, "g_weight": 0.4,
                    "b_weight": 0.3, "saturation": 0.25, "contrast": 0.3,
                },
                top_emotions=[
                    {"name": "calm", "score": 0.78}, {"name": "relief", "score": 0.32},
                ],
            )
        out.append(parse_observation_row(json.dumps(row)))
    return [o for o in out if o is not None]


def test_run_distillation_produces_visual_centroid_v1_model():
    observations = _synthetic_corpus(12)
    result = run_distillation(observations, min_rows=10)
    model = result["model"]
    assert model["algorithm"] == "visual_centroid_v1"
    assert model["outputSchema"] == "com.etzhayyim.apps.hume.normalizedExpression.v1"
    # Centroids must cover the teacher labels in the corpus.
    centroid_names = set(model["emotionCentroids"])
    assert {"joy", "anxiety", "calm"} <= centroid_names
    # Each centroid is a 6-feature dict over hume_image_head.FEATURE_KEYS.
    from kotodama.primitives.hume_image_head import FEATURE_KEYS
    for name, centroid in model["emotionCentroids"].items():
        assert set(centroid) == set(FEATURE_KEYS), f"{name} centroid missing keys"
    # Priors normalise to 1 across primary names seen.
    assert pytest.approx(sum(model["primaryPriors"].values()), abs=1e-6) == 1.0
    # Metrics reflect coverage.
    assert result["metrics"]["rows"] == 12
    assert sum(result["metrics"]["familyCoverage"].values()) == 12
    assert sum(result["metrics"]["primaryCoverage"].values()) == 12


def test_run_distillation_refuses_below_min_rows():
    observations = _synthetic_corpus(5)
    with pytest.raises(DistillationError, match="not enough"):
        run_distillation(observations, min_rows=10)


def test_run_distillation_includes_source_ids_in_training_block():
    """`sourceId` per observation must show up in the trained model's
    `training.sourceIds` list so lineage queries can trace any centroid
    back to its source corpus."""
    observations = _synthetic_corpus(15)
    expected_ids = sorted(o["sourceId"] for o in observations)
    result = run_distillation(observations, min_rows=10)
    actual_ids = sorted(filter(None, result["model"]["training"]["sourceIds"]))
    assert actual_ids == expected_ids


# ── end-to-end: trained model loads back into predict_image_emotion ───────


def test_trained_model_is_loadable_by_predict_image_emotion():
    """The whole point of the loop: the model JSON produced by
    run_distillation feeds back into hume_image_head.predict_image_emotion
    via the `model=` arg, so future panel scoring uses the distilled
    centroid instead of the stdlib heuristic fallback."""
    from kotodama.primitives.hume_image_head import predict_image_emotion

    observations = _synthetic_corpus(15)
    model = run_distillation(observations, min_rows=10)["model"]

    # Build a tiny synthetic image to feed the predictor. predict_image_emotion
    # accepts arbitrary bytes via its byte-histogram fallback when the PNG
    # decoder fails, so we don't need a real PNG to exercise the model path.
    fake_bytes = b"\x89PNG-synthetic-" + b"\x00" * 256
    out = predict_image_emotion(fake_bytes, "image/png", model=model)
    assert out["schema"] == "com.etzhayyim.apps.hume.normalizedExpression.v1"
    # The teacher provider lineage must be preserved.
    assert out["teacher"]["distilledFrom"] == "hume-expression-measurement"
    # `algorithm` reflects the trained centroid, not the heuristic fallback.
    assert out["teacher"]["algorithm"] == "visual_centroid_v1"
    # And we should get a primary emotion + non-empty top list.
    assert out["primary"] is not None
    assert out["primary"]["name"] in model["emotionCentroids"]
    assert out["topEmotions"]


# ── score_emotion_alignment integration ───────────────────────────────────


def test_score_emotion_alignment_routes_through_trained_model():
    """`lg_mangaka.hume_emotion.score_emotion_alignment` accepts a `model`
    kwarg that flows down into `predict_image_emotion`. The compose_scene_3d
    Pregel can plug the distilled model in via that arg without code change."""
    from lg_mangaka.hume_emotion import score_emotion_alignment

    observations = _synthetic_corpus(15)
    model = run_distillation(observations, min_rows=10)["model"]

    fake_bytes = b"\x89PNG-synthetic-" + b"\x00" * 256
    score, evidence = score_emotion_alignment(fake_bytes, "triumph", model=model)
    assert 0.0 <= score <= 1.0
    assert evidence["algorithm"] == "visual_centroid_v1"
    assert evidence["source"] == "hume_image_head"
