"""Hume emotion centroid distillation runner — consumes the corpus
persisted by `tool_persist_hume_emotion_observation` into
`vertex_vector_emotion_signal` and produces a student model JSON via
`kotodama.primitives.hume_image_head.train_image_centroid`.

Closes the loop from P4's persistence work (commit a2ec0f278bc — the
`humeObservation.v1` rows) to the trained student model the
`hume_image_head` primitive's `score_emotion_alignment` can consume via
its `model=` arg. Without this runner the corpus only accumulates;
nothing reads it back into a centroid.

Pipeline:

  vertex_vector_emotion_signal
      ↳ SELECT raw_json WHERE provider='Hume AI'
                            AND model_id='hume-image-head'
                            AND modality='image'
      ↳ parse_observation_row → {input.imageFeatures, labels.{primary,topEmotions}}
      ↳ train_image_centroid → {emotionCentroids, primaryPriors, globalTopEmotions}
      ↳ JSON artifact (stdout / file)

Per ADR-2605111200 this MUST run on a pod (or any RW-reachable host) —
the CF Worker cannot query RW.

Re-using this runner once the model is durable:
  1. Persist the returned dict somewhere stable (B2 blob today; a
     `vertex_vector_embedding_model`-shaped row later).
  2. Pass the loaded dict into
     `lg_mangaka.hume_emotion.score_emotion_alignment(..., model=<dict>)`
     so the next compose_scene_3d render uses the centroid instead of
     the stdlib `visual_heuristic_v1` fallback.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from kotodama.primitives.hume_image_head import train_image_centroid

_log = logging.getLogger(__name__)

_DEFAULT_RW_URL = os.environ.get("RW_URL", "")
_PROVIDER = "Hume AI"
_MODEL_ID = "hume-image-head"
_MODALITY = "image"


# ── parse / rebind ────────────────────────────────────────────────────────


def parse_observation_row(raw_json_str: str | None) -> dict[str, Any] | None:
    """Convert a persisted `humeObservation.v1` envelope into the shape
    `train_image_centroid` expects.

    The persisted envelope places the teacher's predicted emotion under
    top-level `primary` / `topEmotions` and reserves `labels` for the
    author-intended mood (`targetMood` / `targetFamily`). Centroid
    training needs the *teacher's* distribution under
    `labels.primary` + `labels.topEmotions`, so we rebind. The author
    intent is preserved on the rebound row under `labels.author` for
    downstream evaluation tooling that wants ground-truth-vs-teacher
    agreement metrics.

    Returns `None` for malformed JSON or rows lacking
    `input.imageFeatures` (train_image_centroid would skip them anyway —
    short-circuit here so the caller can count skipped rows).
    """
    if not raw_json_str:
        return None
    try:
        envelope = json.loads(raw_json_str)
    except json.JSONDecodeError:
        return None
    if not isinstance(envelope, dict):
        return None

    features = ((envelope.get("input") or {}).get("imageFeatures") or {})
    if not isinstance(features, dict) or not features:
        return None

    primary = envelope.get("primary") if isinstance(envelope.get("primary"), dict) else {}
    top = envelope.get("topEmotions") if isinstance(envelope.get("topEmotions"), list) else []
    author = envelope.get("labels") if isinstance(envelope.get("labels"), dict) else {}

    return {
        "input": {"imageFeatures": dict(features)},
        "labels": {
            "primary": dict(primary),
            "topEmotions": [dict(e) for e in top if isinstance(e, dict)],
            "author": dict(author),
        },
        "sourceId": envelope.get("blobKey") or envelope.get("panelRkey"),
        "humeScore": envelope.get("humeScore"),
        "selected": envelope.get("selected"),
    }


# ── DB fetch ──────────────────────────────────────────────────────────────


async def fetch_observations(
    *,
    rw_url: str | None = None,
    limit: int = 5000,
    since: str | None = None,
) -> list[dict[str, Any]]:
    """Pull raw_json rows from `vertex_vector_emotion_signal` filtered to
    the mangaka Hume image-head corpus, parse + rebind each one.

    `since` is an ISO8601 string compared against `analyzed_at`; defaults
    to the start of time so an initial run sweeps everything. `limit` is
    a defensive cap — the corpus is expected to grow into the millions
    once compose_scene_3d is in steady-state production, and a default
    full table scan would be impolite to RW.
    """
    url = rw_url or _DEFAULT_RW_URL
    if not url:
        raise RuntimeError("RW_URL not configured")

    from kotodama.kotoba_datomic import get_kotoba_client
    import asyncio

    client = get_kotoba_client()
    # Kotoba query with Datalog `q` or `select_where`. Since we need complex filter (>= since, ANDs),
    # we'll use `q` with datalog. Or simpler: fetch all match model_id and modality, then filter in python.
    # Wait, the easiest is to query `raw_json` where provider=_PROVIDER and model_id=_MODEL_ID and modality=_MODALITY
    
    def fetch_sync() -> list[dict[str, Any]]:
        rows = client.select_where(
            "vertex_vector_emotion_signal",
            "model_id",
            _MODEL_ID,
            ["raw_json", "provider", "modality", "analyzed_at"],
            limit=limit
        )
        return rows

    rows = await asyncio.to_thread(fetch_sync)

    observations: list[dict[str, Any]] = []
    for r in rows or []:
        if r.get("provider") != _PROVIDER or r.get("modality") != _MODALITY:
            continue
        if since and (r.get("analyzed_at") or "") < since:
            continue
        parsed = parse_observation_row(r.get("raw_json"))
        if parsed is not None:
            observations.append(parsed)
    return observations


# ── train + summarise ────────────────────────────────────────────────────


class DistillationError(RuntimeError):
    """Raised when the corpus is too small / sparse to train usefully."""


def _per_family_coverage(observations: list[dict[str, Any]]) -> dict[str, int]:
    """Count observations per author-intended family so a maintainer can
    spot a corpus that is heavily skewed (e.g. 99% `joy`, 0 `fear`) —
    the resulting centroid would memorise one family and ignore the
    rest. Surfaces in `metrics.familyCoverage`."""
    counts: dict[str, int] = {}
    for row in observations:
        family = (
            ((row.get("labels") or {}).get("author") or {}).get("targetFamily")
            or "unknown"
        )
        counts[family] = counts.get(family, 0) + 1
    return counts


def _per_primary_coverage(observations: list[dict[str, Any]]) -> dict[str, int]:
    """Count observations per teacher-predicted primary emotion. Tells
    you whether the *teacher* sees enough variety in the corpus, which
    bounds how well the centroid can generalise."""
    counts: dict[str, int] = {}
    for row in observations:
        primary = ((row.get("labels") or {}).get("primary") or {}).get("name") or "unknown"
        counts[primary] = counts.get(primary, 0) + 1
    return counts


def run_distillation(
    observations: list[dict[str, Any]],
    *,
    min_rows: int = 10,
) -> dict[str, Any]:
    """Train a student centroid model from parsed observations.

    Returns `{"model": <hume_image_head student model dict>, "metrics":
    {"rows", "familyCoverage", "primaryCoverage", "createdAt"}}`.

    Raises `DistillationError` when the corpus is below `min_rows` — a
    centroid trained on a tiny corpus would be brittle, and silently
    producing such a model would be worse than no model at all
    (callers would route it through `score_emotion_alignment` and get
    miscalibrated scores).
    """
    n = len(observations)
    if n < int(min_rows):
        raise DistillationError(
            f"not enough observations to distil: have {n}, need >= {min_rows}"
        )
    model = train_image_centroid(observations)
    return {
        "model": model,
        "metrics": {
            "rows": n,
            "familyCoverage": _per_family_coverage(observations),
            "primaryCoverage": _per_primary_coverage(observations),
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    }
