"""P11 unit tests — `kami_mangaka_scene` wheel availability probe.

The probe lives in `lg_mangaka.tools` (`is_kami_wheel_available` +
`kami_wheel_status`) so both the `/health` endpoint and the
`tool_render_keyframes` fallback path agree on a single import-state
view. Tests run with the wheel absent (default in dev venvs) — the
real wheel install is exercised in the integration Dockerfile build.
"""

from __future__ import annotations

import sys
from pathlib import Path

_LG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_LG_DIR))

import pytest

from lg_mangaka import tools as _tools


def test_kami_wheel_status_returns_dict_shape():
    out = _tools.kami_wheel_status()
    assert isinstance(out, dict)
    assert set(out.keys()) >= {"available", "module", "error"}
    assert out["module"] == "kami_mangaka_scene"


def test_kami_wheel_status_available_field_is_bool():
    out = _tools.kami_wheel_status()
    assert isinstance(out["available"], bool)


def test_kami_wheel_status_error_only_when_unavailable():
    out = _tools.kami_wheel_status()
    if out["available"]:
        assert out["error"] is None
    else:
        # Import failure must surface a non-empty diagnostic string.
        assert isinstance(out["error"], str) and out["error"].strip()


def test_is_kami_wheel_available_matches_status_field():
    """Both entry points must agree on the runtime state."""
    avail_direct = _tools.is_kami_wheel_available()
    avail_status = _tools.kami_wheel_status()["available"]
    assert avail_direct == avail_status


def test_is_kami_wheel_available_caches_error_message():
    """When the wheel isn't installed (dev venv), the helper records the
    underlying ImportError so the `/health` endpoint can show ops which
    pod image is shipping a stale build."""
    if _tools.is_kami_wheel_available():
        pytest.skip("wheel is installed in this env")
    # Re-probe to ensure the error message persists across calls.
    _tools.is_kami_wheel_available()
    assert _tools._KAMI_WHEEL_ERR is not None
    # The message format is "<ExcType>: <message>".
    assert ":" in _tools._KAMI_WHEEL_ERR


def test_status_dict_is_json_serialisable():
    """The wheel status is embedded in /health — must round-trip through
    FastAPI's JSON encoder without raising."""
    import json

    payload = _tools.kami_wheel_status()
    encoded = json.dumps(payload)
    assert isinstance(encoded, str)
    decoded = json.loads(encoded)
    assert decoded["module"] == "kami_mangaka_scene"
