"""mangaka_train_character_lora — kohya LoRA training via ComfyUI.

Wraps the `Lora Training in ComfyUI` node (LarryJane491). Trains a
per-character LoRA from a folder of cleanly-captioned images on the
ComfyUI host. Output LoRA lands in `models/loras/` and can be loaded
by any downstream graph (panel_hq, panel_flux_pulid, etc.) for true
character identity preservation (95%+ vs ~75% with IPAdapter).

Prereq (on the ComfyUI host, prepared by the artist before triggering):
  <data_path>/<num>_<trigger_token>/
      img_001.png
      img_001.txt   <- caption file, kohya convention
      img_002.png
      img_002.txt
      ...

A reasonable mangaka workflow:
  1. mangaka_generate_character batch=2 -> design sheet
  2. Crop 8-12 clean single-view images, save as PNG + matching .txt
     captions (descriptive tags + the trigger token verbatim).
  3. Stage them in C:\\Users\\gad\\lora-data\\10_yuki_persona\\ on the
     ComfyUI host.
  4. Trigger this graph with data_path = parent dir, output_name = yuki_persona.

Pregel: build -> submit -> poll -> END

Inputs:
    output_name           str   LoRA filename (without .safetensors)
    data_path             str   Windows abs path on ComfyUI host
    ckpt_name             str   base SDXL checkpoint
    batch_size            int   default 1
    max_train_epochs      int   default 10
    save_every_n_epochs   int   default 10
    clip_skip             int   default 2
    output_dir            str   default models/loras

Output:
    Inherits comfy_runner.poll_outputs shape. The actual artefact is the
    LoRA file at models/loras/<output_name>.safetensors — query
    LoraLoaderModelOnly afterwards to confirm.

Time budget: 20-40 min on AMD Radeon 8060S ROCm 7.2 for 8 imgs / 10
epochs / 512 res / bs=1. Defaults `timeout_seconds = 3600` (1 h).
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from lg_mangaka import comfy_runner as _runner
from lg_mangaka import comfy_workflows as _wf


class _State(TypedDict, total=False):
    output_name: str
    data_path: str
    ckpt_name: str
    batch_size: int
    max_train_epochs: int
    save_every_n_epochs: int
    clip_skip: int
    output_dir: str

    comfy_url: str
    timeout_seconds: int
    poll_interval_ms: int

    workflow: dict
    prompt_id: str
    number: int
    submit_response: dict
    started_at_ms: int
    status: str
    images: list
    raw_history: dict
    elapsed_ms: int
    error: str | None


async def _build(state: _State) -> dict[str, Any]:
    if not state.get("output_name") or not state.get("data_path"):
        return {"status": "error", "error": "output_name + data_path required"}
    wf = _wf.train_character_lora_workflow(
        output_name=state["output_name"],
        data_path=state["data_path"],
        ckpt_name=state.get("ckpt_name") or "illustriousXL_v01.safetensors",
        batch_size=int(state.get("batch_size") or 1),
        max_train_epochs=int(state.get("max_train_epochs") or 10),
        save_every_n_epochs=int(state.get("save_every_n_epochs") or 10),
        clip_skip=int(state.get("clip_skip") or 2),
        output_dir=state.get("output_dir") or "models/loras",
    )
    return {"workflow": wf}


async def _submit(state: _State) -> dict[str, Any]:
    if state.get("status") == "error":
        return {}
    return await _runner.submit_workflow(
        state["workflow"],
        comfy_url=state.get("comfy_url") or _runner.DEFAULT_URL,
    )


async def _poll(state: _State) -> dict[str, Any]:
    if state.get("status") == "error" or not state.get("prompt_id"):
        return {}
    return await _runner.poll_outputs(
        state["prompt_id"],
        comfy_url=state.get("comfy_url") or _runner.DEFAULT_URL,
        started_at_ms=int(state.get("started_at_ms") or 0),
        # Training can take 20-40 min — set a 1h ceiling.
        timeout_seconds=int(state.get("timeout_seconds") or 3600),
        poll_interval_ms=int(state.get("poll_interval_ms") or 10000),
    )


def _build_graph() -> StateGraph:
    g: StateGraph = StateGraph(_State)
    g.add_node("build",  _build)
    g.add_node("submit", _submit, retry_policy=RetryPolicy(max_attempts=2))
    g.add_node("poll",   _poll)
    g.add_edge(START, "build")
    g.add_edge("build", "submit")
    g.add_edge("submit", "poll")
    g.add_edge("poll", END)
    return g


GRAPH = _build_graph().compile(name="mangaka_train_character_lora")
