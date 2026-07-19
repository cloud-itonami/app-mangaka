# ADR — LangGraph TS panel-image generation pipeline

- **Status**: Accepted (2026-05-11)
- **Scope**: Ghost Hacker arc 0-1 origin (45 page + pretitle = 46 entries) panel image generation
- **Reference**: `260419-GH-jump.md` (v2 spec), `story-outline.jsonld`, `episode.jsonld`

## 背景

arc 0-1 の panel 画像 (216 active) を AI 画像生成で量産する必要があった。要件:

1. **キャラ identity の安定** — Ren / Nei / Yuto / Akira / Mei / Saki / nue 等、複数 panel 通して同じキャラに見える
2. **設定 (場所) の安定** — 教室シーンが教室で描かれる、Ren の部屋が捜査壁付き
3. **Jump レベル の表現力** — Naruto / One Piece / Aria / 攻殻機動隊 と同等の cinematic composition
4. **見開き対応** — climax page の double-page-spread
5. **scalable** — 216 panel を 1-2h で生成、再現可能

## 検討した 3 Method × 3 LangGraph パターン

### Method 1: キャラ層 → 背景層 → 機械合成 (sharp.composite)
- Pattern: Graph (deterministic DAG)
- 結果: avg 3.5/10 — char cutout が brittle (white→alpha 漏れ)、合成が depth/lighting blend できず人形貼付け感

### Method 2: 1 枚絵 + agent loop critic
- Pattern: Agent Loop (vision-LLM critique → conditional retry)
- 結果: avg 7.5/10、35s/panel — gpt-image-2 が単一画像で人物+設定を統合的に解釈できる

### Method 3: 3D-proxy (reference 直貼り) + harmonize
- Pattern: PEGEL (plan → parallel execute → assemble → eval)
- 結果: avg 6.0/10 — 設定 2/2 OK だが harmonize 段で identity 0/2 (gpt-image-2 が顔を再描画)

### 採用: Method 2 + Hybrid provider routing (gpt-image-2 + Gemini 3 Pro Image) + Phase 3.4 rich schema

## 決定事項

### 1. 画像生成: M2+ref pipeline

**graph-m2.ts** の状態遷移:
```
START
  → plan          (extract setting/visualNote, resolve focused char references)
  → generate      (/v1/images/edits with ref images + Jump-style prompt)
  → critique      (/v1/chat/completions vision: 7-axis rich critique)
  → [conditional] score >= 7 → persist; else iter < 3 → refine → generate
  → persist       (write versioned PNG, append to gh:generatedImages[])
  → END
```

決定根拠:
- gpt-image-2 の `/v1/images/edits` に reference 画像を直接渡せる (face identity 保持)
- vision-LLM critique で setting/character/text-clean drift を機械的に検知
- agent loop で平均 1-2 反復で score 7+ に収束

### 2. Panel decomposition: Phase 3.4 (LLM semantic)

**phase3-4-semantic-panels.ts** が gpt-4o で v2 outline script を panel に分解。

ABSOLUTE 制約:
1. **PARTITION** — script entry 0..N-1 が exactly once カバーされる
2. **COMPRESS** — 連続同 beat はマージ
3. **DIVERSE FOCUS** — 1 character に偏らない
4. **ACTIVE > PASSIVE** — 動いている character が focus
5. **EXPRESSIVE BODY SIGNALS** — concrete physical signals (汗・涙・拳・瞳孔) を必須

Patch loop: coverage incomplete or duplicate を検出したら LLM に CORRECTED full decomposition を要求。

### 3. Rich schema (panel jsonld)

各 panel が以下を持つ:
- `gh:sceneSubject` — 1 行 topic
- `gh:focusCharacter` — 視覚的主役 (1人 or "shared")
- `gh:allCharacters` — 画面内全員
- `gh:focusedCharacters` — reference を inject する対象
- `gh:props` — 物体一覧
- `gh:visualDescription` — 描く内容を 2-3 文で
- `gh:precedingBeat` / `gh:followingBeat` — narrative 連続性
- `gh:visualStyle` — `cinematic-close` / `anime-action` / `film-medium` / `establishing-illustration`
- `gh:tone` — `action` / `emotional` / `quiet` / `triumph` / `tense` / `comedic` / `ominous` / `contemplative`
- `gh:emotionPhysicalSignals` — `[{character, signals[]}]`
- `gh:panelLayout` — `{row, colSpan, rowSpan, size, emphasis, readingOrder}`
- `gh:scriptEntryIndices` — どの v2 entry を覆うか

page にも `gh:pageLayoutV3` (templateName, pageType, spreadWith, emotionalPeak)。

### 4. Visual style + 参照作品 anchoring

各 panel の visualStyle に応じて prompt suffix を変える:

| visualStyle | inspired by | 主な表現 |
|---|---|---|
| `cinematic-close` | 攻殻機動隊・One Piece emotional close | XCU eyes, rim lighting, depth of field |
| `anime-action` | Naruto fight panels, One Piece battle | foreshortening, motion lines, Dutch angle |
| `film-medium` | 押井守攻殻 dialogue, Aria medium shots | rule of thirds, 3-layer depth |
| `establishing-illustration` | Aria establishing pages | 細密背景、weather/light |

### 5. Shot-type 必須要件

Extreme Close Up / Close Up / Medium Shot / Wide Shot / Insert / OTS / POV ごとに、**Naruto/OP/GitS レベル** の composition 要件 (eyes 30% + catchlight + 1 physical signal、3-layer depth、rule of thirds 等) を強制。

### 6. Quality score (MiniMax)

```
Q_p = 0.25·completeness + 0.20·specificity + 0.20·char_distinction
    + 0.15·continuity + 0.10·prop_density + 0.10·visualStyle_clarity

Q_i = 0.25·critic + 0.15·setting + 0.15·char + 0.10·text_clean
    + 0.15·composition + 0.10·expression + 0.10·props_visible

Q_total = 0.5·min(Q_p, Q_i) + 0.3·sqrt(Q_p · Q_i) + 0.2·max(Q_p, Q_i)
```

採用根拠:
- `min` 重みが 50% — 最弱軸が支配的 (両方良くないと高得点取れない)
- 幾何平均 `geo` で中庸を保ち
- `max` 20% で突出した強みを少し評価

threshold:
- Q_total ≥ 0.75 → auto-ship
- 0.55 ≤ Q_total < 0.75 → manual review
- Q_total < 0.55 → auto-regen (max 3 反復)

### 7. Hybrid provider routing (2026-05-11 added)

Visual style と tone に応じて provider を route:

| tone / visualStyle | provider | 採用理由 |
|---|---|---|
| `ominous` / `tense` / `contemplative` / `quiet` / `emotional` | **Gemini 3 Pro Image** | 手描き horror manga (伊藤潤二系 / 押井守 GitS 系)、psychological intensity に最適 |
| `cinematic-close` (visualStyle) | **Gemini** | cross-hatching と raw line work で emotional close-up を活かす |
| `action` / `triumph` / `comedic` | **gpt-image-2 low** | clean anime action (Naruto / OP 系)、polished screen-tone work |
| `anime-action` (visualStyle) | **gpt-image-2 low** | dynamic motion line + speed effects に最適 |
| default | gpt-image-2 low | safe baseline |

実装: `lib/gemini.ts` `selectProvider(tone, visualStyle)`。
強制 override: `LG_FORCE_PROVIDER=gemini|openai` env var (moderation 回避時に有用)。

最終結果 (279 panels, 2026-05-11):
- Gemini route: ~217 panels (78%)
- OpenAI route: ~62 panels (22%)
- ship tier (Q≥0.75): 270 / 279 (96.8%)
- review tier: 9 (3.2%)
- regen tier: 0
- Avg Q_total: 0.88

### 8. インフラ

- **モデル**:
  - `gpt-image-2` (OpenAI direct, gpt-image-1 は禁止 throw)
  - `google/gemini-3-pro-image-preview` (OpenRouter経由)
- **画像サイズ**: 1024×1536 portrait (manga panel 比率)
- **quality**: `low` (cost-efficient で十分な品質)
- **vision critic**: `gpt-4o-mini` (response_format json_object)
- **シークレット**: macOS Keychain — 1Password CLI session timeout 回避
  - `etzhayyim.openai` / `OPENAI_API_KEY`
  - `etzhayyim.openrouter` / `OPENROUTER_API_KEY`
- **versioning**: 出力 PNG は `_v{N}.png` (N = 既存 generatedImages.length + 1)
- **history**: episode.jsonld の `gh:generatedImages[]` に append、`gh:currentImageIndex` で最新参照

## 検証結果 (2026-05-11)

- **p1 (8 panel)** rich schema + M2+ref: avg score 8.6/10
- **p2-p10 (64 panel)** rich schema + M2+ref: 64/64 OK, p6 が double-page-spread (with p7) と LLM が Jump 流に判断
- **Full arc 0-1 (279 panels, Hybrid pipeline 2026-05-11)**:
  - Generated 279/279 (100%)
  - ship tier 270, review tier 9, regen 0
  - avg Q_total 0.88
  - Gemini route 78%, OpenAI route 22% (psychological-horror manga tonality を反映)
  - 見開き判定: p6↔p7 (Renの捜査壁→Hacker Nues), p39↔p40 (/dev/null SLASH→Daemon消滅)
- 残課題: 服装 drift (自宅でも学生服)、p7n10 同部屋構図、p6/p7 spread タグ整合、9 review-tier panel の polish

## トレードオフ

- **コスト**: 1 panel あたり ~$0.02-0.04 (gpt-image-2 low + critic) → 216 panel ≈ $4-9
- **時間**: 35-90s/panel → 216 panel ≈ 2-4h
- **キャラ多様性**: focused 1 character まで ref 安定。3+ char ensemble は識別が薄れる傾向
- **見開き**: jsonld には spreadGroup を保存するが、画像生成は単一 panel ずつ (post-typesetting で見開き layout を組む)

## 代替案で却下したもの

- **M1 (layered)**: cutout pipeline が brittle、harmonize で identity を再描画される
- **M3 (3D-proxy)**: 完全 3D 構築は数週間コスト、本作 1 話分には合わない (連載開始時に再評価)
- **OpenRouter Gemini 3 Pro Image**: マルチモーダルだが OpenAI 直接の方が token コスト低・gpt-image-2 安定

## Phase 4 — Typesetting layer (2026-05-11 追加)

画像生成 layer の上に **manga typesetting layer** を追加:

### Manuscript frame (Jump 原稿用紙仕様)

`gh:manuscriptFrame` を episode 単位で保持:
```
format: weekly-shounen-jump-A4
trim:   210×297mm
bleed:  ±3mm
innerFrame: 180×270mm (x=15, y=15)
gutter: 3mm × 3mm
pageNumberArea: outer-bottom corner
readingDirection: right-to-left, top-to-bottom
```

### Page template library (`page-templates.jsonld`)

19 template (Jump 流):

**Standard pacing**:
- `tpl:impact-spread-1` — 1 panel = 1 page (title splash, climax)
- `tpl:standard-grid-4` — 2x2 calm pacing
- `tpl:jump-build-release-{5,6}` — Jump 標準
- `tpl:jump-7-asymmetric` — 多用される asymmetric
- `tpl:jump-9-grid` — dense educational (countermeasure pages)
- `tpl:dialogue-cascade-4` — calm dialogue
- `tpl:title-splash-3` — pretitle hook
- `tpl:reveal-spread-2`, `tpl:single-impact-with-margin`
- `tpl:double-page-spread` (見開き, pageSpan 2)

**Diagonal layouts (Jump 流ダイナミック)**:
- `tpl:diagonal-2-split` — time shift (before/after)
- `tpl:diagonal-3-cascade` — continuous motion (action build)
- `tpl:diagonal-x-cross` — collision / confrontation
- `tpl:vortex-impact-7` — central impact + peripheral fragments
- `tpl:shutter-pan-5` — camera-pan effect
- `tpl:inverse-diagonal-anxiety` — psychological wobble (逆斜)
- `tpl:flashback-blur-7` — memory / dream (soft-edge irregular)

### 斜めコマ割り効果分析

| 角度 | 効果 | 用途 |
|---|---|---|
| 15-30° | 軽い動勢 | 日常+α |
| 30-45° | 標準ダイナミック | バトル |
| 45-60° | 強い impact | 必殺技、ショック |
| 60°+ | 暴力的・極端 | 大破壊 |
| 逆方向 (negative) | 不安・違和感 | 心理サスペンス |

### Bubble system

panel ごとの `gh:bubbles[]` に:
- `sizeMode`: auto / fit-to-text / fixed
- `widthMm` / `heightMm` (manual override)
- `position` (xMm/yMm 相対座標)
- `tail` (方向+長さ)
- `style`: round / jagged (叫び) / thought (心) / narration / telop / radio / whisper
- `fontSize`: S / M / L / XL
- `overflowPolicy`: shrink-text / auto-extend-bubble / split-bubble
- `maxWidthFraction: 0.5`, `maxHeightFraction: 0.4` (panel に占める最大比 — 吹き出し肥大化防止)

emotion から style 自動導出: `shout` → jagged, `thought/monologue` → thought, default → round。

### SFX (擬音) system

panel ごとの `gh:sfx[]` (空 array で初期化):
- `text`: 「ドンッ」等
- `font`: impact / brush / hand-drawn / rough
- `size`: S / M / L / XL / spread
- `position` + `rotation` + `skew`
- `strokeWidth`, `strokeColor`, `fillColor`
- `effect`: speed-lines / burst / shadow / halo / none
- `crossesPanel`: 隣接 panel ID list (パネル越え擬音)

### Panel overflow (コマを超える表現)

`gh:panelOverflow`:
- `characterBreaksFrame`: { bodyPart, extendsTo[], effect: punch-out / lean-out / burst-through }
- `bubbleCrossesPanels[]`: panel ID 配列
- `sfxCrossesPanels[]`: 同上
- `backgroundContinuity`: 隣接 panel 背景連続
- `floatingPanelOnPage`: { zIndex, withShadow } (浮遊コマ)

### Template selection heuristics

`phase4-typesetting-schema.ts` の `selectTemplate()` 関数:
1. spread mark → `tpl:double-page-spread`
2. 単一 panel → `tpl:impact-spread-1`
3. プレタイトル → `tpl:title-splash-3`
4. action + impact 3-4 panel → diagonal
5. ominous/tense → inverse-diagonal-anxiety
6. contemplative/emotional → flashback-blur
7. countermeasure → jump-9-grid
8. デフォルト: panel count に応じた standard pacing

### Phase 4 適用結果 (2026-05-11, 46 pages / 279 panels)

```
13 × tpl:jump-7-asymmetric
11 × tpl:flashback-blur-7
 7 × tpl:jump-9-grid (countermeasure pages)
 3 × tpl:jump-build-release-5
 3 × tpl:dialogue-cascade-4
 2 × tpl:title-splash-3
 2 × tpl:diagonal-x-cross (climax conflict)
 2 × tpl:double-page-spread (p6/7, p39/40)
 2 × tpl:diagonal-3-cascade
 1 × tpl:impact-spread-1
+ 他
```

## Phase C — Rendering pipeline (2026-05-11 完了)

Phase 4 が *schema* 層 (manuscript frame + template library + bubble/SFX/overflow shape) に留まる
のに対し、Phase C は episode.jsonld を A4 SVG → PNG → PDF まで一気通貫で **印刷入稿可能**
な原稿に落とす rendering layer。

### 1. 縦書き吹き出し (vertical Japanese bubbles)
- `renderBubbleSvg()` が `bubble.type !== "narration"|"telop"` のとき `writing-mode="vertical-rl"`
- 列幅 / 列数を `estimateBubbleSizeVertical()` で逆算 (`fontSize × 0.55` glyph advance)
- 右→左 stacking: `bx = panelBounds.wMm - w - padding - bubbleIndex × (w + 1.5)`
- 検証: page-01.svg で 40 縦書きカラム emit (台詞メイン page)

### 2. Panel overflow (コマを超える表現)
- `renderPanel()` が `{ contained, overflow }` 2 layer を返却
- 全 panel ループ後に overflow layer を後置 → コマ境界 / 隣接 panel の上に z-index 上書き
- `gh:panelOverflow` を全 279 panel に injection 済 (`element / direction / extentMm / opacity`)
- drop-shadow filter で「コマから飛び出している」立体感を付与

### 3. SFX auto-positioning (LLM 配置)
- `src/sfx-auto.ts` — `gpt-4o` (JSON mode) で 0-2 SFX 提案 per eligible panel
- Eligible: `tone ∈ {action, triumph, tense, ominous}` ∨ `emphasis ∈ {impact, punchline}`
- 出力 schema: `{text, font, size(S/M/L/XL/spread), position(xMm,yMm), rotation(-30..30), effect}`
- 結果: 161 eligible panel → 126 で SFX 付与 / 135 entry / `gh:autoGenerated: true` でマーク

### 3.5 Panel overflow auto-population (LLM, 2026-05-12 追加)
- `src/overflow-auto.ts` — `gpt-4o` (JSON mode) で 0-1 overflow effect per eligible panel
- Eligible: tone ∈ {action, triumph, tense, ominous} ∨ emphasis ∈ {impact, punchline, focal} ∨ SFX size ∈ {L, XL, spread}
- Effect 候補 (mutually exclusive, 70%+ panel は `null`):
  - `characterBreaksFrame` → `{extensionMm: 4..18, extensionDirection: top|bottom|left|right}` 拡張 clip-path で画像をパネル外に滲ませる
  - `bubbleCrossesPanels`  → `string[]` (隣接 panel @id), bubble を overflow z-layer に昇格
  - `sfxCrossesPanels`     → `string[]` (同上), SFX 文字を panel 境界跨ぎに
  - `floatingPanelOnPage`  → `{withShadow: bool}` drop-shadow filter で「浮かんだコマ」
- 結果: 161 eligible panel → 44 panel (16%) に 45 effect 適用
  - characterBreaksFrame: 16 (主に triumph/punchline の hero shot)
  - floatingPanelOnPage:  12 (close-up inset)
  - sfxCrossesPanels:     12
  - bubbleCrossesPanels:  4
- 16% 採用率は「overflow は spotlight, overuse すると効果消失」設計通り

### 4. Japanese font embedding
- 全 SVG の `<style>` に `@import url('https://fonts.googleapis.com/css2?family=Noto+Serif+JP:...')`
- `font-family: 'Noto Serif JP', serif` を bubble / SFX 共通で指定
- librsvg は CSS `@import` を解決するため、PNG ラスタライズ時に正しい和文 glyph を埋め込み

### 5. PDF export (印刷入稿)
- `src/export-pdf.ts` — pdf-lib で A4 trim (210×297mm) + 3mm bleed
- 4 隅 trim mark: 5mm 線 + 2mm gap, 0.3pt 黒線
- 見開き (`gh:pageType: double-page-spread` ∧ `spreadWith > pageNumber`) はランドスケープ
  combined page として描き、second page は `skippedPages` で出力スキップ
- 結果: 46 SVG → 44 PDF page (p7/p40 が p6/p39 と統合), `arc0-1-origin.pdf` 449 MB

### Phase C 検証 (2026-05-12 更新)

```
SVG pages:                  46/46 (44 + 2 spread halves)
PNG pages:                  46/46 @ 150 dpi
PDF master (PNG lossless):  arc0-1-origin.pdf          445 MB
PDF preview (mozjpeg q=82): arc0-1-origin-preview.pdf   38 MB (11.7× smaller)
Vertical bubbles (p01):     40 columns
SFX glyphs (p35):           5 rendered
Font import per SVG:        1 (`Noto Serif JP`)
panelOverflow schema:       279/279 (100% stamped)
panelOverflow ACTIVE:       44/279 (16%, LLM-curated)
  - characterBreaksFrame:   16
  - floatingPanelOnPage:    12
  - sfxCrossesPanels:       12
  - bubbleCrossesPanels:     4
SVG w/ ext clip-path:       12
```

入稿 workflow:
- 印刷所 (B5/A5 製本) → `arc0-1-origin.pdf` (lossless 445 MB)
- web preview / 共有 → `arc0-1-origin-preview.pdf` (38 MB)
- PDF flag: `--jpeg`, `--jpeg-quality N`, `--no-trim-marks`, `--output <path>`

## 今後の進化候補

1. **3D-proxy 連載再評価** — 第 2 話以降で character set が固定化したら、Method 3 を再検討 (initial 3D modeling コストを連載で amortize)
2. **動画生成 (Runway/Sora)** — animatic に展開する場合、key frame として M2+ref 画像を使う
3. **panel-level emphasis-aware lighting** — `gh:tone: ominous` panel に automatic dim lighting prompt
4. **multi-character identity locking** — 3+ char ensemble 時に LoRA / per-char masking を導入
5. **PDF size 削減** — PNG 再圧縮 (mozjpeg / pngquant) で 449 MB → 50-80 MB 目標
6. **SFX hand-drawn brushstroke** — 現状 vector text のみ。`brushPath` 系 SVG path 生成 LLM step を追加

## 参考実装

- `scripts/lg-image-gen/README.md` — pipeline 詳細
- `scripts/lg-image-gen/src/graph-m2.ts` — Method 2 graph
- `scripts/lg-image-gen/src/phase3-4-semantic-panels.ts` — Phase 3.4 LLM decompose
- `scripts/lg-image-gen/src/phase4-typesetting-schema.ts` — Phase 4 schema injector + template auto-assigner
- `scripts/lg-image-gen/src/render-page.ts` — Phase C SVG renderer (vertical text + overflow + font embed)
- `scripts/lg-image-gen/src/sfx-auto.ts` — Phase C LLM SFX placement
- `scripts/lg-image-gen/src/svg-to-png.ts` — sharp rasterize @ 150 dpi
- `scripts/lg-image-gen/src/export-pdf.ts` — pdf-lib + trim marks
- `scripts/lg-image-gen/src/lib/openai.ts` — generate / edit / critique / Q_p / Q_i / combineQ
- `260419-GH-jump.md` — v2 storyboard spec (SSoT)
- `story-outline.jsonld` — v2 script SSoT
