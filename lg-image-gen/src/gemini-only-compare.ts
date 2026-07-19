/**
 * Gemini-only variant for quality-compare (assumes gpt2low/med/high already generated).
 */
import * as fs from "node:fs";
import { critique, computeQp, computeQi, combineQ } from "./lib/openai.js";

const REPO = "/Users/junkawasaki/github/ghosthacker/260123-jump";
const EPISODE_PATH = `${REPO}/resources/episodes/arc0-1-origin/episode.jsonld`;
const COMPARE_DIR = `${REPO}/resources/episodes/arc0-1-origin/quality-compare-runs`;
const MODEL = process.env.LG_GEMINI_MODEL ?? "google/gemini-3-pro-image-preview";
const URL = "https://openrouter.ai/api/v1/chat/completions";

interface CliArgs { pageNum?: number }
function parseArgs(): CliArgs {
  const a = process.argv.slice(2);
  const o: CliArgs = {};
  for (let i = 0; i < a.length; i++) if (a[i] === "--page" && a[i+1]) o.pageNum = Number(a[++i]);
  return o;
}

function buildPrompt(panel: any, page: any): string {
  const setting = page["gh:setting"] ?? "";
  const visualNote = page["gh:visualNote"] ?? "";
  const visualDesc = panel["gh:visualDescription"] ?? panel["visual"] ?? "";
  const shot = panel["shot"] ?? "Medium Shot";
  const props = panel["gh:props"] ?? [];
  const focusChar = panel["gh:focusCharacter"] ?? "";
  const allChars = panel["gh:allCharacters"] ?? [];
  const visualStyle = panel["gh:visualStyle"] ?? "film-medium";
  const tone = panel["gh:tone"] ?? "quiet";
  const signals = panel["gh:emotionPhysicalSignals"] ?? [];
  const sigLine = signals.length > 0 ? `Physical signals: ${signals.map((s: any) => `${s.character}: ${s.signals.join(", ")}`).join(" / ")}.` : "";
  const propLine = props.length > 0 ? `Key props: ${props.join(", ")}.` : "";
  const charLine = allChars.length > 0 ? `Characters: ${allChars.join(", ")}${focusChar ? ` (focus: ${focusChar})` : ""}.` : "";
  return [
    "Fictional manga panel illustration of original characters for Weekly Shounen Jump style fiction publication. Black-and-white monochrome with screen tones, single full-bleed image, standalone artwork.",
    setting ? `LOCATION: ${setting}.` : "",
    visualNote ? `Set dressing: ${visualNote}.` : "",
    `Visual: ${visualDesc}.`,
    `Shot: ${shot}.`, propLine, charLine, sigLine,
    `Tone: ${tone}. Style: ${visualStyle}.`,
    "Cinematic manga composition, depth of field, atmospheric line work, halftone screen tones.",
    "ABSOLUTE: NO text, NO speech bubbles, NO captions, NO labels in the image.",
  ].filter(Boolean).join(" ");
}

async function generateGemini(prompt: string): Promise<string> {
  const apiKey = process.env.OPENROUTER_API_KEY;
  if (!apiKey) throw new Error("OPENROUTER_API_KEY not set");
  const r = await fetch(URL, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
      "HTTP-Referer": "https://ghosthacker.etzhayyim.com",
      "X-Title": "ghosthacker-gemini-compare",
    },
    body: JSON.stringify({
      model: MODEL,
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

async function main() {
  const cli = parseArgs();
  if (!process.env.OPENROUTER_API_KEY) { console.error("OPENROUTER_API_KEY not set"); process.exit(1); }
  if (!process.env.OPENAI_API_KEY) { console.error("OPENAI_API_KEY needed for critique"); process.exit(1); }
  const ep = JSON.parse(fs.readFileSync(EPISODE_PATH, "utf-8"));
  fs.mkdirSync(COMPARE_DIR, { recursive: true });

  const targets: Array<{ page: any; panel: any }> = [];
  for (const page of ep["gh:pages"]) {
    if (cli.pageNum !== undefined && page["gh:pageNumber"] !== cli.pageNum) continue;
    for (const panel of page["gh:panels"] ?? []) targets.push({ page, panel });
  }

  console.log(`Gemini compare — ${targets.length} panel(s) via ${MODEL}\n`);
  for (const { page, panel } of targets) {
    const pn = page["gh:pageNumber"];
    const safeId = panel["@id"].replace(/[^a-zA-Z0-9._-]/g, "_");
    const outputPath = `${COMPARE_DIR}/${safeId}_gemini.png`;
    const prompt = buildPrompt(panel, page);
    process.stdout.write(`p${pn} ${panel["@id"]} ... `);
    try {
      const t0 = Date.now();
      const b64 = await generateGemini(prompt);
      fs.writeFileSync(outputPath, Buffer.from(b64, "base64"));
      const dur = Date.now() - t0;
      const c = await critique(outputPath, panel["gh:visualDescription"] ?? panel["visual"] ?? "", panel["gh:allCharacters"] ?? [], panel["shot"] ?? "Medium", panel["gh:props"] ?? [], panel["gh:emotionPhysicalSignals"] ?? [], panel["gh:visualStyle"] ?? "");
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
    } catch (e) {
      console.log(`FAIL: ${e instanceof Error ? e.message.slice(0, 200) : String(e)}`);
    }
    await new Promise((r) => setTimeout(r, 1500));
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
