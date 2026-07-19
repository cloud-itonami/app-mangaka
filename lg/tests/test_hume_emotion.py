"""Unit tests for lg_mangaka.hume_emotion — mood family resolution + image
emotion alignment via the kotodama hume_image_head primitive.

Pure-CPU, no network. We construct tiny valid PNGs in-memory so the
primitive exercises its full pixel-decode path (not the byte-histogram
fallback).
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

import pytest

_LG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_LG_DIR))

from lg_mangaka import hume_emotion as he


def _png_solid(w: int, h: int, r: int, g: int, b: int) -> bytes:
    """Build a minimal valid 8-bit RGB PNG with a single solid colour."""
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(t: bytes, d: bytes) -> bytes:
        c = zlib.crc32(t + d) & 0xFFFFFFFF
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", c)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes([r, g, b]) * w for _ in range(h))
    idat = zlib.compress(raw)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


# ── resolve_mood_family ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "mood,expected",
    [
        ("triumph", "joy"),
        ("Triumphant return", "joy"),
        ("celebration", "joy"),
        ("calm reflection", "calm"),
        ("quiet contemplation", "calm"),
        ("melancholy farewell", "sad"),
        ("sorrow", "sad"),
        ("ominous tension", "fear"),
        ("anxious dread", "fear"),
        ("furious rage", "anger"),
        ("dynamic action", "excitement"),
        ("emotional", "excitement"),
    ],
)
def test_resolve_mood_family_known_synonyms(mood: str, expected: str) -> None:
    assert he.resolve_mood_family(mood) == expected


@pytest.mark.parametrize("mood", [None, "", "xyzzy", "frobnicate"])
def test_resolve_mood_family_unknown_returns_none(mood) -> None:
    assert he.resolve_mood_family(mood) is None


def test_resolve_mood_family_longest_match_wins() -> None:
    # "triumphant" contains "triumph" — both map to joy, so the result is
    # stable regardless of tiebreak. But "ominous melancholy" mixes fear +
    # sad families; the longer "melanchol" (9) outweighs "ominous" (7) and
    # we resolve to sad.
    assert he.resolve_mood_family("ominous melancholy aftermath") == "sad"


# ── score_emotion_alignment ───────────────────────────────────────────────


def test_score_alignment_neutral_when_no_target() -> None:
    png = _png_solid(4, 4, 200, 50, 50)
    score, evidence = he.score_emotion_alignment(png, None)
    assert score == 0.5
    assert evidence["source"] == "no_target"


def test_score_alignment_neutral_when_png_empty() -> None:
    score, evidence = he.score_emotion_alignment(b"", "triumph")
    assert score == 0.5
    # Empty bytes short-circuit before reaching the primitive.
    assert evidence["source"] == "unavailable"


def test_score_alignment_neutral_when_mood_unknown() -> None:
    png = _png_solid(4, 4, 200, 50, 50)
    score, evidence = he.score_emotion_alignment(png, "frobnicate")
    assert score == 0.5
    assert evidence["family"] is None


def test_score_alignment_warm_palette_aligns_with_joy() -> None:
    """A bright saturated red panel should score >0 against the joy family —
    `excitement` / `joy` / `anger` all weight red+saturation positively, and
    excitement + joy are in the joy family hits set."""
    png = _png_solid(8, 8, 240, 60, 60)
    score, evidence = he.score_emotion_alignment(png, "triumph")
    assert evidence["family"] == "joy"
    assert evidence["source"] == "hume_image_head"
    assert score > 0.0
    names = {item["name"] for item in evidence["topEmotions"]}
    # At least one joy-family emotion should appear in the top emotions.
    assert names & {"joy", "excitement", "gratitude", "relief"}


def test_score_alignment_cool_palette_aligns_with_fear_or_calm() -> None:
    """A dark cool-blue panel should yield non-trivial mass in fear/calm
    families (anxiety / doubt / calm dominate the heuristic for low
    luminance + high blue weight)."""
    png = _png_solid(8, 8, 25, 35, 200)
    score_fear, ev_fear = he.score_emotion_alignment(png, "ominous")
    assert ev_fear["family"] == "fear"
    assert score_fear > 0.0
    score_calm, ev_calm = he.score_emotion_alignment(png, "calm reflection")
    assert ev_calm["family"] == "calm"
    assert score_calm > 0.0


def test_score_alignment_clamps_to_unit() -> None:
    png = _png_solid(8, 8, 240, 60, 60)
    score, _ = he.score_emotion_alignment(png, "joyful")
    assert 0.0 <= score <= 1.0


def test_score_alignment_resilient_to_primitive_failure(monkeypatch) -> None:
    """Exceptions in `predict_image_emotion` should not poison the critique."""

    def boom(*_a, **_k):
        raise RuntimeError("simulated upstream failure")

    monkeypatch.setattr(he, "predict_image_emotion", boom)
    score, evidence = he.score_emotion_alignment(
        _png_solid(4, 4, 240, 60, 60), "triumph"
    )
    assert score == 0.5
    assert evidence["source"] == "error"
