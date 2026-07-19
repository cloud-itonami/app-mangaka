# Image-to-VRM Authoring Stack — Ghost Hacker 57 Characters

ADR-2605141200 P16 — automated character VRM authoring Pregel
(`compose_character_vrm.topology.yaml`). Replaces the 57 × manual
Blender sessions implied by P13's artist runbook with an open-source
GPU pipeline.

## Hard invariant: self-hosted production runtime

**Every node on the production runtime path runs against open-weight /
OSS-source models hosted on our own infrastructure** (murakumo fleet,
VKE render pool, B2-mirrored weights). External commercial APIs
(Mixamo, Adobe, OpenAI, Anthropic, Hume, Higgsfield, Runway, …) are
**permitted ONLY offline** as teacher signals for the distillation
checkpoints we host — see `TRAINING_PIPELINE.md`. The runtime never
phones home to a third-party SaaS for character generation.

Concrete consequence: even though Mixamo's auto-rig API is free + no-
key + high-quality, it does NOT appear in `compose_character_vrm.topology.yaml`.
It only appears in `TRAINING_PIPELINE.md` as a teacher signal used
once-per-quarter to refresh the in-house `rignet-anime-v0.<n>` student
checkpoint stored at `b2://etzhayyim-models/rignet-anime-v0.*/`.

## Recommended stack (best fit for anime / cell-shaded characters)

```
profile.json + reference.png
   │
   ├── [1] CharacterGen (PKU, SIGGRAPH 2024, MIT)
   │       anime-tuned multi-view diffusion → A-pose 4-view PNGs
   │       https://github.com/zjp-shadow/CharacterGen
   │
   ├── [2] Hunyuan3D-2 (Tencent, OSS) or InstantMesh (TencentARC, Apache-2.0)
   │       multi-view → textured GLB mesh
   │       https://github.com/Tencent/Hunyuan3D-2
   │       https://github.com/TencentARC/InstantMesh
   │
   ├── [3] Blender Rigify template-fit (GPL tool) → in-house RigNet distill (MIT)
   │       mesh → rigged humanoid skeleton, ALL self-hosted
   │       https://docs.blender.org/manual/en/latest/addons/rigging/rigify/
   │       https://github.com/zhan-xu/RigNet  (base architecture; weights are ours)
   │       (Mixamo / Adobe API is offline-only teacher; never on the runtime path.)
   │
   ├── [4] MediaPipe Face Landmarker (Apache-2.0)
   │       reference.png → 52 ARKit blendshape weights + 478 face landmarks
   │       https://developers.google.com/mediapipe/solutions/vision/face_landmarker
   │
   ├── [5] Blender 4.1 + saturday06/VRM_Addon_for_Blender (MIT)
   │       glb (rigged) + ARKit blendshapes + spring-bone hints → VRM 1.0
   │       https://github.com/saturday06/VRM_Addon_for_Blender
   │       (Blender CLI: `blender --background --python bind_vrm.py`)
   │
   ├── [6] kami-vrm validation (in-repo)
   │       parse_vrm + humanoid coverage + polygon budget
   │
   └── [7] attachCharacterVrm (P13)
           B2 content-addressed PUT + vertex_mangaka.props.vrmBlobKey
```

Total per-character GPU time: ~3–5 minutes on a 24 GB card. 57
characters: **~3 GPU hours** sequential, less if pool capacity allows.

## Alternative tracks

### Single-image direct (skip CharacterGen, just feed `reference.png`)

Faster but lower quality for anime — TRELLIS / Hunyuan3D-2 / TripoSR
can each take a single image and produce a mesh, but anime characters
hit failure modes (flat shading misread as geometry, hair tessellation
wrong). Use only when CharacterGen multi-view fails (rare with
well-lit refs).

| Tool | License | Speed | Quality (anime) | Notes |
|---|---|---|---|---|
| **TRELLIS** (Microsoft) | MIT | ~30s | B+ | SOTA quality, Gaussian + mesh dual rep |
| **Hunyuan3D-2** | Tencent OSS | ~90s | A- | best texture map output, anime tuning required |
| **InstantMesh** | Apache-2.0 | 600ms | B | fastest, lower fidelity |
| **TripoSR** | MIT | 500ms | C | baseline, easy to LoRA fine-tune |
| **Wonder3D** | AGPL-3.0 | ~60s | B+ | **AGPL — commercial trap**, prefer alternatives |

### Rig-only (skip if your VRM has rig already)

| Tool | License | Runtime fit | Notes |
|---|---|---|---|
| **Rigify (Blender)** | GPL tool | ✓ **production** | Manual meta-rig placement → auto-generate. We automate the meta-rig fit via mesh bounding box + MediaPipe head landmark; works offline + self-hosted. |
| **RigNet** | MIT | ✓ **production fallback** | Voxel-based ML auto-rig. We host our own distilled-on-anime checkpoint at `b2://etzhayyim-models/rignet-anime-v0.*/`. |
| **Mixamo** (Adobe) | proprietary free | ✗ **train-only** | HTTP API, no key needed. Used offline as the teacher signal for the in-house RigNet distill. Never called from production. |
| **AccuRIG** (Reallusion) | proprietary standalone-free | ✗ **train-only** | CLI usable; same role as Mixamo — offline teacher only. |

### Anime-specific avatar generation (alternative to CharacterGen)

| Tool | License | Notes |
|---|---|---|
| **VRoid Studio** (Pixiv) | proprietary free | GUI only, no pod automation. Gold standard for hand-authored VRM. |
| **MV-Adapter** | Apache-2.0 | LoRA adapter for stable diffusion → multi-view consistent. Anime LoRA available. |
| **AnyDressing** | Apache-2.0 | clothed character on rigged base, useful for school uniform variations. |
| **Era3D** | MIT | character-friendly multi-view diffusion, smaller model than CharacterGen. |

### Manual fallback (the P13 path)

If automation fails for a specific character (complex hair like
spaghetti braids, unusual proportions), the original P13 artist runbook
still works: hand-author the VRM in VRoid Studio / Blender, drop
`avatar.vrm` next to `profile.jsonld`, run
`scripts/ingest-vrms.ts`. The two paths coexist.

## License compatibility summary

| Component | License | Commercial OK | Modifications must be OSS? |
|---|---|---|---|
| CharacterGen | MIT | ✓ | no |
| Hunyuan3D-2 | Tencent OSS (Apache-like) | ✓ with attribution | no |
| InstantMesh | Apache-2.0 | ✓ | no |
| TripoSR | MIT | ✓ | no |
| Mixamo | Adobe TOS | ✓ free tier | n/a |
| RigNet | MIT | ✓ | no |
| MediaPipe | Apache-2.0 | ✓ | no |
| Blender | GPL | ✓ (output not GPL) | n/a (we use it as a tool) |
| VRM Add-on | MIT | ✓ | no |
| **Wonder3D** | **AGPL-3.0** | **⚠ network-use trigger** | **YES if served over network** |

We deliberately **avoid Wonder3D and other AGPL/CC-BY-NC tools** —
ghost-hacker is destined for commercial publication via
mangaka.etzhayyim.com. AGPL would force the entire pod stack to be open-
sourced. The recommended stack above is 100% permissive (MIT / Apache-
2.0 / GPL tool use).

## Pod images (P16 work)

Each `mcp_tool` node in `compose_character_vrm.topology.yaml` ships as
its own ghcr.io image. Build invocations live in
`70-tools/scripts/build-vrm-pipeline-pods.sh` (TODO):

| Image | Base | Approx size | External calls? |
|---|---|---|---|
| `ghcr.io/etzhayyim/character-gen:0.1` | nvidia/cuda:12.4 + CharacterGen weights (mirrored at `b2://etzhayyim-models/character-gen-v1/`) | ~6 GB | none |
| `ghcr.io/etzhayyim/hunyuan3d-2:0.2` | nvidia/cuda:12.4 + Hunyuan3D-2 weights (B2 mirror) | ~12 GB | none |
| `ghcr.io/etzhayyim/mediapipe-face:1` | python:3.11-slim + mediapipe wheel (bundled blendshape model) | ~600 MB | none |
| `ghcr.io/etzhayyim/blender-rigify-rignet:0.1` | blender:4.1 + Rigify addon + saturday06/VRM_Addon + in-house RigNet checkpoint | ~3.5 GB | none |
| `ghcr.io/etzhayyim/blender-vrm:4.1` | blender:4.1 + saturday06/VRM_Addon | ~3 GB | none |

Each image exposes a single XRPC endpoint matching its lexicon NSID.
The `vke-render-pool` is the GPU-bearing node selector; the CPU-only
pools (`vke-cpu-pool`) run the rig + bind + validate stages.

## Driver script

When the 8 pod images and lexicon seeds are in place, the 57-char
batch authoring is a single `foreach` loop. Drop-in driver:

```bash
# scripts/author-ghosthacker-vrms.ts  (P16 follow-up)
MANGAKA_API_KEY=$(op read 'op://etzhayyim Japan株式会社/lg-mangaka/api-key') \
deno run -A scripts/author-ghosthacker-vrms.ts --concurrency 1
```

Outputs:
- 57 × `<character>/avatar.vrm` written to disk for inspection
- 57 × `attachCharacterVrm` calls finalising the pipeline
- `data/ghosthacker/vrm-authoring-report.json` with timings + failure cases

## Risk register

| Risk | Mitigation |
|---|---|
| CharacterGen license amendment / model takedown | Weights already mirrored at `b2://etzhayyim-models/character-gen-v1/`; runtime never touches GitHub or Hugging Face. Track `Era3D` as the OSS-stable backup. |
| Anime character geometry fails Hunyuan3D-2 priors | TripoSR fallback (MIT, baseline weights mirrored at `b2://etzhayyim-models/triposr-v1/`) — manual touch-up if both fail. |
| Rigify template-fit produces broken bind on unusual proportions | In-house RigNet distill fires as the second track inside the same pod. Both report `rigSource` to the state channel so visual review can sort failures. |
| In-house RigNet checkpoint regression after a retrain | Roll back B2 model bucket via versioned key; pod image picks the active version via env var. Distill workflow keeps the 3 most recent checkpoints. |
| GPU pool saturation | Sequential by default; `concurrency` flag opt-in only |
| ARKit blendshape mismatch (kami-vrm expects 7 expressions, MediaPipe outputs 52) | `extract_blendshapes` tool maps MediaPipe → VRM Expression preset set |
| Spring bone authoring (hair) needs hand-tuning | `bind_vrm` infers from `profile.gh:hair` keywords (`"long"` / `"twin tail"` / `"ponytail"`) — fallback to no-spring is acceptable |

## What ships now vs. what's pending

| Phase | Scope | Status |
|---|---|---|
| **P16-a (this commit)** | Topology YAML + stack survey + license matrix | ✓ |
| P16-b | 8 lexicon JSONs + seed migrations | open |
| P16-c | 5 pod images (CharacterGen / Hunyuan3D-2 / MediaPipe / Mixamo / Blender-VRM) | open — multi-day docker build work |
| P16-d | `author-ghosthacker-vrms.ts` driver script | open |
| P16-e | DMN policy `vrmBindRetry@1.0.0` for the retry edge | open |
| P16-f | First batch authoring + visual review (Chise + Kota first) | open — gated on a-c |
