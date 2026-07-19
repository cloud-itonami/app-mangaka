/**
 * Phase 4 — Typesetting schema extension.
 *
 * Augments episode.jsonld with:
 *   - episode-level: gh:manuscriptFrame (Jump A4 spec)
 *   - page-level:    gh:pageTemplate (ref to a template in page-templates.jsonld)
 *                    gh:templateSelectionRationale
 *   - panel-level:   gh:bubble (size/position/style with auto defaults)
 *                    gh:sfx (empty array, ready for population)
 *                    gh:panelOverflow (defaults)
 *
 * Template selection: heuristic based on page's emotional peak + panel-emphasis profile.
 *
 * Usage:
 *   npx tsx src/phase4-typesetting-schema.ts            # all pages
 *   npx tsx src/phase4-typesetting-schema.ts --page 39  # single page
 */
import * as fs from "node:fs";

const REPO = "/Users/junkawasaki/github/ghosthacker/260123-jump";
const EPISODE_PATH = `${REPO}/resources/episodes/arc0-1-origin/episode.jsonld`;
const TEMPLATES_PATH = `${REPO}/resources/episodes/arc0-1-origin/page-templates.jsonld`;

interface CliArgs { pages?: number[]; all: boolean }
function parseArgs(): CliArgs {
  const a = process.argv.slice(2);
  const o: CliArgs = { all: true };
  for (let i = 0; i < a.length; i++) {
    if (a[i] === "--page" && a[i+1]) {
      o.pages = o.pages ?? [];
      o.pages.push(Number(a[++i]));
      o.all = false;
    }
  }
  return o;
}

const MANUSCRIPT_FRAME = {
  "gh:format": "weekly-shounen-jump-A4",
  "gh:units": "mm",
  "gh:trim":  {"width": 210, "height": 297},
  "gh:bleed": 3,
  "gh:innerFrame": {"x": 15, "y": 15, "width": 180, "height": 270},
  "gh:gutter": {"horizontalMm": 3, "verticalMm": 3},
  "gh:pageNumberArea": {"corner": "outer-bottom", "xMm": 175, "yMm": 277},
  "gh:readingDirection": "right-to-left, top-to-bottom"
};

function defaultBubble(): any {
  return {
    "gh:sizeMode": "auto",
    "gh:widthMm": null,
    "gh:heightMm": null,
    "gh:position": {"xMm": null, "yMm": null, "relativeToPanel": true},
    "gh:tail": {"direction": "auto", "lengthMm": 6},
    "gh:style": "round",
    "gh:fontSize": "M",
    "gh:overflowPolicy": "shrink-text",
    "gh:maxWidthFraction": 0.5,
    "gh:maxHeightFraction": 0.4
  };
}

function defaultPanelOverflow(): any {
  return {
    "gh:characterBreaksFrame": null,
    "gh:bubbleCrossesPanels": [],
    "gh:sfxCrossesPanels": [],
    "gh:backgroundContinuity": false,
    "gh:floatingPanelOnPage": null
  };
}

interface Template {
  "@id": string;
  "gh:name": string;
  "gh:panelCount": number;
  "gh:emotionalProfile"?: string[];
  "gh:psychologicalEffect"?: string;
  "gh:pageSpan"?: number;
  "gh:diagonalAngleDeg"?: number;
  "gh:panels": any[];
}

function selectTemplate(page: any, templates: Template[]): { templateId: string; rationale: string } {
  const panels = page["gh:panels"] ?? [];
  const panelCount = panels.length;
  const isSpread = page["gh:pageLayoutV3"]?.["gh:pageType"] === "double-page-spread";
  const peak = (page["gh:pageLayoutV3"]?.["gh:emotionalPeak"] ?? "").toLowerCase();
  const tones = panels.map((p: any) => (p["gh:tone"] ?? "").toLowerCase());
  const emphases = panels.map((p: any) => p["gh:panelLayout"]?.["gh:emphasis"] ?? "").map((s: string) => s.toLowerCase());
  const hasImpact = emphases.includes("impact") || emphases.includes("punchline");
  const dominantTone = tones.reduce<Record<string, number>>((a, t) => (a[t] = (a[t] ?? 0) + 1, a), {});
  const topTone = Object.entries(dominantTone).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "";

  // 1. Double-page-spread → use double-page-spread template
  if (isSpread) {
    return { templateId: "tpl:double-page-spread", rationale: `page is marked double-page-spread; full bleed for ${peak}` };
  }

  // 2. Single-panel impact pages
  if (panelCount === 1) {
    return { templateId: "tpl:impact-spread-1", rationale: `single panel page (${peak})` };
  }

  // 3. Pretitle title pages
  if (page["gh:pageTitle"]?.includes("プレタイトル") || page["gh:pageTitle"]?.includes("タイトル")) {
    return { templateId: "tpl:title-splash-3", rationale: "title/pretitle page → splash" };
  }

  // 4. Action / battle pages — diagonal layouts
  if (["action", "tense"].includes(topTone) && hasImpact && panelCount >= 3 && panelCount <= 4) {
    if (panelCount === 4) return { templateId: "tpl:diagonal-x-cross", rationale: `action+impact 4-panel → diagonal-x-cross (collision)` };
    if (panelCount === 3) return { templateId: "tpl:diagonal-3-cascade", rationale: `action+impact 3-panel → diagonal-3-cascade (motion)` };
  }
  if (["action"].includes(topTone) && panelCount === 7) {
    return { templateId: "tpl:vortex-impact-7", rationale: "action 7-panel → vortex-impact (centre+fragments)" };
  }

  // 5. Anxiety / ominous → inverse diagonal
  if (["ominous", "tense"].includes(topTone) && panelCount === 3) {
    return { templateId: "tpl:inverse-diagonal-anxiety", rationale: `ominous/tense 3-panel → inverse diagonal (unease)` };
  }

  // 6. Contemplative / emotional / flashback
  if (["contemplative", "emotional"].includes(topTone) && panelCount >= 5 && panelCount <= 8) {
    return { templateId: "tpl:flashback-blur-7", rationale: `contemplative/emotional ${panelCount}-panel → flashback-blur (soft-edge)` };
  }

  // 7. Camera-pan effect for shutter-paced pages
  if (panelCount === 5 && emphases.filter((e: string) => e === "beat").length >= 3) {
    return { templateId: "tpl:shutter-pan-5", rationale: "5-panel beat-heavy → shutter-pan" };
  }

  // 8. Reveal pages
  if (panelCount === 2 && hasImpact) {
    return { templateId: "tpl:reveal-spread-2", rationale: "2-panel with impact → reveal-spread" };
  }

  // 9. Dense countermeasure / educational
  if (page["gh:pageTitle"]?.includes("Step") || page["gh:pageTitle"]?.includes("対策") || panelCount === 9) {
    return { templateId: "tpl:jump-9-grid", rationale: "countermeasure/educational dense → 9-grid" };
  }

  // 10. Standard pacing by panel count
  if (panelCount === 4) return { templateId: "tpl:standard-grid-4", rationale: "4-panel calm → standard 2x2 grid" };
  if (panelCount === 5) return { templateId: "tpl:jump-build-release-5", rationale: "5-panel → Jump build-and-release" };
  if (panelCount === 6) return { templateId: "tpl:jump-build-release-6", rationale: "6-panel → Jump build-and-release" };
  if (panelCount === 7) return { templateId: "tpl:jump-7-asymmetric", rationale: "7-panel → Jump asymmetric" };

  // Fallback for unusual counts
  if (panelCount <= 4) return { templateId: "tpl:dialogue-cascade-4", rationale: `fallback (${panelCount}-panel) → dialogue-cascade` };
  return { templateId: "tpl:jump-7-asymmetric", rationale: `fallback (${panelCount}-panel) → asymmetric` };
}

async function main() {
  const cli = parseArgs();
  const ep = JSON.parse(fs.readFileSync(EPISODE_PATH, "utf-8"));
  const tplFile = JSON.parse(fs.readFileSync(TEMPLATES_PATH, "utf-8"));
  const templates: Template[] = tplFile["gh:templates"];
  const tplById = new Map<string, Template>();
  for (const t of templates) tplById.set(t["@id"], t);

  // Episode-level manuscriptFrame
  ep["gh:manuscriptFrame"] = MANUSCRIPT_FRAME;
  ep["gh:pageTemplatesRef"] = tplFile["@id"];

  let processed = 0, panelsProcessed = 0;
  for (const page of ep["gh:pages"]) {
    if (!cli.all && cli.pages && !cli.pages.includes(page["gh:pageNumber"])) continue;

    const sel = selectTemplate(page, templates);
    const tpl = tplById.get(sel.templateId)!;
    page["gh:pageTemplate"] = {
      "gh:ref": sel.templateId,
      "gh:name": tpl["gh:name"],
      "gh:panelCount": tpl["gh:panelCount"],
      "gh:diagonalAngleDeg": tpl["gh:diagonalAngleDeg"] ?? null,
      "gh:psychologicalEffect": tpl["gh:psychologicalEffect"] ?? null,
      "gh:rationale": sel.rationale,
      "gh:assignedAt": new Date().toISOString()
    };
    processed++;

    // Map panel slots → episode panels by reading order
    const panels = page["gh:panels"] ?? [];
    panels.forEach((panel: any, idx: number) => {
      const slot = tpl["gh:panels"][idx]; // map by index (reading order)
      if (slot) {
        panel["gh:panelSlot"] = {
          "gh:ref": tpl["@id"] + "/slot/" + slot.slot,
          "gh:slot": slot.slot,
          "gh:shape": slot.shape,
          "gh:bounds": slot.bounds ?? null,
          "gh:vertices": slot.vertices ?? null,
          "gh:skewDeg": slot.skewDeg ?? null,
          "gh:rotationDeg": slot.rotationDeg ?? null,
          "gh:emphasis": slot.emphasis,
          "gh:readingOrder": slot.readingOrder,
          "gh:fullBleed": slot.fullBleed ?? false,
          "gh:withWhiteMargin": slot.withWhiteMargin ?? false,
          "gh:diagonalEdge": slot.diagonalEdge ?? null,
          "gh:centerImpact": slot.centerImpact ?? false
        };
      }

      // Bubble defaults per dialogue
      const dlgs = panel["dialogue"] ?? [];
      panel["gh:bubbles"] = dlgs.map((d: any, i: number) => ({
        ...defaultBubble(),
        "gh:bubbleIndex": i,
        "gh:speaker": d.speaker,
        "gh:text": d.text,
        "gh:style": d.emotion?.toLowerCase().includes("shout") ? "jagged"
                  : d.emotion?.toLowerCase().includes("thought") || d.emotion?.toLowerCase().includes("monologue") ? "thought"
                  : "round"
      }));

      // SFX array (empty, ready for manual or LLM population)
      if (!panel["gh:sfx"]) panel["gh:sfx"] = [];

      // Panel overflow defaults
      if (!panel["gh:panelOverflow"]) panel["gh:panelOverflow"] = defaultPanelOverflow();

      panelsProcessed++;
    });
  }

  ep["gh:phase4Migration"] = {
    "gh:appliedAt": new Date().toISOString(),
    "gh:operations": [
      "added gh:manuscriptFrame (Jump A4 weekly spec) at episode level",
      "assigned gh:pageTemplate from page-templates.jsonld to each page (heuristic by emotional peak + emphasis profile)",
      "added gh:panelSlot to each panel (mapping to template slot)",
      "added gh:bubbles default per dialogue (style auto-derived from emotion)",
      "added gh:sfx empty array per panel",
      "added gh:panelOverflow defaults per panel"
    ],
    "gh:pagesProcessed": processed,
    "gh:panelsProcessed": panelsProcessed
  };

  fs.writeFileSync(EPISODE_PATH, JSON.stringify(ep, null, 2) + "\n");
  console.log(`Phase 4 schema applied: ${processed} pages, ${panelsProcessed} panels`);

  // Distribution
  const tplDist: Record<string, number> = {};
  for (const page of ep["gh:pages"]) {
    const id = page["gh:pageTemplate"]?.["gh:ref"];
    if (id) tplDist[id] = (tplDist[id] ?? 0) + 1;
  }
  console.log(`\nTemplate distribution:`);
  for (const [id, c] of Object.entries(tplDist).sort((a, b) => b[1] - a[1])) {
    console.log(`  ${c.toString().padStart(3)} × ${id}`);
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
