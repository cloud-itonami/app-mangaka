# VRM Character Ingestion — Ghost Hacker Series

ADR-2605141200 P13 + P14 production runbook. **For artists handing off
VRM character assets** to the mangaka 3D scene pipeline.

## TL;DR

```bash
# 1. Drop a `avatar.vrm` next to the existing profile.jsonld:
#    data/ghosthacker/resources/characters/Chise/avatar.vrm
#    data/ghosthacker/resources/characters/Kota/avatar.vrm
#    ...

# 2. Dry-run to confirm what gets pushed:
cd 60-apps/etzhayyim-project-mangaka
deno run -A scripts/ingest-vrms.ts \
    data/ghosthacker/resources/characters --dry-run

# 3. Real ingest (uploads + patches vertex_mangaka):
MANGAKA_API_KEY=$(op read 'op://etzhayyim Japan株式会社/lg-mangaka/api-key') \
deno run -A scripts/ingest-vrms.ts data/ghosthacker/resources/characters
```

Once the script reports `✓ attached  blobKey=blobs/mangaka/vrm/<sha256>`,
the next `compose_scene_3d` run will pick the VRM up via
`tool_resolve_assets` → `tool_place_scene` → real character silhouette
in the wgpu render.

## VRM authoring requirements

| Constraint | Why |
|---|---|
| VRM 1.0 binary (`.vrm` = glTF binary container) | The tool validates the `glTF` magic + version 2 before uploading. VRM 0.x is rejected. |
| Humanoid bone mapping populated | `kami-vrm::humanoid::to_kami_skeleton` reads VRM 1.0 humanoid bones; missing bones break the pose lexicon (`action.dash`, `action.swing`, ...). At minimum: `hips, spine, chest, head, leftUpperArm/leftLowerArm/leftHand, rightUpperArm/rightLowerArm/rightHand, leftUpperLeg/leftLowerLeg/leftFoot, rightUpperLeg/rightLowerLeg/rightFoot`. |
| ARKit-style blendshape names | `MangakaScene::expression(Happy/Angry/Sad/Surprised/Determined/Pained/Smirk)` looks up these labels on the VRM expression preset map. Names must match (case-insensitive). |
| Spring bones for hair / cloth (optional) | If present, `kami-vrm::SpringBone` settles them during `MangakaScene::settle(30)`. Without them the character renders rigid but doesn't fail. |
| Polygon budget < 50k tris | Pod-side wgpu pipelines target 60fps render budget; > 50k tris adds latency without panel-quality benefit. Use Houdini / Blender decimate before export. |

## Per-character folder layout

```
data/ghosthacker/resources/characters/Chise/
├── profile.jsonld          # existing (gh:appearance / gh:background)
├── reference.png           # existing (2D character ref for M2+ref)
├── avatar.vrm              # ← NEW (your VRM file)
└── pose-refs/              # optional, ignored by the ingest CLI
    └── *.png
```

The CLI derives `characterRkey = "ch-" + folder.toLowerCase()`. Override
with `--rkey-prefix` if the production rkey scheme differs.

## How the upload pipeline works

```
artist hands off                            mangaka pod
  Chise/avatar.vrm                              com.etzhayyim.mangaka.tools.attachCharacterVrm
       │                                              │
       │  scripts/ingest-vrms.ts                      │
       │  POST /xrpc/...attachCharacterVrm            │
       │  { characterRkey: "ch-chise",                │
       │    vrmContentB64: "<base64>" }               │
       ├─────────────────────────────────────────────►│
       │                                              ▼
       │                                       glTF magic check
       │                                              │
       │                                              ▼
       │                                       B2 PUT blobs/mangaka/vrm/{sha256}
       │                                              │
       │                                              ▼
       │                                       UPDATE vertex_mangaka
       │                                       SET props = json_set(props, 'vrmBlobKey',
       │                                              'blobs/mangaka/vrm/{sha256}')
       │                                       WHERE kind='character' AND rkey='ch-chise'
       │                                              │
       │  ◄───────────────────────────────────────────┤
       │  { blobKey: "blobs/mangaka/vrm/9b...",        │
       │    vertexId: "at://did:web:mangaka.etzhayyim.com/ │
       │                com.etzhayyim.mangaka.character/ch-chise",
       │    status: "attached" }                      │
       ▼                                              │
   next render run                                    │
   compose_scene_3d.resolve_assets reads              │
   vertex_mangaka.props.vrmBlobKey ─────────────────► │
                                                      ▼
                                       kami_mangaka_scene::load_character
                                       fetches VRM bytes via the wheel's
                                       internal B2 client → settles spring
                                       bones → projects into the render pass
```

## Idempotency

`attachCharacterVrm` is fully idempotent:

| Re-run condition | Behaviour |
|---|---|
| Same bytes, same character | Status: `unchanged`. Zero work — B2 sha256 already matches, props.vrmBlobKey unchanged. |
| New version of the VRM (different bytes) | Status: `attached`. New B2 blob, props.vrmBlobKey patched. Old blob stays in B2 (content-addressed history). |
| `characterRkey` not in `vertex_mangaka` | Warning surfaced; blob uploaded but no graph write. Run the character ingest first (`scripts/import-jump-page.ts` etc.). |
| B2 not configured on the pod | Tool returns `error: "B2 not configured"` — escalate to ops. |

## Verification

After a successful run:

```bash
# Confirm B2 blob landed:
curl -I https://mangaka.etzhayyim.com/api/blob/blobs/mangaka/vrm/<sha256>
#   → 200 OK, content-type: model/gltf-binary

# Confirm character row sees the new key (replace ch-chise / API key):
curl -X POST https://mangaka.etzhayyim.com/xrpc/com.etzhayyim.mangaka.tools.resolveAssets \
  -H "content-type: application/json" \
  -d '{"panelPlan": {"characters": ["ch-chise"]}}'
#   → {"assetRefs": {"characters": {"ch-chise": {"vrm_blob_key": "blobs/mangaka/vrm/<sha256>", ...}}}}
```

The next `compose_scene_3d` run for any panel that references the
character will pick up the new VRM with no further intervention.

## Bulk re-ingest

When a character set changes (e.g. arc 0-2 redesigns), re-run the script.
Content-addressing means unchanged VRMs are no-ops, only the diff
actually transfers.

```bash
deno run -A scripts/ingest-vrms.ts \
    data/ghosthacker/resources/characters
```

Logs end with a `summary: N ok · N warn · N err · N dry/skip` so artist
+ pipeline ops can see at a glance how many landed.

## Related

- `00-contracts/lexicons/com/etzhayyim/apps/mangaka/tools/attachCharacterVrm.json` — wire contract
- `60-apps/etzhayyim-project-mangaka/lg/lg_mangaka/tools.py:tool_attach_character_vrm` — tool implementation
- `30-graph/graph-schema/sql_migrations/20260514180000_seed_mangaka_attach_character_vrm_mcp_tool.up.sql` — registry seed
- `40-engine/kami-engine/kami-vrm/` — VRM parse / spring bone simulator
- `40-engine/kami-engine/kami-mangaka-scene/` — headless render facade
- ADR-2605141200 — full Phase C activation roadmap
