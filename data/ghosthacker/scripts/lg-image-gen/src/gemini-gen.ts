/**
 * Gemini image generation via OpenRouter.
 *
 * Pattern derived from 60-apps/etzhayyim-project-narou/ghosthacker/apps/web/src/lib/ai/openrouter-image.ts.
 * Uses /v1/chat/completions with `modalities: ["text", "image"]` + `image_config`.
 *
 * Usage:
 *   OPENROUTER_API_KEY=... npx tsx src/gemini-gen.ts --page 0
 *   OPENROUTER_API_KEY=... npx tsx src/gemini-gen.ts --panel-id panel:p0n1-v3
 */
import * as fs from "node:fs";

const REPO = "/Users/junkawasaki/github/ghosthacker/260123-jump";
const EPISODE_PATH = `${REPO}/resources/episodes/arc0-1-origin/episode.jsonld`;

const MODEL = process.env.LG_GEMINI_MODEL ?? "google/gemini-3-pro-image-preview";
const URL = "https://openrouter.ai/api/v1/chat/completions";

interface CliArgs { pageNum?: number; panelId?: string }
function parseArgs(): CliArgs {
  const a = process.argv.slice(2);
  const out: CliArgs = {};
  for (let i = 0; i < a.length; i++) {
    if (a[i] === "--page" && a[i+1]) out.pageNum = Number(a[++i]);
    else if (a[i] === "--panel-id" && a[i+1]) out.panelId = a[++i];
  }
  return out;
}

async function generateGemini(prompt: string, refImagePaths: string[] = []): Promise<string> {
  const apiKey = process.env.OPENROUTER_API_KEY;
  if (!apiKey) throw new Error("OPENROUTER_API_KEY not set");

  // Build multimodal content: text prompt + optional reference images
  const content: any[] = [{ type: "text", text: prompt }];
  for (const p of refImagePaths) {
    const buf = fs.readFileSync(p);
    content.push({ type: "image_url", image_url: { url: `data:image/png;base64,${buf.toString("base64")}` } });
  }

  const r = await fetch(URL, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
      "HTTP-Referer": "https://ghosthacker.etzhayyim.com",
      "X-Title": "ghosthacker-arc0-1-gemini-gen",
    },
    body: JSON.stringify({
      model: MODEL,
      messages: [{ role: "user", content }],
      modalities: ["text", "image"],
      image_config: { aspect_ratio: "3:4", image_size: "1K" },
      stream: false,
    }),
  });
  if (!r.ok) throw new Error(`Gemini HTTP ${r.status}: ${(await r.text()).slice(0, 300)}`);
  const j: any = await r.json();
  if (j.error) throw new Error(`Gemini error: ${j.error.message ?? JSON.stringify(j.error)}`);
  const raw = j.choices?.[0]?.message?.images?.[0]?.image_url;
  const url = typeof raw === "string" ? raw : raw?.url;
  if (!url) throw new Error(`No image returned: ${(j.choices?.[0]?.message?.content ?? "").slice(0, 200)}`);
  // url is usually data: URL with base64
  if (url.startsWith("data:")) return url.slice(url.indexOf(",") + 1);
  // If http URL, fetch
  const r2 = await fetch(url);
  const buf = Buffer.from(await r2.arrayBuffer());
  return buf.toString("base64");
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

  const sigLine = signals.length > 0
    ? `Physical signals: ${signals.map((s: any) => `${s.character}: ${s.signals.join(", ")}`).join(" / ")}.`
    : "";
  const propLine = props.length > 0 ? `Key props: ${props.join(", ")}.` : "";
  const charLine = allChars.length > 0 ? `Characters in frame: ${allChars.join(", ")}${focusChar ? ` (focus: ${focusChar})` : ""}.` : "";

  return [
    "Fictional manga panel illustration of original characters for a Weekly Shounen Jump style fiction publication. Black-and-white monochrome with screen tones, single full-bleed image, each panel standalone.",
    setting ? `LOCATION: ${setting}.` : "",
    visualNote ? `Set dressing: ${visualNote}.` : "",
    `Visual: ${visualDesc}.`,
    `Shot framing: ${shot}.`,
    propLine, charLine, sigLine,
    `Tone: ${tone}.`,
    `Visual style: ${visualStyle} — cinematic manga composition with depth of field and atmospheric line work.`,
    "ABSOLUTE: NO text, NO speech bubbles, NO captions, NO labels in the image.",
  ].filter(Boolean).join(" ");
}

async function main() {
  const cli = parseArgs();
  if (!process.env.OPENROUTER_API_KEY) {
    console.error("ERROR: OPENROUTER_API_KEY not set");
    process.exit(1);
  }
  const ep = JSON.parse(fs.readFileSync(EPISODE_PATH, "utf-8"));

  const targets: Array<{ page: any; panel: any }> = [];
  for (const page of ep["gh:pages"]) {
    if (cli.pageNum !== undefined && page["gh:pageNumber"] !== cli.pageNum) continue;
    for (const panel of page["gh:panels"] ?? []) {
      if (cli.panelId && panel["@id"] !== cli.panelId) continue;
      targets.push({ page, panel });
    }
  }
  if (targets.length === 0) {
    console.log("No matching panels.");
    return;
  }

  console.log(`Gemini gen — ${targets.length} panel(s) via ${MODEL}`);

  for (let i = 0; i < targets.length; i++) {
    const { page, panel } = targets[i];
    const pn = page["gh:pageNumber"];
    const safeId = panel["@id"].replace(/[^a-zA-Z0-9._-]/g, "_");
    const existingCount = (panel["gh:generatedImages"] ?? []).length;
    const outputPath = `${REPO}/resources/images/episodes/episode:arc0-1-origin/pages/${pn}/panel_${safeId}_gemini_v${existingCount + 1}.png`;
    fs.mkdirSync(outputPath.substring(0, outputPath.lastIndexOf("/")), { recursive: true });

    const prompt = buildPrompt(panel, page);
    console.log(`\n[${i + 1}/${targets.length}] p${pn} ${panel["@id"]}`);
    console.log(`  shot=${panel["shot"]} style=${panel["gh:visualStyle"]}`);

    try {
      const t0 = Date.now();
      const b64 = await generateGemini(prompt);
      fs.writeFileSync(outputPath, Buffer.from(b64, "base64"));
      const dur = Date.now() - t0;
      console.log(`  OK ${dur}ms → ${outputPath.split("/").pop()}`);

      const relUrl = outputPath.slice(outputPath.indexOf("/resources/") + "/resources".length);
      if (!panel["gh:generatedImages"]) panel["gh:generatedImages"] = [];
      panel["gh:generatedImages"].push({
        "gh:imageUrl": relUrl,
        "gh:imagePrompt": prompt,
        "gh:generatedAt": Math.floor(Date.now() / 1000),
        "gh:model": MODEL,
        "gh:durationMs": dur,
        "gh:generationPipeline": "langgraph-ts-gemini-openrouter",
        "gh:provider": "openrouter",
      });
      panel["gh:currentImageIndex"] = panel["gh:generatedImages"].length - 1;
      panel["gh:generatedImageUrl"] = relUrl;
    } catch (e) {
      console.log(`  FAIL: ${e instanceof Error ? e.message.slice(0, 200) : String(e)}`);
    }
    if (i < targets.length - 1) await new Promise((r) => setTimeout(r, 1500));
  }

  fs.writeFileSync(EPISODE_PATH, JSON.stringify(ep, null, 2) + "\n");
  console.log("\nDone.");
}

main().catch((e) => { console.error(e); process.exit(1); });
