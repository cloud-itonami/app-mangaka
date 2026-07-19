"""CLI runner for the Hume emotion centroid distillation loop.

Pulls the persisted `vertex_vector_emotion_signal` Hume image-head
corpus (see `tool_persist_hume_emotion_observation` in
`lg_mangaka/tools.py`), trains a student centroid model via
`kotodama.primitives.hume_image_head.train_image_centroid`, and
writes the resulting model JSON.

Usage
-----

    # one-shot run, writes model to stdout
    python -m scripts.distill_hume_emotion --rw-url $RW_URL

    # write to file with a non-default minimum corpus size + recent slice
    python -m scripts.distill_hume_emotion \
        --rw-url $RW_URL --limit 50000 --min-rows 200 --since 2026-06-01T00:00:00Z \
        --out /tmp/hume-student-mangaka.json

Per ADR-2605111200 this MUST run on a pod (or any RW-reachable host).
The CF Worker cannot query RW.

Re-use the produced model JSON by passing it into
`lg_mangaka.hume_emotion.score_emotion_alignment(..., model=<dict>)` —
the next compose_scene_3d render will then score `emotionAlignment`
against the distilled centroid instead of the stdlib heuristic
fallback.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# Allow `python scripts/distill_hume_emotion.py` from the lg/ root.
_LG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_LG_DIR))

from lg_mangaka.hume_distill import (  # noqa: E402
    DistillationError,
    fetch_observations,
    run_distillation,
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="distill_hume_emotion",
        description=(
            "Train a Hume image-head student centroid from the corpus persisted in "
            "vertex_vector_emotion_signal."
        ),
    )
    p.add_argument(
        "--rw-url",
        default=None,
        help="RisingWave connection string. Defaults to the RW_URL env var.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="Maximum number of observations to pull. Default: 5000.",
    )
    p.add_argument(
        "--since",
        default=None,
        help=(
            "ISO8601 timestamp; only observations with analyzed_at >= since "
            "are included. Default: no lower bound."
        ),
    )
    p.add_argument(
        "--min-rows",
        type=int,
        default=10,
        help=(
            "Refuse to train when the corpus has fewer rows than this — "
            "tiny corpora produce brittle centroids. Default: 10."
        ),
    )
    p.add_argument(
        "--out",
        default="-",
        help="Output path for the model JSON. '-' (default) means stdout.",
    )
    p.add_argument(
        "--include-metrics",
        action="store_true",
        help=(
            "Wrap the model JSON in `{model, metrics}` instead of writing the bare "
            "model. Useful for audit / lineage capture."
        ),
    )
    return p


async def _amain(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    log = logging.getLogger("distill_hume_emotion")

    log.info("fetching observations (limit=%d, since=%s) …", args.limit, args.since)
    try:
        observations = await fetch_observations(
            rw_url=args.rw_url, limit=args.limit, since=args.since,
        )
    except RuntimeError as exc:
        log.error("fetch failed: %s", exc)
        return 2
    log.info("fetched %d observations", len(observations))

    try:
        result = run_distillation(observations, min_rows=args.min_rows)
    except DistillationError as exc:
        log.error("distillation refused: %s", exc)
        return 3

    log.info(
        "trained centroid: rows=%d families=%s primaries=%s",
        result["metrics"]["rows"],
        result["metrics"]["familyCoverage"],
        result["metrics"]["primaryCoverage"],
    )

    payload = result if args.include_metrics else result["model"]
    blob = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    if args.out == "-":
        sys.stdout.write(blob + "\n")
    else:
        Path(args.out).write_text(blob, encoding="utf-8")
        log.info("wrote %d bytes to %s", len(blob), args.out)
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(argv))


if __name__ == "__main__":
    sys.exit(main())
