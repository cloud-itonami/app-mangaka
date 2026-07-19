/**
 * v2 quality-compare: fix sub-panel split issue by replacing "panel" wording with "illustration" / "single image"
 */
import * as fs from "node:fs";
import { critique, computeQp, computeQi, combineQ } from "./lib/openai.js";

const REPO = "/Users/junkawasaki/github/ghosthacker/260123-jump";
const EPISODE_PATH = `${REPO}/resources/episodes/arc0-1-origin/episode.jsonld`;
const COMPARE_DIR = `${REPO}/resources/episodes/arc0-1-origin/quality-compare-runs`;

function buildPromptV2(panel: any, page: any): string {
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
  // CRITICAL fix: avoid "panel" word, force SINGLE image
  return [
    "ONE SINGLE manga-style illustration filling the entire image, black-and-white monochrome with screen tones. NOT a manga page with multiple sub-frames — just ONE single contiguous illustration.",
    "Fictional manga of original characters for Weekly Shounen Jump style fiction publication.",
    setting ? `LOCATION: ${setting}.` : "",
    visualNote ? `Set dressing: ${visualNote}.` : "",
    `Subject: ${visualDesc}.`,
    `Camera framing: ${shot}.`, propLine, charLine, sigLine,
    `Tone: ${tone}. Style: ${visualStyle}.`,
    "Cinematic composition, depth of field, atmospheric line work, halftone screen tones.",
    "ABSOLUTE: ONE seamless full-bleed image only. NO sub-panels, NO panel dividers, NO multi-frame layout, NO text, NO speech bubbles, NO captions.",
  ].filter(Boolean).join(" ");
}

async function generateQ(prompt: string, quality: string): Promise<string> {
  const r = await fetch("https://api.openai.com/v1/images/generations", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${process.env.OPENAI_API_KEY}` },
    body: JSON.stringify({ model: "gpt-image-2", prompt: prompt.slice(0, 32000), size: "1024x1536", quality, n: 1 }),
  });
  if (!r.ok) throw new Error(`OpenAI ${quality}: ${(await r.text()).slice(0, 200)}`);
  const j: any = await r.json();
  return j.data?.[0]?.b64_json;
}

async function main() {
  const ep = JSON.parse(fs.readFileSync(EPISODE_PATH, "utf-8"));
  fs.mkdirSync(COMPARE_DIR, { recursive: true });
  const p0 = ep["gh:pages"].find((p: any) => p["gh:pageNumber"] === 0);
  const panels = p0["gh:panels"];
  const qualities = ["low", "medium"]; // skip high (similar score, 3x cost)

  for (const panel of panels) {
    const safeId = panel["@id"].replace(/[^a-zA-Z0-9._-]/g, "_");
    const prompt = buildPromptV2(panel, p0);
    console.log(`\n=== p0 ${panel["@id"]} ===`);
    for (const q of qualities) {
      const outputPath = `${COMPARE_DIR}/${safeId}_v2promptfix_gpt2${q}.png`;
      process.stdout.write(`  gpt2${q} v2-prompt ... `);
      try {
        const t0 = Date.now();
        const b64 = await generateQ(prompt, q);
        fs.writeFileSync(outputPath, Buffer.from(b64, "base64"));
        const dur = Date.now() - t0;
        const c = await critique(outputPath, panel["gh:visualDescription"] ?? "", panel["gh:allCharacters"] ?? [], panel["shot"] ?? "Medium", panel["gh:props"] ?? [], panel["gh:emotionPhysicalSignals"] ?? [], panel["gh:visualStyle"] ?? "");
        const { Q_p } = computeQp({
          sceneSubject: panel["gh:sceneSubject"], focusCharacter: panel["gh:focusCharacter"],
          allCharacters: panel["gh:allCharacters"], props: panel["gh:props"], shot: panel["shot"],
          panelLayout: panel["gh:panelLayout"], dialogues: panel["dialogue"], scriptEntryIndices: panel["gh:scriptEntryIndices"],
          visual: panel["gh:visualDescription"], precedingBeat: panel["gh:precedingBeat"], followingBeat: panel["gh:followingBeat"], visualStyle: panel["gh:visualStyle"],
        });
        const { Q_i } = computeQi(c, panel["gh:props"] ?? []);
        const Q_total = combineQ(Q_p, Q_i);
        console.log(`${dur}ms | critic ${c.score}/10 | Q_p=${Q_p.toFixed(2)} Q_i=${Q_i.toFixed(2)} Q_total=${Q_total.toFixed(2)}`);
      } catch (e) {
        console.log(`FAIL: ${e instanceof Error ? e.message.slice(0, 120) : String(e)}`);
      }
      await new Promise((r) => setTimeout(r, 1500));
    }
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
