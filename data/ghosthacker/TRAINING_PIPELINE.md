# Distillation Training Pipeline — Open-Weight Student Models

Companion to `VRM_AUTHORING_STACK.md`. Documents how external commercial
APIs become **offline teacher signals** for our self-hosted student
checkpoints, while staying out of the production runtime path.

## Invariant

> **Production runtime never calls an external commercial API.**
> External APIs (Mixamo, Adobe, OpenAI, Anthropic, Hume, Higgsfield,
> Runway, …) appear only in offline distillation jobs that author
> training data for the open-weight student checkpoints we host on B2 /
> the murakumo fleet.

This is consistent with ADR-2605010000 (LLM Inference SSoT — RunPod
6000 Ada Unified Pod) + the Mehikari / Bonsai Cultivar ecosystem ADRs
(2605091300+) that mandate a self-grown vector substrate.

## The two ledger entries

| Path | Where it runs | Where outputs go | Allowed external APIs |
|---|---|---|---|
| **Production runtime** | `compose_character_vrm` / `compose_scene_3d` Pregels on VKE / murakumo fleet | B2 character VRMs, render PNGs, vertex_mangaka writes | **none — self-hosted weights only** |
| **Offline distillation** | `70-tools/distill/*.py` scripts on dev workstations + RunPod ada-6000 pods | Training datasets, fine-tuned checkpoints uploaded to B2 | Mixamo, OpenAI vision, Hume Expression, etc. (rate-limited, batched) |

## Distillation targets

### 1. `rignet-anime-v0.<n>` — auto-rig student

**Teacher**: Adobe Mixamo (free public API, auto-rig endpoint).
**Student**: RigNet (MIT base architecture, github.com/zhan-xu/RigNet) +
our anime-tuned weights.

**Process**:
1. Build a corpus of ~5,000 anime / cel-shaded character meshes (mix of
   gh:character set + public anime datasets: MMD models, VRoid samples,
   VRoidHub liberally-licensed avatars).
2. Submit each mesh to Mixamo's `Auto-rigger` REST endpoint, collect the
   returned skeletons (~13 sec/mesh, OAuth-free public endpoint).
3. Write each `(mesh, skeleton)` pair to `b2://etzhayyim-training-data/rignet-anime-corpus-v<n>/`.
4. Fine-tune RigNet's GNN head on the corpus (~6 GPU hours on a 6000
   Ada, weights are ~80 MB).
5. Upload the checkpoint to `b2://etzhayyim-models/rignet-anime-v0.<n>/`.

**Cadence**: ~quarterly, or when the visual review surfaces ≥10%
broken binds on the production runtime.

**Failure mode**: Mixamo API removed / Adobe TOS change → snapshot the
existing corpus is enough; future re-trains can use the previous
checkpoint as the teacher (self-distillation). The runtime never
notices.

### 2. `mangaka-critic-v0.<n>` — vision critic student

**Teacher**: OpenAI gpt-4o-mini-vision (P4 in-tree path).
**Student**: A Qwen-VL or similar open-weight VLM, fine-tuned on the
7-axis manga critique rubric (composition / silhouette / character
recognizability / framing / manga shot grammar / lighting drama /
action clarity).

**Process**:
1. Run `compose_scene_3d` with `OPENAI_API_KEY` set against the existing
   ghost-hacker arc 0-1 panels — log every `(image, rubric_breakdown)`
   pair from `_score_one_render`.
2. Accumulate ~50k pairs across 6 months of normal operation
   (production-shadow distill — the critic runs at 5% sampling rate
   alongside the deterministic fallback score).
3. Fine-tune a Qwen2.5-VL-7B with LoRA on the rubric (~12 GPU hours on
   6000 Ada).
4. Upload to `b2://etzhayyim-models/mangaka-critic-v0.<n>/`.
5. Production runtime points `kotodama.llm._VISION_TIER_OVERRIDES['vision']`
   at the murakumo gateway hosting this checkpoint instead of OpenAI.

**Cadence**: once the corpus reaches ~50k pairs.

**Failure mode**: OpenAI deprecates gpt-4o-mini-vision → the corpus
already gathered is enough for a frozen-teacher distill. The runtime
critique path already has the deterministic fallback score
(`MANGAKA_CRITIQUE_FALLBACK_SCORE`).

### 3. `hume-emotion-v0.<n>` — image emotion student

**Teacher**: Hume Expression Measurement API (lg_mangaka.hume_emotion
already wraps it).
**Student**: Already shipped as `kotodama.primitives.hume_image_head`
— a distilled student trained from Hume signals (the comment in
`hume_emotion.py` notes this explicitly). Runtime uses ONLY the
student; the Hume API is not called from production at all.

**Status**: ✓ already self-hosted (the in-tree path is the student).
This entry exists for completeness — the same pattern applies to all
future external signals we add.

### 4. `characterGen-anime-v0.<n>` — multi-view diffusion fork

**Teacher**: CharacterGen's official weights (already MIT, but the
authors stopped updating after SIGGRAPH 2024).
**Student**: A LoRA on top of the released checkpoint, fine-tuned on
the ghost-hacker character roster + similar anime corpora to lock
in the visual style we want.

**Process**: standard LoRA training in diffusers, ~3 GPU hours per
LoRA. Mounted into the `character-gen` pod image via env var
`CHARACTER_GEN_LORA_PATH=b2://etzhayyim-models/character-gen-lora-v0.<n>/`.

**Cadence**: once per arc that introduces new character archetypes.

## Lints

A repo-level lint (`70-tools/scripts/lint/no-ext-api-on-runtime.mjs`,
to-be-authored) walks every `vertex_mcp_tool_def` row + every
`compose_*.topology.yaml` and rejects:

- Hostname `mixamo.com` / `api.adobe.com` / `api.openai.com` /
  `api.anthropic.com` / `api.hume.ai` / etc. inside any production tool
  body referenced from a topology with an active `vertex_langgraph_deployment` row.
- Env var reads of `MIXAMO_*` / `OPENAI_*` / `ANTHROPIC_*` etc. from
  any python file under `lg_mangaka/tools.py` or pod entrypoints.

The same hostnames ARE allowed under `70-tools/distill/` and
`60-apps/etzhayyim-project-mangaka/lg/distill/` (offline-only paths).

## Inventory: where each external API stands today

| External API | Used today on runtime? | Used for distill? | Production student |
|---|---|---|---|
| Mixamo auto-rig | ✗ | ✓ (rignet-anime corpus) | `rignet-anime-v0.<n>` (in-house) |
| OpenAI gpt-4o-mini-vision | ✓ via P4 in-tree path | ✓ (manga-critic corpus, shadow-logged) | `mangaka-critic-v0.<n>` (planned) |
| OpenAI gpt-image-2 / Gemini 3 Pro Image | ✓ via lg-image-gen M2+ref / M3-3D | ✓ (long-term: distill into a local image-gen model) | TBD — likely SDXL / Pixart-Σ LoRA |
| Hume Expression | ✗ (already distilled) | ✓ historical | `hume_image_head` (in-repo, already shipped) |
| Higgsfield / Runway | ✗ | optional video distill teacher | not in scope yet |

The OpenAI vision + image-gen entries are the only **runtime**
external calls remaining. Both have a planned student. Until those
students are ready, those calls survive on the runtime path under the
existing API keys, with rate limits + budget caps. The character
authoring pipeline (`compose_character_vrm`) is **fully self-hosted
from day one** — no external API calls anywhere.

## Why this discipline

Three reasons:

1. **License + IP safety**: anything produced with a commercial API's
   output can carry derivative-work restrictions (Mixamo Auto-rig
   output is fine for distill input but not for direct ship; OpenAI
   image output policy varies). Distillation strips the dependency.
2. **Cost predictability**: 57 × 5 = 285 character authoring rounds
   per arc at API rates ($0.50 / multi-view) = ~$140 / arc. Over 30
   arcs = $4,200. Self-hosted on the existing fleet = $0 incremental.
3. **Sovereignty**: an Adobe / OpenAI policy change can't break
   ghost-hacker series 5's release. The in-house student keeps
   working.

## Files in this discipline

| Path | Purpose |
|---|---|
| `compose_character_vrm.topology.yaml` | production runtime — no ext APIs |
| `VRM_AUTHORING_STACK.md` | stack chosen because every node is self-hosted |
| `TRAINING_PIPELINE.md` (this file) | distillation workflow that uses ext APIs |
| `70-tools/distill/*` | offline distill scripts (planned) |
| `b2://etzhayyim-models/<student>-v0.<n>/` | versioned student checkpoint store |
| `b2://etzhayyim-training-data/<corpus>-v<n>/` | versioned training corpus |
