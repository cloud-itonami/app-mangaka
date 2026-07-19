"""P10.1b — `kotodama.langgraph_node_resolvers.make_llm_vision_node`
+ `kotodama.llm.call_tier_vision_json` smoke tests.

Mocks the OpenAI vision endpoint with `monkeypatch` so the suite stays
network-free. The mangaka topology binds this node with a `blob_fetcher`
sourced from `lg_mangaka.blob.get` at server bootstrap — here we supply
a stub fetcher to exercise the path/encode/dispatch chain end-to-end.
"""

from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path

_LG_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_LG_DIR))

import pytest


def _run(coro):
    return asyncio.run(coro)


# ── kotodama.llm.call_tier_vision_json ──────────────────────────────────


def test_vision_json_rejects_unknown_tier():
    from kotodama import llm as _llm

    out = _llm.call_tier_vision_json(
        "tier-that-does-not-exist",
        "system", "user", ["aGVsbG8="],
    )
    assert out["ok"] is False
    assert "unknown vision tier" in out["error"]


def test_vision_json_rejects_when_api_key_missing(monkeypatch):
    from kotodama import llm as _llm

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out = _llm.call_tier_vision_json(
        "vision", "system", "user", ["aGVsbG8="],
    )
    assert out["ok"] is False
    assert "OPENAI_API_KEY" in out["error"]


def test_vision_json_rejects_empty_image_list(monkeypatch):
    from kotodama import llm as _llm

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    out = _llm.call_tier_vision_json("vision", "system", "user", [])
    assert out["ok"] is False
    assert "images_b64" in out["error"]


def test_vision_json_filters_blank_image_entries(monkeypatch):
    """Empty / whitespace strings in `images_b64` are dropped. When nothing
    survives, the dispatcher returns a structured error envelope."""
    from kotodama import llm as _llm

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    out = _llm.call_tier_vision_json("vision", "system", "user", ["", "   "])
    assert out["ok"] is False
    assert "no valid image payloads" in out["error"]


# ── make_llm_vision_node — happy path ─────────────────────────────────────


def test_vision_node_dispatches_with_blob_fetcher(monkeypatch):
    """Exercises the full path: image_keys path resolve → blob_fetcher →
    base64 encode → call_tier_vision_json."""
    from kotodama import langgraph_node_resolvers as resolvers
    from kotodama import llm as _llm

    fetched: list[str] = []

    async def fake_fetcher(key: str):
        fetched.append(key)
        return f"PNG-{key}".encode()

    seen = {}

    def fake_vision_call(tier, system, user, images_b64, **kwargs):
        seen["tier"] = tier
        seen["system"] = system
        seen["user"] = user
        seen["images_b64"] = list(images_b64)
        seen["kwargs"] = kwargs
        return {"ok": True, "data": {"composition": 0.9}, "model": "stub", "latencyMs": 1}

    monkeypatch.setattr(_llm, "call_tier_vision_json", fake_vision_call)

    node = resolvers.make_llm_vision_node(
        "vision",
        {
            "input_keys": ["brief"],
            "image_keys": ["renders.*.blobKey"],
            "result_key": "critique_raw",
            "args": {
                "system": "You critique.",
                "user_template": "Brief: {brief}",
                "max_tokens": 384,
                "temperature": 0.2,
            },
        },
        blob_fetcher=fake_fetcher,
    )

    state = {
        "brief": "rooftop chase, dawn",
        "renders": [
            {"blobKey": "blobs/anonymous/aaa", "angle": "FullShot"},
            {"blobKey": "blobs/anonymous/bbb", "angle": "Closeup"},
        ],
    }
    out = _run(node(state))
    assert "critique_raw" in out
    assert out["critique_raw"]["ok"] is True
    assert out["critique_raw"]["data"]["composition"] == 0.9

    # Fetcher was called once per blobKey.
    assert fetched == ["blobs/anonymous/aaa", "blobs/anonymous/bbb"]
    # call_tier_vision_json saw both images base64-encoded.
    assert len(seen["images_b64"]) == 2
    assert base64.b64decode(seen["images_b64"][0]) == b"PNG-blobs/anonymous/aaa"
    # user_template was formatted.
    assert "rooftop chase" in seen["user"]


def test_vision_node_skips_missing_blob(monkeypatch):
    """Fetcher returning None means the blob isn't available; the node
    must drop it and continue with the remaining images."""
    from kotodama import langgraph_node_resolvers as resolvers
    from kotodama import llm as _llm

    async def fake_fetcher(key: str):
        return None if key.endswith("bbb") else b"\x89PNG"

    captured = {}

    def fake_vision_call(*a, **k):
        captured["a"] = a
        captured["k"] = k
        return {"ok": True, "data": {}, "model": "stub"}

    monkeypatch.setattr(_llm, "call_tier_vision_json", fake_vision_call)

    node = resolvers.make_llm_vision_node(
        "vision",
        {
            "image_keys": ["renders.*.blobKey"],
            "result_key": "out",
            "args": {"system": "s", "user_template": "u"},
        },
        blob_fetcher=fake_fetcher,
    )
    state = {"renders": [{"blobKey": "blobs/aaa"}, {"blobKey": "blobs/bbb"}, {"blobKey": "blobs/ccc"}]}
    _run(node(state))
    # 2 of 3 blobs successfully encoded.
    assert len(captured["a"][3]) == 2


def test_vision_node_requires_result_key():
    from kotodama import langgraph_node_resolvers as resolvers

    with pytest.raises(ValueError, match="result_key"):
        resolvers.make_llm_vision_node("vision", {"image_keys": ["x"]})


def test_vision_node_requires_fetcher_when_image_keys_set():
    from kotodama import langgraph_node_resolvers as resolvers

    with pytest.raises(ValueError, match="blob_fetcher"):
        resolvers.make_llm_vision_node(
            "vision",
            {
                "image_keys": ["renders.*.blobKey"],
                "result_key": "out",
                "args": {"system": "s", "user_template": "u"},
            },
        )


def test_resolve_node_dispatches_llm_vision_kind(monkeypatch):
    """The dispatcher recognises kind='llm_vision' and routes to the
    new resolver."""
    from kotodama import langgraph_node_resolvers as resolvers

    async def fetcher(_):
        return b""

    node = resolvers.resolve_node(
        "llm_vision",
        "vision",
        {
            "image_keys": ["renders.*.blobKey"],
            "result_key": "x",
            "args": {"system": "s", "user_template": "u"},
        },
        blob_fetcher=fetcher,
    )
    assert callable(node)
