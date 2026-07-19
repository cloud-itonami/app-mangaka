/**
 * Panel overflow auto-population via LLM.
 *
 * For each eligible panel, ask gpt-4o to propose 0-1 overflow effect from:
 *   - characterBreaksFrame      → focus char punches/leaps beyond panel border
 *   - bubbleCrossesPanels       → dialogue spans current + adjacent panel
 *   - sfxCrossesPanels          → existing SFX glyph crosses panel boundary
 *   - floatingPanelOnPage       → small inset panel with drop shadow
 *
 * Eligibility:
 *   tone ∈ {action, triumph, tense, ominous}
 *   OR emphasis ∈ {impact, punchline, focal}
 *   OR has SFX entry with size ∈ {L, XL, spread}
 *
 * Conservative: LLM is told to return null for >70% of panels — overflow is
 * a spotlight effect that loses impact if over-applied.
 *
 * Renderer field map (src/render-page.ts):
 *   gh:characterBreaksFrame: { gh:extensionMm: number, gh:extensionDirection: "top"|"bottom"|"left"|"right" }
 *   gh:bubbleCrossesPanels:  string[]  (non-empty → bubbles render in overflow z-layer)
 *   gh:sfxCrossesPanels:     string[]  (non-empty → sfx render in overflow z-layer)
 *   gh:floatingPanelOnPage:  { gh:withShadow: boolean }
 *
 * Usage:
 *   OPENAI_API_KEY=... npx tsx src/overflow-auto.ts
 *   OPENAI_API_KEY=... npx tsx src/overflow-auto.ts --page 35
 *   OPENAI_API_KEY=... npx tsx src/overflow-auto.ts --force   # overwrite existing
 */
import * as fs from "node:fs";

const REPO = "/Users/junkawasaki/github/ghosthacker/260123-jump";
const EPISODE_PATH = `${REPO}/resources/episodes/arc0-1-origin/episode.jsonld`;
const MODEL = process.env.LG_OVERFLOW_MODEL ?? "gpt-4o";
const CHAT_URL = "https://api.openai.com/v1/chat/completions";

interface CliArgs { pageNum?: number; force: boolean }
function parseArgs(): CliArgs {
  const a = process.argv.slice(2);
  const o: CliArgs = { force: false };
  for (let i = 0; i < a.length; i++) {
    if (a[i] === "--page" && a[i+1]) o.pageNum = Number(a[++i]);
    else if (a[i] === "--force") o.force = true;
  }
  return o;
}

const ELIGIBLE_TONES = new Set(["action", "triumph", "tense", "ominous"]);
const ELIGIBLE_EMPHASES = new Set(["impact", "punchline", "focal"]);
const BIG_SFX_SIZES = new Set(["L", "XL", "spread"]);

function isEligible(panel: any): boolean {
  const tone = (panel["gh:tone"] ?? "").toLowerCase();
  const emphasis = (panel["gh:panelLayout"]?.["gh:emphasis"] ?? "").toLowerCase();
  if (ELIGIBLE_TONES.has(tone)) return true;
  if (ELIGIBLE_EMPHASES.has(emphasis)) return true;
  const sfx = panel["gh:sfx"] ?? [];
  if (sfx.some((s: any) => BIG_SFX_SIZES.has(s["gh:size"]))) return true;
  return false;
}

function hasActiveOverflow(panel: any): boolean {
  const ov = panel["gh:panelOverflow"] ?? {};
  return !!(
    ov["gh:characterBreaksFrame"] ||
    (Array.isArray(ov["gh:bubbleCrossesPanels"]) && ov["gh:bubbleCrossesPanels"].length) ||
    (Array.isArray(ov["gh:sfxCrossesPanels"]) && ov["gh:sfxCrossesPanels"].length) ||
    ov["gh:floatingPanelOnPage"]
  );
}

async function generateOverflow(panel: any, page: any, neighborIds: string[]): Promise<any | null> {
  const apiKey = process.env.OPENAI_API_KEY;
  if (!apiKey) throw new Error("OPENAI_API_KEY not set");
  const sys = `You are a manga panel layout director for Weekly Shounen Jump. Decide whether this panel benefits from a "コマからはみ出す" (overflow) effect, where image/bubble/SFX crosses the panel border for dramatic emphasis.

Critical rules:
- Most panels need NO overflow. Return {"effect": null} for ~70% of cases.
- Pick AT MOST ONE effect type. Overflow is a spotlight; overuse kills it.
- Only choose an effect when it directly amplifies the panel's emotional peak (impact, reveal, punchline).

Effect options:
1. "characterBreaksFrame" — focus character punches/leaps/extends beyond the panel border (action/triumph peak, hero shot).
   Output: {"effect":"characterBreaksFrame", "gh:extensionMm": <4..18>, "gh:extensionDirection": "top|bottom|left|right"}
2. "bubbleCrossesPanels" — dialogue or shout spills into adjacent panel (tense conversation continuation).
   Output: {"effect":"bubbleCrossesPanels", "gh:targets": [<neighbor @id>]}
3. "sfxCrossesPanels" — large SFX glyph crosses panel boundary (huge impact, only if existing SFX is L/XL/spread).
   Output: {"effect":"sfxCrossesPanels", "gh:targets": [<neighbor @id>]}
4. "floatingPanelOnPage" — small inset panel with drop shadow floating over the page (close-up / cut-in moment).
   Output: {"effect":"floatingPanelOnPage", "gh:withShadow": true}

Respond ONLY with valid JSON.`;
  const sfxSummary = (panel["gh:sfx"] ?? []).map((s: any) => `${s["gh:text"]}(${s["gh:size"]})`).join(", ") || "none";
  const user = `Panel context:
- @id: ${panel["@id"]}
- shot: ${panel["shot"]}
- tone: ${panel["gh:tone"]}
- emphasis: ${panel["gh:panelLayout"]?.["gh:emphasis"]}
- focusCharacter: ${panel["gh:focusCharacter"]}
- sceneSubject: ${panel["gh:sceneSubject"]}
- existing SFX: ${sfxSummary}
- bubbles: ${(panel["gh:bubbles"] ?? []).length}
- visualDescription: ${(panel["gh:visualDescription"] ?? "").slice(0, 250)}
- page emotional peak: ${page["gh:pageLayoutV3"]?.["gh:emotionalPeak"] ?? "—"}
- adjacent panel @ids on same page: ${neighborIds.join(", ") || "(none)"}

Return JSON: {"effect": <null|"characterBreaksFrame"|"bubbleCrossesPanels"|"sfxCrossesPanels"|"floatingPanelOnPage">, ...effect-specific fields, "rationale": "<1 sentence>"}`;

  const r = await fetch(CHAT_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
    body: JSON.stringify({
      model: MODEL,
      messages: [
        { role: "system", content: sys },
        { role: "user", content: user },
      ],
      response_format: { type: "json_object" },
      max_completion_tokens: 400,
    }),
  });
  if (!r.ok) {
    console.warn(`  ${panel["@id"]}: HTTP ${r.status}`);
    return null;
  }
  const j: any = await r.json();
  const txt = j.choices?.[0]?.message?.content ?? "{}";
  try { return JSON.parse(txt); } catch { return null; }
}

function applyToPanel(panel: any, decision: any): boolean {
  if (!decision || !decision.effect) return false;
  const ov = panel["gh:panelOverflow"] ?? {};
  switch (decision.effect) {
    case "characterBreaksFrame":
      ov["gh:characterBreaksFrame"] = {
        "gh:extensionMm": Number(decision["gh:extensionMm"] ?? 8),
        "gh:extensionDirection": decision["gh:extensionDirection"] ?? "top",
        "gh:autoGenerated": true,
      };
      break;
    case "bubbleCrossesPanels":
      ov["gh:bubbleCrossesPanels"] = Array.isArray(decision["gh:targets"]) ? decision["gh:targets"] : [];
      break;
    case "sfxCrossesPanels":
      ov["gh:sfxCrossesPanels"] = Array.isArray(decision["gh:targets"]) ? decision["gh:targets"] : [];
      break;
    case "floatingPanelOnPage":
      ov["gh:floatingPanelOnPage"] = {
        "gh:withShadow": decision["gh:withShadow"] !== false,
        "gh:autoGenerated": true,
      };
      break;
    default: return false;
  }
  ov["gh:autoGeneratedAt"] = new Date().toISOString();
  ov["gh:autoModel"] = MODEL;
  ov["gh:autoRationale"] = decision.rationale ?? "";
  panel["gh:panelOverflow"] = ov;
  return true;
}

async function main() {
  const cli = parseArgs();
  if (!process.env.OPENAI_API_KEY) { console.error("OPENAI_API_KEY not set"); process.exit(1); }
  const ep = JSON.parse(fs.readFileSync(EPISODE_PATH, "utf-8"));

  const targets: { page: any; panel: any; neighbors: string[] }[] = [];
  for (const page of ep["gh:pages"] ?? []) {
    if (cli.pageNum !== undefined && page["gh:pageNumber"] !== cli.pageNum) continue;
    const panels = page["gh:panels"] ?? [];
    const allIds: string[] = panels.map((p: any) => p["@id"]);
    panels.forEach((panel: any, idx: number) => {
      if (!isEligible(panel)) return;
      if (!cli.force && hasActiveOverflow(panel)) return;
      const neighbors = [allIds[idx - 1], allIds[idx + 1]].filter(Boolean) as string[];
      targets.push({ page, panel, neighbors });
    });
  }
  console.log(`Overflow auto: ${targets.length} eligible panel(s)\n`);

  let ok = 0, fail = 0, applied = 0;
  const byEffect: Record<string, number> = {};
  for (let i = 0; i < targets.length; i++) {
    const { page, panel, neighbors } = targets[i];
    process.stdout.write(`[${i+1}/${targets.length}] p${page["gh:pageNumber"]} ${panel["@id"]} (${panel["gh:tone"]}/${panel["gh:panelLayout"]?.["gh:emphasis"]}) ... `);
    const decision = await generateOverflow(panel, page, neighbors);
    if (decision === null) { console.log("FAIL"); fail++; continue; }
    if (!decision.effect) { console.log("none"); ok++; continue; }
    if (applyToPanel(panel, decision)) {
      applied++;
      byEffect[decision.effect] = (byEffect[decision.effect] ?? 0) + 1;
      console.log(`+${decision.effect}`);
    } else {
      console.log("invalid");
      fail++;
    }
    ok++;
    if (i < targets.length - 1) await new Promise((r) => setTimeout(r, 600));
  }

  fs.writeFileSync(EPISODE_PATH, JSON.stringify(ep, null, 2) + "\n");
  console.log(`\nDone: ${ok}/${targets.length} OK, ${fail} fail, ${applied} overflow effects applied`);
  console.log("By effect:", byEffect);
}

main().catch((e) => { console.error(e); process.exit(1); });
