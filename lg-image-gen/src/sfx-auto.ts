/**
 * SFX (擬音) auto-positioning via LLM.
 *
 * For each panel with tone in [action, triumph, tense, impact] OR emphasis = impact/punchline,
 * ask gpt-5/gpt-4o to propose 0-2 SFX entries (text, font, size, position, rotation, effect).
 *
 * Usage:
 *   OPENAI_API_KEY=... npx tsx src/sfx-auto.ts            # all eligible panels
 *   OPENAI_API_KEY=... npx tsx src/sfx-auto.ts --page 35  # single page
 */
import * as fs from "node:fs";

const REPO = "/Users/junkawasaki/github/ghosthacker/260123-jump";
const EPISODE_PATH = `${REPO}/resources/episodes/arc0-1-origin/episode.jsonld`;
const MODEL = process.env.LG_SFX_MODEL ?? "gpt-4o";
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
const ELIGIBLE_EMPHASES = new Set(["impact", "punchline"]);

function isEligible(panel: any): boolean {
  const tone = (panel["gh:tone"] ?? "").toLowerCase();
  const emphasis = (panel["gh:panelLayout"]?.["gh:emphasis"] ?? "").toLowerCase();
  return ELIGIBLE_TONES.has(tone) || ELIGIBLE_EMPHASES.has(emphasis);
}

async function generateSfx(panel: any, page: any): Promise<any[] | null> {
  const apiKey = process.env.OPENAI_API_KEY;
  if (!apiKey) throw new Error("OPENAI_API_KEY not set");
  const sys = `You are a manga SFX (擬音/onomatopoeia) editor for Weekly Shounen Jump. For the given panel, propose 0-2 short Japanese SFX overlays.

Rules:
- Only add SFX where it strengthens the panel impact (action, tense moment, sudden change). For quiet panels return [].
- Each SFX is short (1-6 katakana/hiragana characters): ドッ, バサッ, シーン, ピシッ, ザワ, etc.
- size: S/M/L/XL/spread (M is typical, XL/spread for big impact)
- font: impact (bold gothic) / brush (mincho stroke) / hand-drawn (soft) / rough (jagged)
- position: relative xMm/yMm within the panel bounds (panel size in mm)
- rotation: -30..30 deg
- effect: speed-lines / burst / shadow / halo / none

Respond ONLY with valid JSON: {"sfx": [<entries>]}`;
  const bounds = panel["gh:panelSlot"]?.["gh:bounds"];
  const user = `Panel context:
- @id: ${panel["@id"]}
- sceneSubject: ${panel["gh:sceneSubject"]}
- focusCharacter: ${panel["gh:focusCharacter"]}
- props: ${(panel["gh:props"] ?? []).join(", ")}
- shot: ${panel["shot"]}
- visualStyle: ${panel["gh:visualStyle"]}
- tone: ${panel["gh:tone"]}
- emphasis: ${panel["gh:panelLayout"]?.["gh:emphasis"]}
- panel bounds (mm): w=${bounds?.wMm}, h=${bounds?.hMm}
- visual: ${panel["gh:visualDescription"]?.slice(0, 250)}

Return JSON: {
  "sfx": [
    {
      "gh:text": "<japanese SFX>",
      "gh:font": "impact|brush|hand-drawn|rough",
      "gh:size": "S|M|L|XL|spread",
      "gh:position": {"xMm": <0..${bounds?.wMm ?? 60}>, "yMm": <0..${bounds?.hMm ?? 60}>},
      "gh:rotation": <int -30..30>,
      "gh:skew": 0,
      "gh:effect": "speed-lines|burst|shadow|halo|none",
      "gh:rationale": "<1 sentence>"
    }
  ]
}`;
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
      max_completion_tokens: 800,
    }),
  });
  if (!r.ok) {
    console.warn(`  ${panel["@id"]}: HTTP ${r.status}`);
    return null;
  }
  const j: any = await r.json();
  const txt = j.choices?.[0]?.message?.content ?? "{}";
  try {
    const parsed = JSON.parse(txt);
    return Array.isArray(parsed.sfx) ? parsed.sfx : [];
  } catch {
    return null;
  }
}

async function main() {
  const cli = parseArgs();
  if (!process.env.OPENAI_API_KEY) { console.error("OPENAI_API_KEY not set"); process.exit(1); }
  const ep = JSON.parse(fs.readFileSync(EPISODE_PATH, "utf-8"));

  const targets: { page: any; panel: any }[] = [];
  for (const page of ep["gh:pages"] ?? []) {
    if (cli.pageNum !== undefined && page["gh:pageNumber"] !== cli.pageNum) continue;
    for (const panel of page["gh:panels"] ?? []) {
      if (!isEligible(panel)) continue;
      // Skip if SFX already populated, unless --force
      if (!cli.force && Array.isArray(panel["gh:sfx"]) && panel["gh:sfx"].length > 0) continue;
      targets.push({ page, panel });
    }
  }
  console.log(`SFX auto: ${targets.length} eligible panel(s)\n`);

  let ok = 0, fail = 0, totalAdded = 0;
  for (let i = 0; i < targets.length; i++) {
    const { page, panel } = targets[i];
    process.stdout.write(`[${i+1}/${targets.length}] p${page["gh:pageNumber"]} ${panel["@id"]} (${panel["gh:tone"]}/${panel["gh:panelLayout"]?.["gh:emphasis"]}) ... `);
    const sfx = await generateSfx(panel, page);
    if (sfx === null) { console.log("FAIL"); fail++; continue; }
    if (sfx.length === 0) { console.log("no-sfx"); ok++; continue; }
    // Annotate + store
    for (const s of sfx) {
      s["gh:autoGenerated"] = true;
      s["gh:generatedAt"] = new Date().toISOString();
      s["gh:model"] = MODEL;
    }
    panel["gh:sfx"] = sfx;
    totalAdded += sfx.length;
    console.log(`+${sfx.length}: ${sfx.map((s: any) => s["gh:text"]).join(", ")}`);
    ok++;
    // Throttle
    if (i < targets.length - 1) await new Promise((r) => setTimeout(r, 800));
  }

  fs.writeFileSync(EPISODE_PATH, JSON.stringify(ep, null, 2) + "\n");
  console.log(`\nDone: ${ok}/${targets.length} OK, ${fail} fail, ${totalAdded} SFX added`);
}

main().catch((e) => { console.error(e); process.exit(1); });
