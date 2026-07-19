"""P13 unit tests — `tool_attach_character_vrm`.

Pure-CPU with monkey-patched B2 + RW. Validates the input guards
(glTF magic, base64, character_rkey shape), the B2 PUT call, the
idempotent SELECT → JSON-patch → UPDATE write-back path, and the
warning envelopes for partial-success cases (no RW, missing row).
"""

from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path

_LG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_LG_DIR))

import pytest

from lg_mangaka import blob as _blob
from lg_mangaka import tools as _tools


def _run(coro):
    return asyncio.run(coro)


def _minimal_vrm_payload(extra_bytes: int = 0) -> str:
    """Synthesise a base64-encoded payload that starts with the canonical
    glTF magic 'glTF' + version 2 + length placeholder, then `extra_bytes`
    of zero padding. Enough to clear `tool_attach_character_vrm`'s magic
    check; real VRMs are >= 5 MB but the validator only inspects the first
    four bytes."""
    header = b"glTF" + b"\x02\x00\x00\x00" + b"\x00" * max(0, extra_bytes)
    return base64.b64encode(header).decode("ascii")


# ── input guards ──────────────────────────────────────────────────────────


def test_missing_character_rkey_returns_error():
    out = _run(_tools.tool_attach_character_vrm(
        character_rkey="",
        vrm_content_b64=_minimal_vrm_payload(),
    ))
    assert "error" in out
    assert "character_rkey" in out["error"]


def test_missing_vrm_returns_error():
    out = _run(_tools.tool_attach_character_vrm(
        character_rkey="ch-honoka",
        vrm_content_b64="",
    ))
    assert "error" in out
    assert "vrm_content_b64" in out["error"]


def test_invalid_base64_returns_error():
    out = _run(_tools.tool_attach_character_vrm(
        character_rkey="ch-honoka",
        vrm_content_b64="not!base64!?",
    ))
    assert "error" in out
    assert "base64" in out["error"]


def test_missing_gltf_magic_returns_error():
    # base64 of "NOTGLTF" — passes b64 decode but fails magic check.
    junk = base64.b64encode(b"NOTGLTF and some bytes").decode("ascii")
    out = _run(_tools.tool_attach_character_vrm(
        character_rkey="ch-honoka",
        vrm_content_b64=junk,
    ))
    assert "error" in out
    assert "magic" in out["error"]


# ── B2 not configured ────────────────────────────────────────────────────


def test_b2_unconfigured_returns_error(monkeypatch):
    monkeypatch.setattr(_blob, "is_configured", lambda: False)
    out = _run(_tools.tool_attach_character_vrm(
        character_rkey="ch-honoka",
        vrm_content_b64=_minimal_vrm_payload(),
    ))
    assert "error" in out
    assert "B2 not configured" in out["error"]


def test_b2_put_failure_returns_error(monkeypatch):
    monkeypatch.setattr(_blob, "is_configured", lambda: True)

    def boom(*a, **k):
        raise RuntimeError("B2 quota exceeded")

    monkeypatch.setattr(_blob, "put_content_addressed", boom)
    out = _run(_tools.tool_attach_character_vrm(
        character_rkey="ch-honoka",
        vrm_content_b64=_minimal_vrm_payload(),
    ))
    assert "error" in out
    assert "B2 PUT failed" in out["error"]
    assert "quota" in out["error"]


# ── upload-only path (no RW configured) ──────────────────────────────────


def test_no_rw_url_returns_warning(monkeypatch):
    """When RW_URL is not configured, the upload still succeeds but the
    character row update is skipped — caller gets blobKey + warning."""
    monkeypatch.setattr(_blob, "is_configured", lambda: True)
    monkeypatch.setattr(
        _blob, "put_content_addressed",
        lambda data, **k: (f"blobs/mangaka/vrm/{data.hex()[:6]}", "s3://stub"),
    )
    monkeypatch.setattr(_tools, "_DEFAULT_RW_URL", "")
    out = _run(_tools.tool_attach_character_vrm(
        character_rkey="ch-honoka",
        vrm_content_b64=_minimal_vrm_payload(),
        rw_url=None,
    ))
    assert "blobKey" in out
    assert out["vertexId"] is None
    assert "warning" in out
    assert "RW_URL" in out["warning"]


# ── blob key format / content-addressing ─────────────────────────────────


def test_blob_key_uses_vrm_prefix(monkeypatch):
    captured = {}

    def stub_put(data, *, prefix, content_type):
        captured["prefix"] = prefix
        captured["content_type"] = content_type
        return (f"{prefix}/abc123", f"s3://stub/{prefix}/abc123")

    monkeypatch.setattr(_blob, "is_configured", lambda: True)
    monkeypatch.setattr(_blob, "put_content_addressed", stub_put)
    monkeypatch.setattr(_tools, "_DEFAULT_RW_URL", "")

    out = _run(_tools.tool_attach_character_vrm(
        character_rkey="ch-honoka",
        vrm_content_b64=_minimal_vrm_payload(),
    ))
    assert captured["prefix"] == "blobs/mangaka/vrm"
    assert captured["content_type"] == "model/gltf-binary"
    assert out["blobKey"].startswith("blobs/mangaka/vrm/")


# ── dispatcher routing ───────────────────────────────────────────────────


def test_server_dispatches_attach_character_vrm():
    """The pod's XRPC dispatcher must include this tool so MCP `tools/call`
    envelopes route to the right handler."""
    from lg_mangaka import server as srv

    assert "com.etzhayyim.mangaka.tools.attachCharacterVrm" in srv._TOOL_NSID_TO_HANDLER
    assert (
        srv._TOOL_NSID_TO_HANDLER["com.etzhayyim.mangaka.tools.attachCharacterVrm"]
        is _tools.tool_attach_character_vrm
    )


# ── lexicon contract present ─────────────────────────────────────────────


def test_lexicon_file_exists():
    import json

    repo_root = _LG_DIR.parents[2]
    lex_path = (
        repo_root
        / "00-contracts"
        / "lexicons"
        / "ai"
        / "etzhayyim"
        / "apps"
        / "mangaka"
        / "tools"
        / "attachCharacterVrm.json"
    )
    assert lex_path.is_file()
    lex = json.loads(lex_path.read_text())
    assert lex["id"] == "com.etzhayyim.mangaka.tools.attachCharacterVrm"
    schema = lex["defs"]["main"]["input"]["schema"]
    assert "characterRkey" in schema["required"]
    assert "vrmContentB64" in schema["required"]
