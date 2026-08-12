"""The inference edge must default to the murakumo fleet alias, not a pod id.

Guards ADR-2607173100 at the two modules that hold an inference URL:
`lg_mangaka.llm` and `lg_mangaka.graphs.agent_chat`.

Three properties, one per clause of the ADR's resolution order:

  * an env override still wins (clause 1) — the positive control, which is
    expected to pass both before and after the change this file lands with;
  * the baked fallback names the fleet endpoint (clause 3);
  * the baked fallback names NO concrete model id (clause 3) — it carries the
    `murakumo-main` alias, which the fleet resolves server-side, so switching
    the fleet's main model does not require a release here.

The modules read their env at import time, so overrides are exercised by
reloading the module under a patched environ rather than by monkeypatching
attributes — that is what a real process start does.
"""

from __future__ import annotations

import importlib

import pytest

# Every module in this repo that holds an inference endpoint default.
_LLM_MODULES = ["lg_mangaka.llm", "lg_mangaka.graphs.agent_chat"]


def _reload(module_name: str):
    return importlib.reload(importlib.import_module(module_name))


@pytest.fixture(autouse=True)
def _restore_modules():
    """Leave the imported modules holding their real defaults afterwards."""
    yield
    for name in _LLM_MODULES:
        _reload(name)


@pytest.mark.parametrize("module_name", _LLM_MODULES)
def test_default_endpoint_is_the_murakumo_fleet(module_name, monkeypatch):
    monkeypatch.delenv("VLLM_URL", raising=False)
    mod = _reload(module_name)
    assert mod._VLLM_URL == "https://api.murakumo.cloud/v1"


@pytest.mark.parametrize("module_name", _LLM_MODULES)
def test_default_endpoint_is_not_an_ephemeral_pod_host(module_name, monkeypatch):
    """RunPod releases `*.proxy.runpod.net` names and can reassign them.

    A default pointing at one names a host another tenant may come to own.
    No credential is sent to this endpoint (the call sites send only
    Content-Type), so the exposure is of request payloads — scripts, chat
    history, work ids — but a default must still not name a host we do not
    control.
    """
    monkeypatch.delenv("VLLM_URL", raising=False)
    mod = _reload(module_name)
    assert "runpod.net" not in mod._VLLM_URL


@pytest.mark.parametrize("module_name", _LLM_MODULES)
def test_default_model_is_the_alias_not_a_concrete_id(module_name, monkeypatch):
    """The fallback must carry the endpoint only — never a pinned model.

    `murakumo-main` is an alias the fleet resolves; naming the model behind it
    would pin this app to whatever was current on the day it was written.
    """
    monkeypatch.delenv("VLLM_MODEL", raising=False)
    mod = _reload(module_name)
    assert mod._VLLM_MODEL == "murakumo-main"
    # `tier0-general` was the previous pinned default.
    assert mod._VLLM_MODEL != "tier0-general"


@pytest.mark.parametrize("module_name", _LLM_MODULES)
def test_env_override_still_wins(module_name, monkeypatch):
    """Positive control: passes before and after the alias change.

    If this ever fails, the fix broke the escape hatch rather than the default.
    """
    monkeypatch.setenv("VLLM_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("VLLM_MODEL", "some-local-build")
    mod = _reload(module_name)
    assert mod._VLLM_URL == "http://127.0.0.1:4000/v1"
    assert mod._VLLM_MODEL == "some-local-build"


@pytest.mark.parametrize("module_name", _LLM_MODULES)
def test_trailing_slash_is_stripped_from_override(module_name, monkeypatch):
    """Also a positive control — URL joining is `f"{url}/chat/completions"`."""
    monkeypatch.setenv("VLLM_URL", "https://example.invalid/v1/")
    mod = _reload(module_name)
    assert mod._VLLM_URL == "https://example.invalid/v1"
