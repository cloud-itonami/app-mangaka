# Ghost Hacker Import — Migration Notes

Imported from `~/github/ghosthacker/260123-jump/` on **2026-05-12** so the series can be continued at `mangaka.etzhayyim.com`.

## Layout

```
60-apps/etzhayyim-project-mangaka/
├── data/ghosthacker/                  # this directory (imported)
│   ├── PROJECT.jsonld                 # series-level manifest (gh:Project)
│   ├── README.md                      # arc 0-1 raw script
│   ├── docs/adr/                      # ghosthacker-local ADR (langgraph pipeline)
│   ├── drafts/                        # episode drafts (md, plot/policy notes)
│   ├── resources/
│   │   ├── characters/                # 59 character JSON-LD profiles
│   │   ├── environments/              # 33 scene environment prompts
│   │   ├── organizations/             # 10 in-story orgs
│   │   ├── props/, settings/, jobs/   # supporting data
│   │   ├── chat_history/              # 21 prior LLM sessions
│   │   ├── episodes/<slug>/           # per-episode jsonld + manifests
│   │   ├── images/                    # ► symlink → ~/github/.../images (5.8 GB)
│   │   ├── episodes/arc0-1-origin/rendered-pages{,-png}/  # ► symlinks
│   │   ├── storyboard.jsonld          # master storyboard
│   │   ├── manga_script.jsonld        # full script
│   │   ├── generation_prompts.jsonld  # standardized prompts
│   │   └── incidents.jsonld           # cyber incident dataset
│   ├── scripts/                       # ts pipeline + helpers
│   │   ├── lg-image-gen/              # (also copied to ../../lg-image-gen)
│   │   ├── migrate-arc0-1-to-v2.ts
│   │   ├── phase3-*-*.ts
│   │   └── generate-character-avatars.ts
│   ├── arc0-1-origin.pdf              # ► symlink (445 MB)
│   └── arc0-1-origin-preview.pdf      # ► symlink (38 MB)
└── lg-image-gen/                      # canonical LangGraph TS pipeline (full copy)
    ├── package.json
    ├── README.md
    └── src/{graph-m2,graph-m3,run,phase3-4-semantic-panels,lib/…}
```

## What was symlinked vs copied

| Item | Strategy | Reason |
|---|---|---|
| `*.jsonld`, `*.md`, `*.ts`, `*.sh` | **full copy** | small, source of truth, must be tracked |
| `resources/images/` (5.8 GB) | **symlink** | derived art assets, regenerable, disk-bound |
| `episodes/arc0-1-origin/rendered-pages{,-png}/` (1.1 GB) | **symlink** | rendered deliverables |
| `arc0-1-origin{,-preview}.pdf` (483 MB) | **symlink** | compiled book |

All symlinks point back to `~/github/ghosthacker/260123-jump/...` — the original repo remains the source of binary content. If `~/github/ghosthacker/` is moved or deleted, copy or regenerate the targets first.

## Current state (from PROJECT.jsonld)

- **Status**: `Arc 0-1「パスワードは覚えるな」45ページ最終構成完成`
- **Last session**: 2026-01-26 — verylonganimals avatar configuration
- **Episodes available**: 19 (arc0-1-origin, arc0-2-private-account, arc0-3-digital-footprint, 13× 260125-jump-arc{A,B,C,D}-{1,2,3}, 260123-cschool-*, 260125-parent-smartphone-safety)
- **Only arc0-1-origin has been rendered to PDF/PNG.** The rest exist as `episode.jsonld` only.

## Continuing the series in mangaka.etzhayyim.com

Two complementary paths:

### Path A — Direct manga authoring on mangaka.etzhayyim.com (Genko canvas)

The mangaka appview already has import scripts at
`60-apps/etzhayyim-project-mangaka/scripts/import-jump-all.ts` that ingest these
episodes into the live `mangaka.etzhayyim.com` PDS as `com.etzhayyim.mangaka.document`
records. Each episode becomes 1 document with deep-link
`https://mangaka.etzhayyim.com/at/mng4k4x1.etzhayyim.com/com.etzhayyim.mangaka.document/doc-gh-<slug>`.
Run with `deno run scripts/import-jump-all.ts` after updating the `JUMP_DIR`
constant if needed (currently points at the original `~/github/...` path).

### Path B — Continue the LangGraph TS image pipeline

```bash
# 1. Provide OpenAI / OpenRouter keys (Keychain → env)
export OPENAI_API_KEY=$(security find-generic-password -s etzhayyim.openai -a OPENAI_API_KEY -w)
export OPENROUTER_API_KEY=$(security find-generic-password -s etzhayyim.openrouter -a OPENROUTER_API_KEY -w)

# 2. Install + run on the next episode
cd 60-apps/etzhayyim-project-mangaka/lg-image-gen
npm install
# generate the missing panels (uses gh:needsImageGeneration flag)
npx tsx src/run.ts --pipeline m2ref --only-pending \
  --manifest ../data/ghosthacker/resources/episodes/arc0-2-private-account/episode.jsonld
```

The pipeline reads `resources/images/episodes/episode:<slug>/pages/<n>/panel_<id>_v<v>.png` and writes new versions next to existing ones.

### Updating JUMP_DIR in import scripts

The scripts in `60-apps/etzhayyim-project-mangaka/scripts/import-jump*.ts` still
hard-code `/Users/junkawasaki/github/ghosthacker/260123-jump/resources`. After
this import you can either keep that path (works because of symlinks pointing
back) or rewrite it to
`60-apps/etzhayyim-project-mangaka/data/ghosthacker/resources` so the in-repo
copy is the source.
