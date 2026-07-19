/**
 * Quality / provider comparison for a panel set.
 *
 * For each panel, generates 4 variants:
 *   - gpt-image-2 quality=low
 *   - gpt-image-2 quality=medium
 *   - gpt-image-2 quality=high
 *   - Gemini (google/gemini-3-pro-image-preview via OpenRouter)
 *
 * Saves with distinct suffixes (_gpt2low / _gpt2med / _gpt2high / _gemini) and runs critique on each.
 * Outputs a side-by-side comparison report.
 *
 * Skips Gemini if OPENROUTER_API_KEY not set.
 *
 * Usage:
 *   OPENAI_API_KEY=... OPENROUTER_API_KEY=... npx tsx src/quality-compare.ts --page 0
 *   OPENAI_API_KEY=... npx tsx src/quality-compare.ts --panel-id panel:p0n1-v3
 */
import * as fs from "node:fs";
import { generate as openaiGenerate, critique as openaiCritique, computeQp, computeQi, combineQ, type RichCritique } from "./lib/openai.js";

const REPO = "/Users/junkawasaki/github/ghosthacker/260123-jump";
const EPISODE_PATH = `${REPO}/resources/episodes/arc0-1-origin/episode.jsonld`;
const COMPARE_DIR = `${REPO}/resources/episodes/arc0-1-origin/quality-compare-runs`;

const GEMINI_MODEL = process.env.LG_GEMINI_MODEL ?? "google/gemini-3-pro-image-preview";
const OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions";

interface CliArgs { pageNum?: number; panelId?: string }
function parseArgs(): CliArgs {
  const a = process.argv.slice(2);
  const o: CliArgs = {};
  for (let i = 0; i < a.length; i++) {
    if (a[i] === "--page" && a[i+1]) o.pageNum = Number(a[++i]);
    else if (a[i] === "--panel-id" && a[i+1]) o.panelId = a[++i];
  }
  return o;
}

function buildPrompt(panel: any, page: any): string {
  const setting = page["gh:setting"] ?? page["gh:pageLayoutV3"]?.["gh:gridDescription"] ?? "";
  const visualNote = page["gh:visualNote"] ?? "";
  const visualDesc = panel["gh:visualDescription"] ?? panel["visual"] ?? "";
  const shot = panel["shot"] ?? "Medium Shot";
  const props = panel["gh:props"] ?? [];
  const focusChar = panel["gh:focusCharacter"] ?? "";
  const allChars = panel["gh:allCharacters"] ?? [];
  const visualStyle = panel["gh:visualStyle"] ?? "film-medium";
  const tone = panel["gh:tone"] ?? "quiet";
  const signals = panel["gh:emotionPhysicalSignals"] ?? [];

  const sigLine = signals.length > 0
    ? `Physical signals: ${signals.map((s: any) => `${s.character}: ${s.signals.join(", ")}`).join(" / ")}.`
    : "";
  const propLine = props.length > 0 ? `Key props: ${props.join(", ")}.` : "";
  const charLine = allChars.length > 0 ? `Characters: ${allChars.join(", ")}${focusChar ? ` (focus: ${focusChar})` : ""}.` : "";

  return [
    "Fictional manga panel illustration of original characters for Weekly Shounen Jump style fiction publication. Black-and-white monochrome with screen tones, single full-bleed image, standalone artwork.",
    setting ? `LOCATION: ${setting}.` : "",
    visualNote ? `Set dressing: ${visualNote}.` : "",
    `Visual: ${visualDesc}.`,
    `Shot: ${shot}.`,
    propLine, charLine, sigLine,
    `Tone: ${tone}. Style: ${visualStyle}.`,
    "Cinematic manga composition, depth of field, atmospheric line work, halftone screen tones.",
    "ABSOLUTE: NO text, NO speech bubbles, NO captions, NO labels in the image.",
  ].filter(Boolean).join(" ");
}

async function generateOpenAIQuality(prompt: string, quality: "low" | "medium" | "high"): Promise<string> {
  const apiKey = process.env.OPENAI_API_KEY;
  if (!apiKey) throw new Error("OPENAI_API_KEY not set");
  const r = await fetch("https://api.openai.com/v1/images/generations", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
    body: JSON.stringify({ model: "gpt-image-2", prompt: prompt.slice(0, 32000), size: "1024x1536", quality, n: 1 }),
  });
  if (!r.ok) throw new Error(`OpenAI ${quality} HTTP ${r.status}: ${(await r.text()).slice(0, 300)}`);
  const j: any = await r.json();
  if (j.error) throw new Error(`OpenAI ${quality}: ${j.error.message}`);
  const b64 = j.data?.[0]?.b64_json;
  if (!b64) throw new Error(`OpenAI ${quality}: no b64`);
  return b64;
}

async function generateGemini(prompt: string): Promise<string> {
  const apiKey = process.env.OPENROUTER_API_KEY;
  if (!apiKey) throw new Error("OPENROUTER_API_KEY not set");
  const r = await fetch(OPENROUTER_URL, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
      "HTTP-Referer": "https://ghosthacker.etzhayyim.com",
      "X-Title": "ghosthacker-quality-compare",
    },
    body: JSON.stringify({
      model: GEMINI_MODEL,
      messages: [{ role: "user", content: [{ type: "text", text: prompt }] }],
      modalities: ["text", "image"],
      image_config: { aspect_ratio: "3:4", image_size: "1K" },
      stream: false,
    }),
  });
  if (!r.ok) throw new Error(`Gemini HTTP ${r.status}: ${(await r.text()).slice(0, 300)}`);
  const j: any = await r.json();
  if (j.error) throw new Error(`Gemini: ${j.error.message ?? JSON.stringify(j.error)}`);
  const raw = j.choices?.[0]?.message?.images?.[0]?.image_url;
  const url = typeof raw === "string" ? raw : raw?.url;
  if (!url) throw new Error(`Gemini: no image (${(j.choices?.[0]?.message?.content ?? "").slice(0, 200)})`);
  if (url.startsWith("data:")) return url.slice(url.indexOf(",") + 1);
  const r2 = await fetch(url);
  return Buffer.from(await r2.arrayBuffer()).toString("base64");
}

interface VariantResult {
  variant: string;
  outputPath: string;
  durationMs: number;
  ok: boolean;
  error?: string;
  critique?: RichCritique;
  Q_p?: number; Q_i?: number; Q_total?: number; Q_tier?: string;
}

async function main() {
  const cli = parseArgs();
  if (!process.env.OPENAI_API_KEY) {
    console.error("ERROR: OPENAI_API_KEY not set");
    process.exit(1);
  }
  const hasGemini = !!process.env.OPENROUTER_API_KEY;
  if (!hasGemini) console.log("(skipping Gemini variant — OPENROUTER_API_KEY not set)");

  const ep = JSON.parse(fs.readFileSync(EPISODE_PATH, "utf-8"));
  fs.mkdirSync(COMPARE_DIR, { recursive: true });

  const targets: Array<{ page: any; panel: any }> = [];
  for (const page of ep["gh:pages"]) {
    if (cli.pageNum !== undefined && page["gh:pageNumber"] !== cli.pageNum) continue;
    for (const panel of page["gh:panels"] ?? []) {
      if (cli.panelId && panel["@id"] !== cli.panelId) continue;
      targets.push({ page, panel });
    }
  }
  if (!targets.length) { console.log("no targets"); return; }
  console.log(`Compare ${targets.length} panel(s) × ${hasGemini ? 4 : 3} variants`);

  const variants: { name: string; gen: (p: string) => Promise<string> }[] = [
    { name: "gpt2low",  gen: (p) => generateOpenAIQuality(p, "low") },
    { name: "gpt2med",  gen: (p) => generateOpenAIQuality(p, "medium") },
    { name: "gpt2high", gen: (p) => generateOpenAIQuality(p, "high") },
  ];
  if (hasGemini) variants.push({ name: "gemini", gen: generateGemini });

  const allResults: any[] = [];

  for (const { page, panel } of targets) {
    const pn = page["gh:pageNumber"];
    const safeId = panel["@id"].replace(/[^a-zA-Z0-9._-]/g, "_");
    const prompt = buildPrompt(panel, page);
    console.log(`\n=== p${pn} ${panel["@id"]} (${panel["shot"]}, ${panel["gh:visualStyle"] ?? "n/a"}) ===`);

    const variantResults: VariantResult[] = [];
    for (const v of variants) {
      const outputPath = `${COMPARE_DIR}/${safeId}_${v.name}.png`;
      process.stdout.write(`  ${v.name.padEnd(8)} ... `);
      const t0 = Date.now();
      try {
        const b64 = await v.gen(prompt);
        fs.writeFileSync(outputPath, Buffer.from(b64, "base64"));
        const dur = Date.now() - t0;
        // Critique
        const c = await openaiCritique(outputPath, panel["gh:visualDescription"] ?? panel["visual"] ?? "", panel["gh:allCharacters"] ?? [], panel["shot"] ?? "Medium", panel["gh:props"] ?? [], panel["gh:emotionPhysicalSignals"] ?? [], panel["gh:visualStyle"] ?? "");
        const { Q_p } = computeQp({
          sceneSubject: panel["gh:sceneSubject"], focusCharacter: panel["gh:focusCharacter"],
          allCharacters: panel["gh:allCharacters"], props: panel["gh:props"], shot: panel["shot"],
          panelLayout: panel["gh:panelLayout"], dialogues: panel["dialogue"], scriptEntryIndices: panel["gh:scriptEntryIndices"],
          visual: panel["gh:visualDescription"] ?? panel["visual"],
          precedingBeat: panel["gh:precedingBeat"], followingBeat: panel["gh:followingBeat"], visualStyle: panel["gh:visualStyle"],
        });
        const { Q_i } = computeQi(c, panel["gh:props"] ?? []);
        const Q_total = combineQ(Q_p, Q_i);
        const tier = Q_total >= 0.75 ? "ship" : Q_total >= 0.55 ? "review" : "regen";
        console.log(`${dur}ms | critic ${c.score}/10 | Q_p=${Q_p.toFixed(2)} Q_i=${Q_i.toFixed(2)} Q_total=${Q_total.toFixed(2)} [${tier}]`);
        variantResults.push({ variant: v.name, outputPath, durationMs: dur, ok: true, critique: c, Q_p, Q_i, Q_total, Q_tier: tier });
      } catch (e) {
        console.log(`FAIL: ${e instanceof Error ? e.message.slice(0, 120) : String(e)}`);
        variantResults.push({ variant: v.name, outputPath, durationMs: Date.now() - t0, ok: false, error: String(e) });
      }
      await new Promise((r) => setTimeout(r, 1500));
    }
    allResults.push({ panelId: panel["@id"], pageNum: pn, shot: panel["shot"], visualStyle: panel["gh:visualStyle"], variants: variantResults });
  }

  // Summary table
  console.log(`\n\n=== Summary ===`);
  console.log(`${"Panel".padEnd(22)} ${"variant".padEnd(8)} ${"time".padEnd(7)} ${"score".padEnd(6)} ${"Q_i".padEnd(5)} ${"Q_total".padEnd(8)} ${"tier"}`);
  for (const r of allResults) {
    for (const v of r.variants) {
      if (!v.ok) { console.log(`${r.panelId.padEnd(22)} ${v.variant.padEnd(8)} FAIL`); continue; }
      const t = `${(v.durationMs/1000).toFixed(0)}s`;
      console.log(`${r.panelId.padEnd(22)} ${v.variant.padEnd(8)} ${t.padEnd(7)} ${(v.critique?.score ?? 0).toString().padEnd(6)} ${v.Q_i?.toFixed(2).padEnd(5)} ${v.Q_total?.toFixed(2).padEnd(8)} ${v.Q_tier}`);
    }
  }

  // Report
  const reportPath = `${COMPARE_DIR}/report-${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
  fs.writeFileSync(reportPath, JSON.stringify({ runAt: new Date().toISOString(), targets: targets.map((t) => t.panel["@id"]), variants: variants.map((v) => v.name), results: allResults }, null, 2));
  console.log(`\nReport: ${reportPath}`);
}

main().catch((e) => { console.error(e); process.exit(1); });
