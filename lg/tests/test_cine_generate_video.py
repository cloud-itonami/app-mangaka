"""Unit tests for cine_generate_video._resolve_ffmpeg().

Covers the binary-resolution logic only — no ComfyUI, no subprocess,
no ffmpeg binary required in the test environment.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_LG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_LG_DIR))

# Import the resolver without triggering the module-level _FFMPEG assignment.
with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
    from lg_mangaka.graphs.cine_generate_video import _resolve_ffmpeg


# ── PATH resolution (happy path) ─────────────────────────────────────────

def test_resolve_prefers_which_result():
    with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
         patch.dict(os.environ, {"FFMPEG_BIN": "/malicious/binary"}, clear=False):
        assert _resolve_ffmpeg() == "/usr/bin/ffmpeg"


def test_resolve_ffmpeg_bin_ignored_when_which_finds_binary():
    with patch("shutil.which", return_value="/opt/homebrew/bin/ffmpeg"), \
         patch.dict(os.environ, {"FFMPEG_BIN": "/tmp/evil"}, clear=False):
        assert _resolve_ffmpeg() == "/opt/homebrew/bin/ffmpeg"


# ── FFMPEG_BIN validation ─────────────────────────────────────────────────

def test_resolve_rejects_relative_ffmpeg_bin():
    with patch("shutil.which", return_value=None), \
         patch.dict(os.environ, {"FFMPEG_BIN": "bin/ffmpeg"}, clear=False):
        with pytest.raises(ValueError, match="absolute path"):
            _resolve_ffmpeg()


def test_resolve_rejects_nonexistent_ffmpeg_bin():
    with patch("shutil.which", return_value=None), \
         patch.dict(os.environ, {"FFMPEG_BIN": "/nonexistent/ffmpeg"}, clear=False):
        with pytest.raises(ValueError, match="does not exist"):
            _resolve_ffmpeg()


def test_resolve_rejects_non_executable_ffmpeg_bin(tmp_path):
    fake = tmp_path / "ffmpeg"
    fake.write_bytes(b"")
    fake.chmod(0o644)
    with patch("shutil.which", return_value=None), \
         patch.dict(os.environ, {"FFMPEG_BIN": str(fake)}, clear=False):
        with pytest.raises(ValueError, match="not executable"):
            _resolve_ffmpeg()


def test_resolve_accepts_valid_absolute_executable_ffmpeg_bin(tmp_path):
    fake = tmp_path / "ffmpeg"
    fake.write_bytes(b"")
    fake.chmod(0o755)
    with patch("shutil.which", return_value=None), \
         patch.dict(os.environ, {"FFMPEG_BIN": str(fake)}, clear=False):
        assert _resolve_ffmpeg() == str(fake)


# ── fallback sentinel ─────────────────────────────────────────────────────

def test_resolve_falls_back_to_bare_ffmpeg_when_no_env():
    env_without = {k: v for k, v in os.environ.items() if k != "FFMPEG_BIN"}
    with patch("shutil.which", return_value=None), \
         patch.dict(os.environ, env_without, clear=True):
        assert _resolve_ffmpeg() == "ffmpeg"


def test_resolve_ignores_empty_string_ffmpeg_bin():
    with patch("shutil.which", return_value=None), \
         patch.dict(os.environ, {"FFMPEG_BIN": "  "}, clear=False):
        assert _resolve_ffmpeg() == "ffmpeg"
