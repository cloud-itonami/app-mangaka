/**
 * Phase 3.4 — Semantic panel decomposition via LLM.
 *
 * Problem: panels were grouped by speaker switches (Phase 3.2), losing scene context.
 * E.g., p1n7 contains only Mei+Saki dialogues but their reactions are TO Akira's sneakers
 * which is in adjacent panels — the panel jsonld doesn't carry that context, so the
 * image gen prompt doesn't know about sneakers.
 *
 * This script uses gpt-4o (or gpt-4o-mini) to re-decompose each page's v2 outline
 * gh:script entries into semantically coherent panels with rich schema:
 *   - gh:sceneSubject       — one-line topic of the panel
 *   - gh:focusCharacter     — who's the visual subject
 *   - gh:allCharacters      — everyone in frame (silent included)
 *   - gh:props              — key objects in scene
 *   - gh:visualDescription  — detailed description of the moment
 *   - gh:dialogues          — speech beats
 *   - gh:precedingBeat      — what just happened (context)
 *   - gh:followingBeat      — what's next (continuity)
 *   - gh:shot               — camera framing recommendation
 *   - gh:scriptEntryIndices — which v2 outline entries this panel covers
 *
 * After regeneration:
 *   - episode.jsonld panels are replaced with rich versions
 *   - existing gh:generatedImages entries are migrated where script-entry overlap is high
 *   - manifest is regenerated to reflect new panel structure
 *
 * Usage:
 *   OPENAI_API_KEY=... npx tsx src/phase3-4-semantic-panels.ts --page 1
 *   OPENAI_API_KEY=... npx tsx src/phase3-4-semantic-panels.ts --all
 */
import * as fs from "node:fs";

const REPO = "/Users/junkawasaki/github/ghosthacker/260123-jump";
const EPISODE_PATH = `${REPO}/resources/episodes/arc0-1-origin/episode.jsonld`;
const OUTLINE_PATH = `${REPO}/resources/episodes/arc0-1-origin/story-outline.jsonld`;
const MANIFEST_PATH = `${REPO}/resources/episodes/arc0-1-origin/image-gen-manifest.json`;

const LLM_MODEL = process.env.LG_DECOMPOSE_MODEL ?? "gpt-4o";

interface CliArgs { pages: number[]; all: boolean }
function parseArgs(): CliArgs {
  const args = process.argv.slice(2);
  const out: CliArgs = { pages: [], all: false };
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--all") out.all = true;
    else if (args[i] === "--page" && args[i + 1]) out.pages.push(Number(args[++i]));
  }
  return out;
}

interface RichPanel {
  panelIndex: number;
  sceneSubject: string;
  focusCharacter: string;
  allCharacters: string[];
  props: string[];
  visualDescription: string;
  dialogues: { speaker: string; text: string; emotion?: string }[];
  precedingBeat: string;
  followingBeat: string;
  shot: string;
  scriptEntryIndices: number[];
  // Visual style classification (drives prompt suffix + reference-work anchoring)
  visualStyle: "cinematic-close" | "anime-action" | "film-medium" | "establishing-illustration";
  tone: "action" | "emotional" | "quiet" | "triumph" | "tense" | "comedic" | "ominous" | "contemplative";
  // Emotion concretization — physical signals per focused character
  emotionPhysicalSignals: { character: string; signals: string[] }[];
  // Page layout fields (manga reading: right-to-left, top-to-bottom)
  layout: {
    row: number;          // 1-based row from top
    colSpan: number;      // 1 = narrow, 2 = wide, 3 = full row
    rowSpan: number;      // 1 = normal, 2 = double-height (spread vertically)
    size: "small" | "medium" | "large" | "spread";
    emphasis: "establish" | "beat" | "impact" | "transition" | "punchline";
    readingOrder: number; // 1-based; manga right-to-left top-to-bottom
  };
}

interface PageLayout {
  templateName: string;       // e.g., "Jump 6-panel asymmetric", "Impact spread (見開き)", "9-panel grid"
  totalRows: number;
  gridDescription: string;
  pageType: "single-page" | "double-page-spread";  // 見開き flag
  spreadWith?: number;        // if double-page-spread, the adjacent page number it spans with
  emotionalPeak: string;      // what the page builds to — informs panel size choices
  notes: string;
}

interface DecompositionResult {
  pageLayout: PageLayout;
  panels: RichPanel[];
}

async function decomposePage(pageNum: number, pageData: any): Promise<DecompositionResult> {
  const apiKey = process.env.OPENAI_API_KEY;
  if (!apiKey) throw new Error("OPENAI_API_KEY not set");
  const script = pageData["gh:script"] ?? [];
  const setting = pageData["gh:setting"] ?? "";
  const visualNote = pageData["gh:visualNote"] ?? "";
  const pov = pageData["gh:pov"] ?? "";
  const title = pageData["gh:pageTitle"] ?? "";

  const sys = `You are a professional manga storyboarder for Weekly Shounen Jump (週刊少年ジャンプ). Each panel must read as a STANDALONE artwork worthy of Naruto / One Piece / Aria / 攻殻機動隊 quality — strong cinematic composition, expressive character signals, atmospheric depth.

ABSOLUTE RULES (highest priority):
1. PARTITION: every script entry index 0..N-1 must appear in EXACTLY ONE panel's scriptEntryIndices. No duplicates. No omissions. The union of all scriptEntryIndices must equal {0, 1, ..., N-1}.
2. COMPRESS: consecutive entries describing the same beat (e.g., 3 entries about a character sleeping) MUST be merged into ONE panel. Do not make 3 panels of the same character sleeping in similar poses.
3. DIVERSE FOCUS: do not bias toward one character. If the script shows Akira revealing sneakers, that gets its own panel WITH AKIRA as focus (not the sleeping character in the background).
4. ACTIVE > PASSIVE: when a panel covers a beat, focus on the character TAKING ACTION (Akira showing sneakers > Ren sleeping at desk).
5. EXPRESSIVE BODY SIGNALS: every focused character must have CONCRETE physical signals (not template emotions). "afraid" → ["dilated pupils", "sweat on forehead", "trembling lips"]. "excited" → ["raised fist", "open mouth wide", "flushed cheeks"]. NO generic emotion words alone.

PAGE LAYOUT (Jump-style):
- Reading: right-to-left, top-to-bottom
- Asymmetric grid; varied panel sizes for pacing
- Sizes: small (col-span 1) / medium (col-span 2) / large (col-span 3 or row-span 2) / spread (full row)
- 見開き (double-page-spread) for high-impact moments: climax, decisive choice, big reveal
- Pacing: small panels build → 1 large/spread releases

PANEL FIELDS:
- ONE focus character (or "shared" for ensemble) — but list ALL characters in frame
- Capture key PROPS (objects that drive the scene)
- visualDescription (2-3 sentences) describing what to DRAW — focus on the active character's action, key props, OTHER characters' positions
- precedingBeat / followingBeat (1 sentence each)
- shot: Wide / Medium / Close Up / Extreme Close Up / Insert / Over the Shoulder / POV
- layout: row (1-based), colSpan (1-3), rowSpan (1-2), size, emphasis (establish/beat/impact/transition/punchline), readingOrder (1-based)
- visualStyle: classify the panel's VISUAL TREATMENT
  - "cinematic-close" — emotional impact, XCU eyes, rim lighting, depth of field (use for impact/punchline emotional beats)
  - "anime-action" — dynamic poses, motion lines, exaggerated foreshortening, speed effects (use for action/movement beats)
  - "film-medium" — composed shot, depth, set dressing, multi-character staging (use for dialogue beats with 2+ chars)
  - "establishing-illustration" — detailed environment, atmospheric, scenic (use for establish beats)
- tone: "action" | "emotional" | "quiet" | "triumph" | "tense" | "comedic" | "ominous" | "contemplative"
- emotionPhysicalSignals: array of {character, signals[]} — concrete physical indicators per focused character
  - Example: [{character: "Yuto", signals: ["dilated pupils", "sweat on forehead", "trembling lips", "phone-glow rim light on face"]}]
  - REQUIRED: every focused/named character must have at least 2 specific signals (not just emotion words)

PAGE-LEVEL:
- pageType: "single-page" (default) or "double-page-spread" (only for climactic moments)
- spreadWith: adjacent page number if 見開き
- emotionalPeak: what the page builds to
- templateName: e.g., "Jump 7-panel build-and-release"

VALIDATION before responding: count entries vs panels' scriptEntryIndices — every index must be covered exactly once.

Respond with VALID JSON: { "pageLayout": <PageLayout>, "panels": [<panel objects>] }`;

  const user = `Page ${pageNum} — "${title}"
Setting: ${setting}
Visual note: ${visualNote}
POV: ${pov}

Script entries (must all be covered):
${script.map((e: any, i: number) => `[${i}] ${e["gh:type"]}${e["gh:speaker"] ? ` (${e["gh:speaker"]})` : ""}: ${(e["gh:text"] ?? e["gh:items"]?.join("; ") ?? "").slice(0, 200)}`).join("\n")}

Output JSON schema:
{
  "pageLayout": {
    "templateName": "<short name>",
    "totalRows": <int>,
    "gridDescription": "<sentence>",
    "pageType": "single-page" | "double-page-spread",
    "spreadWith": <adjacent page number, only if pageType is double-page-spread>,
    "emotionalPeak": "<what this page builds toward>",
    "notes": "<any layout rationale>"
  },
  "panels": [
    {
      "panelIndex": <1-based int>,
      "sceneSubject": "<one-line topic>",
      "focusCharacter": "<character name or 'shared'>",
      "allCharacters": ["<names>"],
      "props": ["<objects>"],
      "visualDescription": "<2-3 sentence vivid description>",
      "dialogues": [{"speaker": "<name>", "text": "<jp text>", "emotion": "<optional>"}],
      "precedingBeat": "<1 sentence>",
      "followingBeat": "<1 sentence>",
      "shot": "<framing>",
      "scriptEntryIndices": [<int>, ...],
      "visualStyle": "cinematic-close" | "anime-action" | "film-medium" | "establishing-illustration",
      "tone": "action" | "emotional" | "quiet" | "triumph" | "tense" | "comedic" | "ominous" | "contemplative",
      "emotionPhysicalSignals": [
        {"character": "<name>", "signals": ["<specific physical signal>", ...]}
      ],
      "layout": {
        "row": <int>,
        "colSpan": <int 1-3>,
        "rowSpan": <int 1-2>,
        "size": "small" | "medium" | "large" | "spread",
        "emphasis": "establish" | "beat" | "impact" | "transition" | "punchline",
        "readingOrder": <int>
      }
    }
  ]
}

Return ONLY the JSON object, no prose.`;

  const r = await fetch("https://api.openai.com/v1/chat/completions", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
    body: JSON.stringify({
      model: LLM_MODEL,
      messages: [
        { role: "system", content: sys },
        { role: "user", content: user },
      ],
      response_format: { type: "json_object" },
      max_tokens: 16000,
    }),
  });
  if (!r.ok) throw new Error(`decompose HTTP ${r.status}: ${(await r.text()).slice(0, 400)}`);
  const j: any = await r.json();
  const txt = j.choices?.[0]?.message?.content ?? "{}";
  let parsed: any;
  try { parsed = JSON.parse(txt); }
  catch { throw new Error(`decompose parse fail: ${txt.slice(0, 200)}`); }
  const pageLayout: PageLayout = parsed.pageLayout ?? {
    templateName: "auto",
    totalRows: 0,
    gridDescription: "",
    pageType: "single-page",
    emotionalPeak: "",
    notes: "",
  };
  const panels: RichPanel[] = Array.isArray(parsed.panels) ? parsed.panels :
    (Array.isArray(parsed) ? parsed : []);
  if (!panels.length) throw new Error(`decompose returned no panels: ${JSON.stringify(parsed).slice(0, 200)}`);

  // Coverage validation
  const allIndices = new Set<number>(script.map((_: any, i: number) => i));
  const covered = new Set<number>();
  const duplicates: number[] = [];
  for (const p of panels) {
    for (const i of p.scriptEntryIndices ?? []) {
      if (covered.has(i)) duplicates.push(i);
      covered.add(i);
    }
  }
  const missing = [...allIndices].filter((i) => !covered.has(i));

  if (missing.length === 0 && duplicates.length === 0) {
    return { pageLayout, panels };
  }

  console.warn(`  ⚠ coverage issues: missing=[${missing.join(",")}], duplicates=[${duplicates.join(",")}]`);
  console.warn(`  → asking LLM to merge/extend with full PARTITION constraint`);

  // Patch call: send the partial result + missing entries + dupe info, ask LLM to FIX (merge dupes, add missing)
  const missingDetail = missing.map((i) => `[${i}] ${script[i]["gh:type"]}${script[i]["gh:speaker"] ? ` (${script[i]["gh:speaker"]})` : ""}: ${(script[i]["gh:text"] ?? "").slice(0, 200)}`).join("\n");
  const patchUser = `${user}\n\nPrevious decomposition (NEEDS FIXING):\n${JSON.stringify({ pageLayout, panels }, null, 2).slice(0, 6000)}\n\nIssues:\n- Missing entries (not covered by any panel, must be added): [${missing.join(", ")}]\n${missingDetail ? "\nDetail:\n" + missingDetail : ""}\n- Duplicated entries (covered by multiple panels, must merge): [${duplicates.join(", ")}]\n\nReturn a CORRECTED full decomposition: same schema, ALL entries 0..${script.length - 1} covered EXACTLY ONCE. Merge duplicates by combining their panels. Insert new panels for missing entries WITHOUT reproducing existing scenes.`;

  const r2 = await fetch("https://api.openai.com/v1/chat/completions", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
    body: JSON.stringify({
      model: LLM_MODEL,
      messages: [
        { role: "system", content: sys },
        { role: "user", content: patchUser },
      ],
      response_format: { type: "json_object" },
      max_tokens: 16000,
    }),
  });
  if (!r2.ok) {
    console.warn(`  patch HTTP ${r2.status} — keeping original`);
    return { pageLayout, panels };
  }
  const j2: any = await r2.json();
  const txt2 = j2.choices?.[0]?.message?.content ?? "{}";
  try {
    const p2 = JSON.parse(txt2);
    const panels2: RichPanel[] = Array.isArray(p2.panels) ? p2.panels : [];
    const layout2: PageLayout = p2.pageLayout ?? pageLayout;
    if (panels2.length > 0) {
      // Re-validate the patch
      const cov2 = new Set<number>();
      const dup2: number[] = [];
      for (const p of panels2) for (const i of p.scriptEntryIndices ?? []) {
        if (cov2.has(i)) dup2.push(i);
        cov2.add(i);
      }
      const miss2 = [...allIndices].filter((i) => !cov2.has(i));
      console.warn(`  ↳ patched: ${panels2.length} panels, missing=${miss2.length}, dups=${dup2.length}`);
      return { pageLayout: layout2, panels: panels2 };
    }
  } catch {
    console.warn(`  patch parse failed`);
  }
  return { pageLayout, panels };
}

function buildPanelJsonld(rich: RichPanel, pageNum: number): any {
  const cleanChar = (c: string) => c.startsWith("character:") ? c : `character:${c}`;
  return {
    "@id": `panel:p${pageNum}n${rich.panelIndex}-v3`,
    "characters": rich.allCharacters.map(cleanChar),
    "dialogue": rich.dialogues,
    "panel": rich.panelIndex,
    "shot": rich.shot,
    "visual": rich.visualDescription,
    "gh:panelIndex": rich.panelIndex,
    "gh:sceneSubject": rich.sceneSubject,
    "gh:focusCharacter": rich.focusCharacter,
    "gh:allCharacters": rich.allCharacters,
    "gh:focusedCharacters": [rich.focusCharacter].filter((c) => c !== "shared"),
    "gh:props": rich.props,
    "gh:visualDescription": rich.visualDescription,
    "gh:precedingBeat": rich.precedingBeat,
    "gh:followingBeat": rich.followingBeat,
    "gh:scriptEntryIndices": rich.scriptEntryIndices,
    "gh:panelLayout": rich.layout ? {
      "gh:row": rich.layout.row,
      "gh:colSpan": rich.layout.colSpan,
      "gh:rowSpan": rich.layout.rowSpan,
      "gh:size": rich.layout.size,
      "gh:emphasis": rich.layout.emphasis,
      "gh:readingOrder": rich.layout.readingOrder,
    } : undefined,
    "gh:visualStyle": rich.visualStyle,
    "gh:tone": rich.tone,
    "gh:emotionPhysicalSignals": rich.emotionPhysicalSignals,
    "gh:inserted": true,
    "gh:insertedRevision": "phase3.4-semantic-decompose-v3",
    "gh:insertedSource": "story-outline.jsonld + LLM decomposition (Jump-style + 見開き-aware)",
    "gh:needsImageGeneration": true,
  };
}

async function main() {
  const cli = parseArgs();
  const ep = JSON.parse(fs.readFileSync(EPISODE_PATH, "utf-8"));
  const outline = JSON.parse(fs.readFileSync(OUTLINE_PATH, "utf-8"));
  const targetPages = cli.all ? outline["gh:pages"].map((p: any) => p["gh:pageNumber"]) : cli.pages;
  if (targetPages.length === 0) {
    console.error("No pages specified. Use --page N or --all");
    process.exit(1);
  }

  console.log(`Decomposing ${targetPages.length} page(s) with ${LLM_MODEL}`);

  for (const pn of targetPages) {
    const v2Page = outline["gh:pages"].find((p: any) => p["gh:pageNumber"] === pn);
    if (!v2Page) { console.warn(`v2 page ${pn} missing`); continue; }
    const epPage = ep["gh:pages"].find((p: any) => p["gh:pageNumber"] === pn);
    if (!epPage) { console.warn(`episode page ${pn} missing`); continue; }
    console.log(`\n=== Page ${pn} (${v2Page["gh:pageTitle"]}) ===`);
    try {
      const result = await decomposePage(pn, v2Page);
      const { pageLayout, panels: richPanels } = result;
      console.log(`  pageLayout: ${pageLayout.templateName} | ${pageLayout.pageType}${pageLayout.spreadWith ? ` (with p${pageLayout.spreadWith})` : ""} | rows=${pageLayout.totalRows}`);
      console.log(`  emotional peak: ${pageLayout.emotionalPeak}`);
      console.log(`  decomposed → ${richPanels.length} panel(s)`);
      for (const rp of richPanels) {
        const lo = rp.layout;
        const layoutTag = lo ? `[r${lo.row} c${lo.colSpan}×${lo.rowSpan} ${lo.size}/${lo.emphasis}]` : "";
        console.log(`    n${rp.panelIndex} ${layoutTag} (${rp.shot}, focus=${rp.focusCharacter}, props=${rp.props.join(", ")}): ${rp.visualDescription.slice(0, 80)}`);
      }

      // Apply page-level layout
      epPage["gh:pageLayoutV3"] = {
        "gh:templateName": pageLayout.templateName,
        "gh:totalRows": pageLayout.totalRows,
        "gh:gridDescription": pageLayout.gridDescription,
        "gh:pageType": pageLayout.pageType,
        ...(pageLayout.spreadWith !== undefined ? { "gh:spreadWith": pageLayout.spreadWith } : {}),
        "gh:emotionalPeak": pageLayout.emotionalPeak,
        "gh:notes": pageLayout.notes,
      };

      // Migrate existing image generation history
      const oldPanels = epPage["gh:panels"] ?? [];
      const newPanels = richPanels.map((rp) => {
        const newPanel = buildPanelJsonld(rp, pn);
        // Match to old panel by script-entry overlap
        const best = oldPanels.find((op: any) => {
          const oldEntries = op["gh:scriptEntries"]?.map((e: any) => e["gh:type"] + ":" + (e["gh:text"] ?? "").slice(0, 30)) ?? [];
          // overlap = any old script-entry text appears within rp's covered indices
          const rpTexts = rp.scriptEntryIndices.map((i) => v2Page["gh:script"][i]).filter(Boolean).map((e: any) => e["gh:type"] + ":" + (e["gh:text"] ?? "").slice(0, 30));
          return oldEntries.some((t: string) => rpTexts.includes(t));
        });
        if (best) {
          // Preserve image history
          if (best["gh:generatedImages"]) {
            newPanel["gh:generatedImages"] = best["gh:generatedImages"];
            newPanel["gh:currentImageIndex"] = best["gh:currentImageIndex"];
            newPanel["gh:generatedImageUrl"] = best["gh:generatedImageUrl"];
            // Don't mark as needsImageGeneration if we have a usable image
            // BUT visualDescription is now different, so mark for regen
            newPanel["gh:needsImageGeneration"] = true;
            newPanel["gh:migratedFromPanel"] = best["@id"];
          }
        }
        return newPanel;
      });

      // Stash deprecated panels
      const deprecated = oldPanels.filter((op: any) =>
        !newPanels.some((np: any) => np["gh:migratedFromPanel"] === op["@id"])
      );

      epPage["gh:panels"] = newPanels;
      epPage["gh:panelCount"] = newPanels.length;
      if (deprecated.length > 0) {
        epPage["gh:deprecatedPanelsP34"] = (epPage["gh:deprecatedPanelsP34"] ?? []).concat(deprecated);
      }
      epPage["gh:phase3.4Migration"] = {
        decomposedAt: new Date().toISOString(),
        model: LLM_MODEL,
        oldPanelCount: oldPanels.length,
        newPanelCount: newPanels.length,
      };
    } catch (e) {
      console.error(`  page ${pn} FAILED: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  fs.writeFileSync(EPISODE_PATH, JSON.stringify(ep, null, 2) + "\n");

  // Regenerate manifest
  console.log(`\nRegenerating manifest...`);
  const manifestPanels: any[] = [];
  for (const page of ep["gh:pages"]) {
    for (const panel of page["gh:panels"] ?? []) {
      if (!panel["gh:needsImageGeneration"]) continue;
      const safeId = panel["@id"].replace(/[^a-zA-Z0-9._-]/g, "_");
      const outputDir = `${REPO}/resources/images/episodes/episode:arc0-1-origin/pages/${page["gh:pageNumber"]}`;
      const outputFile = `${outputDir}/panel_${safeId}_v1.png`;
      manifestPanels.push({
        pageNum: page["gh:pageNumber"],
        panelId: panel["@id"],
        panelIndex: panel["gh:panelIndex"],
        pageTitle: page["gh:pageTitle"],
        shot: panel["shot"],
        visual: panel["gh:visualDescription"] ?? panel["visual"],
        sceneSubject: panel["gh:sceneSubject"],
        focusCharacter: panel["gh:focusCharacter"],
        allCharacters: panel["gh:allCharacters"] ?? [],
        focusedCharacters: panel["gh:focusedCharacters"] ?? [],
        props: panel["gh:props"] ?? [],
        precedingBeat: panel["gh:precedingBeat"],
        followingBeat: panel["gh:followingBeat"],
        visualStyle: panel["gh:visualStyle"] ?? "film-medium",
        tone: panel["gh:tone"] ?? "quiet",
        emotionPhysicalSignals: panel["gh:emotionPhysicalSignals"] ?? [],
        panelLayout: panel["gh:panelLayout"] ?? null,
        characters: panel["gh:allCharacters"] ?? panel["characters"]?.map((c: string) => c.replace("character:", "")) ?? [],
        dialogues: panel["dialogue"] ?? [],
        prompt: "",
        outputPath: outputFile,
        outputDir,
        referenceCharacters: panel["gh:focusedCharacters"] ?? panel["gh:allCharacters"] ?? [],
        referenceSelections: [],
      });
    }
  }
  const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf-8"));
  manifest.panels = manifestPanels;
  manifest.totalPanels = manifestPanels.length;
  manifest.regeneratedAt = new Date().toISOString();
  manifest.schema = "phase3.4-rich";
  fs.writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2) + "\n");
  console.log(`Manifest updated: ${manifestPanels.length} panels need image generation.`);
}

main().catch((e) => { console.error(e); process.exit(1); });
