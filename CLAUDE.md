# etzhayyim-project-mangaka — mangaka.etzhayyim.com

**Manga creation appview** — KAMI Engine canvas-based manga editor with AI-assisted drawing, panel layout, inking, and toning.

## Architecture

| 項目 | 値 |
|---|---|
| Domain | `mangaka.etzhayyim.com` |
| Runtime | **Single Worker** (TS Native) |
| nanoid | `mng4k4x1` |
| performerType | `service` (default sensitivity: `public`) |
| uiType | `appview` (KAMI Engine canvas) |

## Multi-DID Architecture `[DESIGN]`

| DID | 用途 |
|---|---|
| `did:web:mangaka.etzhayyim.com` | Controller (app 本体) |
| `did:web:mangaka.etzhayyim.com:work:{nanoid}` | Manga work (作品単位) |
| `did:web:mangaka.etzhayyim.com:chapter:{nanoid}` | Chapter (話単位) |
| `did:web:mangaka.etzhayyim.com:character:{nanoid}` | Character design sheet |

## Design E 3-Tier Write

| Tier | 用途 | 関数 | Collection NSID |
|---|---|---|---|
| **1 Social** | 作品公開・更新告知 | `AppBskyFeedPost(did, text, {embed})` | `app.bsky.feed.post` |
| **2 Domain** | work/page/panel/asset | `ComAtprotoRepoCreateRecord(kind, payload)` | `com.etzhayyim.mangaka.*` |
| **3 State** | エディタ設定・ブラシプリセット | `Preferences()` | server-side |

## Domain Record Types (Tier 2, camelCase)

| Kind | NSID | 内容 |
|---|---|---|
| `work` | `com.etzhayyim.mangaka.work` | 漫画作品 (title, genre, status, coverCid) |
| `chapter` | `com.etzhayyim.mangaka.chapter` | 話 (workId, chapterNum, title, pageCount) |
| `page` | `com.etzhayyim.mangaka.page` | ページ (chapterId, pageNum, width, height, layersCid) |
| `panel` | `com.etzhayyim.mangaka.panel` | コマ (pageId, x, y, w, h, order, contentCid) |
| `asset` | `com.etzhayyim.mangaka.asset` | 素材 (screentone, effect, background, character sheet) |
| `character` | `com.etzhayyim.mangaka.character` | キャラクターデザイン (name, appearance, expressions) |
| `document` | `com.etzhayyim.mangaka.document` | Genko canvas 状態 (B2 primary, metadata graph) |
| `project` | `com.etzhayyim.mangaka.project` | プロジェクト (B2 index + graph) |
| `organization` | `com.etzhayyim.mangaka.organization` | 作中組織 |
| `environment` | `com.etzhayyim.mangaka.environment` | 場面環境 (basePrompt) |
| `generatedImage` | `com.etzhayyim.mangaka.generatedImage` | AI 画像生成履歴 |
| `chatMessage` | `com.etzhayyim.mangaka.chatMessage` | プロジェクト内 LLM 対話 |

## Reactive Pipeline (ComAtprotoSyncSubscribeRepos) `[DESIGN]`

- `com.etzhayyim.mangaka.work` create -> social announcement via AppBskyFeedPost
- `com.etzhayyim.mangaka.page` create -> AI auto-panel layout suggestion
- `com.etzhayyim.mangaka.panel` create -> AI inking/toning assist

## KAMI Engine Integration `[DESIGN]`

| 機能 | KAMI Component | 用途 |
|---|---|---|
| Canvas rendering | wgpu renderer | ページ描画 (WebGPU + WebGL2 fallback) |
| Pen/Brush input | kami-input | 筆圧・傾き対応 stylus input |
| Layer compositing | kami-render | レイヤー合成 (ink, tone, color, sketch) |
| Text rendering | kami-text | SDF フキダシ・効果音テキスト |
| Panel layout | kami-ui-gpu | GPU コマ割りガイド |
| Post-processing | kami-postfx | スクリーントーン・集中線エフェクト |

## AT URI Deep-Link

AT Protocol URI scheme (`at://`) を HTTP path `/at/` にマッピング。正規化コスト 0 (`s|/at/|at://|`)。

```
https://mangaka.etzhayyim.com/at/mng4k4x1.etzhayyim.com/com.etzhayyim.mangaka.document/{rkey}
  ↔ at://mng4k4x1.etzhayyim.com/com.etzhayyim.mangaka.document/{rkey}
```

| Collection | Deep-link 例 |
|---|---|
| document | `mangaka.etzhayyim.com/at/mng4k4x1.etzhayyim.com/com.etzhayyim.mangaka.document/doc-gh-arc01-xxx` |
| work | `mangaka.etzhayyim.com/at/mng4k4x1.etzhayyim.com/com.etzhayyim.mangaka.work/work-arc0-1-origin` |

**Genko canvas**: pathname から `{authority}/{collection}/{rkey}` を parse → `loadDocument(rkey)` で自動ロード。

## Document Persistence (Graph Primary)

B2 storage removed 2026-04-11 → `_archive/60-apps/r2-mangaka-canvas-storage.ts`

| Layer | Storage | 用途 |
|---|---|---|
| **Graph (primary)** | `graphar.vertex_document` | document (id, name, convoId, document body via createRecord) |
| **Graph** | `graphar.vertex_project` | project metadata (graph query) |
| **PDS blob** | `blobs/anonymous/{sha256}` | AI 生成画像 (content-addressed, getBlob で配信) |

## File Structure

```
60-apps/etzhayyim-project-mangaka/
├── CLAUDE.md
├── wit/mangaka/package.wit           # Domain WIT capability
├── data/ghosthacker/                 # ghost hacker series source (imported 2026-05-12, see IMPORT.md)
│   ├── PROJECT.jsonld
│   ├── resources/{episodes,characters,environments,...}/
│   ├── resources/images/             # → symlink to ~/github/ghosthacker/.../images
│   └── scripts/
├── lg-image-gen/                     # LangGraph TS panel-image pipeline (3-stage / m2ref / m3)
│   └── src/{graph-m2,graph-m3,run,phase3-4-semantic-panels,lib/…}
└── wasm/etzhayyim-wasm-mangaka-mng4k4x1/
    ├── src/app.ts                   # TS Native — Design E reactive pipeline
    ├── kotodama.jsonld
    ├── wrangler.jsonc
    ├── package.json
    └── wit/world.wit                # Component WIT (contract + capability export)
```

## Cinematic Pipeline (kami-cine)

mangaka uses a **subset** of the 8-stage kami-cine pipeline (`etzhayyim:kami-cine@1.0.0`, `40-engine/kami-engine/wit/cine/package.wit`) for 3D-reference panels and motion-comic export. The full chain (worldModel → usdScene → neuralGeom → temporalField → neuralRender → diffusionPass → exrSeq → encode) is shared with animeka and dogaka; mangaka typically stops at stage 6 (diffusionPass) for inked still panels and runs all 8 only for animated chapter PVs.

| Use case | Stages | Output binding |
|---|---|---|
| 3D-reference panel (BG / mech pose) | 1 → 5 | `com.etzhayyim.mangaka.panel.contentCid` ← stage-5 beauty PNG |
| Inked AI panel | 1 → 6 | `com.etzhayyim.mangaka.panel.contentCid` ← stage-6 refined PNG |
| Animated chapter PV | 1 → 8 | `com.etzhayyim.mangaka.chapter` social post `embed.video` ← stage-8 mp4 |

Stage records live in the shared `com.etzhayyim.apps.cine.*` collections. Each render carries a `pipelineRunId` (TID) and `subjectKind = "mangaka.page"` or `"mangaka.panel"` + `subjectRef` strongRef. Stage executors run pod-side per ADR-2605111200; the mangaka edge worker only dispatches via XRPC.

Subscribe to `com.etzhayyim.apps.cine.encode` to derive social announcements on take-finalize.

### LangGraph Pregel implementation

Two new graphs in `lg/lg_mangaka/graphs/` execute the pipeline pod-side; both are registered in `lg/langgraph.json` and seeded into `vertex_langgraph_assistant` by migration `20260518_0001_mangaka_cine_pipeline_tables.py`.

| Graph | Stages | Pregel pattern | NSID handler |
|---|---|---|---|
| `cine_generate_scene` | 1-4 (worldModel → usdScene → neuralGeom → temporalField) | Sequential super-steps; **Send fan-out** at stage 3 (one reconstruction per region of the USD bbox) merged via a shallow-dict reducer. | `com.etzhayyim.mangaka.cineGenerateScene` |
| `cine_generate_panel` | 5-6 (neuralRender + diffusionPass) | `load_scene` → `plan_panels` → **Send fan-out** of `per_panel_render` (one per panel on the page) → `aggregate` → `finalize`. Each panel write goes through `record_stage` twice + `record_panel` once. | `com.etzhayyim.mangaka.cineGeneratePanel` |

Shared helpers live in `lg/lg_mangaka/cine.py` (`STAGE_NAMES`, `new_run_id`, `record_stage`, `record_run`, `record_panel`).

**Persistence** is dual-write per stage:
- `graphar.vertex_cine_stage` — shared ledger, one row per `com.etzhayyim.apps.cine.<stage>` artifact (works across mangaka/animeka/dogaka)
- `graphar.vertex_mangaka_cine_run` — per-`pipelineRunId` summary (status: `scene_ready` → `panels_rendered`)
- `graphar.vertex_mangaka_cine_panel` — per finalized panel (render_blob_key + refined_blob_key + score)

**Local Studio**: `bash lg/scripts/dev.sh` starts `langgraph dev` against `lg/langgraph.dev.json` (in-memory checkpointer, no RW required). Studio at `https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024`; API at `http://127.0.0.1:2024/docs`. Pick `cine_generate_scene` or `cine_generate_panel` in Studio, run with `"dry_run": true` to inspect the Pregel DAG + per-node state without touching B2 / RW / vLLM. For connected-dev runs, copy `lg/.env.example` → `lg/.env` and fill `RW_URL` / `VLLM_URL`.

**MCP server (`studio.etzhayyim.com/mcp`)** — Claude Code / agent integration, **passkey-free after first deploy**. 5 tools (JSON-RPC 2.0):

| Tool | 機能 | Backend |
|---|---|---|
| `studio.listGraphs` | 21 graphs 列挙 | proxy `/assistants/search` |
| `studio.getGraphDag` | Pregel nodes+edges | proxy `/assistants/{id}/graph` |
| `studio.runGraph` | 任意 graph invoke + SSE drain + image preview 集約 | proxy `/threads/{}/runs/stream`、Send fan-out も merge |
| `studio.mintApiKey` | sk_live_* mint (RW INSERT) | `auth_mint_api_key` Pregel (`lg/lg_mangaka/graphs/auth_mint_api_key.py`) — pod は cluster 内なので RW_URL 到達可 |
| `studio.restartStudio` | k8s rollout restart 案内 | manual `kubectl rollout restart` |

**Auth**: CF Access JWT (`@etzhayyim.com` / Microsoft Entra) で gate。`Cf-Access-Authenticated-User-Email` → `owner_did` 自動派生 (`did:web:mangaka.etzhayyim.com:user:{email-safe}`)。passkey は **studio.etzhayyim.com への 24h SSO 1 回のみ** — `etzhayyim authn signin` の chicken-and-egg は解消。

**Deploy state (2026-05-19)**:
- Image: `ghcr.io/etzhayyim/lg-mangaka-studio:0.1.1-amd64@sha256:eb6901c…` (slim, no torch/cuda — ~400MB vs 8GB)
- Pod: `lg-mangaka-studio` in `mitama-udf` ns、helm release rev 10、`studio.enabled=true`、replicas=2 (`sessionAffinity: ClientIP` 3h)
- Status: ✅ 21 graphs imported, mint API key INSERT 済 (`sk_live_comfyui_f931…` in `public.vertex_api_key`)
- 残: Studio Worker (svelte + /mcp) deploy (`etzhayyim authn signin` block 中)、CF Tunnel + CF Access apps (operator manual)、comfyui worker migration to yatabase-style pod-side authResolveApiKey (migration debt — ADR-2605111200 で Hyperdrive-Worker 検証が壊れた、未 migrate)

**Claude Code 統合**: `~/.claude/mcp.json` に:
```json
{ "mcpServers": { "studio": { "url": "https://studio.etzhayyim.com/mcp" } } }
```
追加 → `mcp__studio_listGraphs` / `mcp__studio_runGraph` / `mcp__studio_mintApiKey` などが Claude tool palette に並ぶ。最初の使用時に CF Access SSO challenge (Microsoft Entra)、以降 24h はトークン継続。

**Team Studio (`studio.etzhayyim.com`)** — self-hosted, **no LangSmith dependency**. Two-tier:

| Tier | Where | What |
|---|---|---|
| UI + auth edge | CF Worker `kotodama-stdk2024` at `studio.etzhayyim.com/*` | Svelte 5 SPA (graph list + Mermaid DAG + invoke form + SSE stream). Behind CF Access (Microsoft Entra IdP, `@etzhayyim.com` domain). Code: `appview-studio/etzhayyim-wasm-studio-stdk2024/` |
| LangGraph backend | k8s pod `lg-mangaka-studio` × 2 (mitama-udf ns) at `studio-api.etzhayyim.com` | Stock `langgraph dev` (in-memory, ClientIP affinity). Image: `lg/Dockerfile.studio`. Chart: `50-infra/vultr/lg-mangaka-pool/templates/studio.yaml`, toggle `studio.enabled=true`. Tunnel: `50-infra/vultr/cloudflared/lg-mangaka-studio-tunnel.yaml`. Behind CF Access **service-token** policy — only the Worker's `CF-Access-Client-Id/-Secret` pair passes through. Audit disabled (`LG_AUDIT_DISABLED=1`). |

Operator runbook (8 steps including the two CF Access apps) lives in the tunnel YAML header. End-user opens `https://studio.etzhayyim.com/` → SSO → pick `cine_generate_scene` / `cine_generate_panel` (or any of the 20) → run with `"dry_run": true` for cost-free inspection.

Invocation pattern (XRPC → bpmn-dispatcher → LangServer pod):
```
POST /xrpc/com.etzhayyim.mangaka.cineGenerateScene
  { subject_kind, subject_ref, prompt, style, world_kind, frame_start, frame_end }
  → { status: "scene_ready", pipeline_run_id, stage_records }

POST /xrpc/com.etzhayyim.mangaka.cineGeneratePanel
  { pipeline_run_id, page_rkey, panels: [{panel_rkey, framing, charactersAppearing}] }
  → { status: "panels_rendered", panels: [{panel_rkey, panel_blob_key, score}] }
```


## Ghost Hacker Series (imported 2026-05-12)

Series source lives at `data/ghosthacker/`; see `data/ghosthacker/IMPORT.md`. The 5.8 GB image library and rendered PDF/PNG output remain at `~/github/ghosthacker/260123-jump/` and are reached through symlinks. Continue the series by either (a) running `scripts/import-jump-all.ts` to publish episodes into the live mangaka PDS, or (b) running `lg-image-gen/src/run.ts --pipeline m2ref --only-pending` against the next episode's `episode.jsonld`.
