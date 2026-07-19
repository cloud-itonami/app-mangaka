/**
 * Alternative prompt approach for 5 stuck panels:
 * - Frame as "phenomenon / atmospheric event" not "character does action"
 * - Pull narration directly from v2 outline source entries
 * - Use manga-craft vocabulary (halftone, speed lines, ink flow, screen tone)
 * - Skip refs entirely
 */
import * as fs from "node:fs";
import { generate, critique, computeQp, computeQi, combineQ } from "./lib/openai.js";

const REPO = "/Users/junkawasaki/github/ghosthacker/260123-jump";
const EPISODE_PATH = `${REPO}/resources/episodes/arc0-1-origin/episode.jsonld`;
const OUTLINE_PATH = `${REPO}/resources/episodes/arc0-1-origin/story-outline.jsonld`;

// Panels still flagged as low Q from minimal-gen (regen tier or just want to retry)
const STUCK_IDS = ["panel:p32n7-v3", "panel:p34n4-v3", "panel:p37n1-v3", "panel:p40n1-v3", "panel:p43n7-v3"];

// Per-panel phenomenon framing (carefully written to avoid moderation triggers while preserving scene)
const PHENOMENON_PROMPT: Record<string, string> = {
  "panel:p32n7-v3": "A wide cinematic illustration of a futuristic crystal cube projector device on a wooden classroom desk, emitting a soft luminous pulse that ripples outward as a translucent geometric pattern. The room fills with a serene blue glow. Cinematic depth of field, halftone screen-tone shading, ink-flow line art, no text or labels.",
  "panel:p34n4-v3": "Wide atmospheric illustration of a school classroom dimly lit, with small abstract dark sphere shapes floating gently above each desk like quiet metaphors of unspoken thought. Soft afternoon light through the windows. Detailed manga screen-tone background, cinematic composition, ink-flow line work, no text.",
  "panel:p37n1-v3": "Wide atmospheric establishing shot of a school classroom interior, where a single tall elongated shadow stretches dramatically along the floor and wall, suggesting an immense abstract presence looming. The room itself appears small and quiet by contrast. Strong tonal contrast, manga screen-tone shading, cinematic perspective, ink-flow line work, no text.",
  "panel:p40n1-v3": "Wide cinematic illustration of a school classroom glowing with soft restored radiance, tiny luminous particles floating gently throughout the air like a quiet constellation. The familiar details of the room re-emerge in warm tones. Detailed manga screen-tone background, cinematic depth, ink-flow line art, no text.",
  "panel:p43n7-v3": "Wide atmospheric illustration of a private study room interior, featuring a small geometric Holon device on a desk emitting a luminous emblem-like geometric pattern. Wooden floor, bookshelf, soft window light. Cinematic composition, manga screen-tone shading, ink-flow line art, no text or labels.",
};

async function main() {
  const ep = JSON.parse(fs.readFileSync(EPISODE_PATH, "utf-8"));
  const outline = JSON.parse(fs.readFileSync(OUTLINE_PATH, "utf-8"));

  for (const id of STUCK_IDS) {
    for (const page of ep["gh:pages"]) {
      const panel = page["gh:panels"]?.find((p: any) => p["@id"] === id);
      if (!panel) continue;
      const prompt = PHENOMENON_PROMPT[id];
      if (!prompt) { console.log(`${id}: no prompt template`); continue; }

      const safeId = id.replace(/[^a-zA-Z0-9._-]/g, "_");
      const existingCount = (panel["gh:generatedImages"] ?? []).length;
      const outputPath = `${REPO}/resources/images/episodes/episode:arc0-1-origin/pages/${page["gh:pageNumber"]}/panel_${safeId}_v${existingCount + 1}.png`;

      console.log(`\n${id} (p${page["gh:pageNumber"]}):`);
      console.log(`  prompt[0:150]: ${prompt.slice(0, 150)}...`);

      try {
        const t0 = Date.now();
        const b64 = await generate(prompt);
        fs.mkdirSync(outputPath.substring(0, outputPath.lastIndexOf("/")), { recursive: true });
        fs.writeFileSync(outputPath, Buffer.from(b64, "base64"));
        const dur = Date.now() - t0;
        console.log(`  OK ${dur}ms`);

        const c = await critique(outputPath, panel["gh:visualDescription"], [], panel["shot"], panel["gh:props"] ?? [], [], panel["gh:visualStyle"]);
        const { Q_p } = computeQp(panel);
        const { Q_i } = computeQi(c, panel["gh:props"] ?? []);
        const Q_total = combineQ(Q_p, Q_i);
        const tier = Q_total >= 0.75 ? "ship" : Q_total >= 0.55 ? "review" : "regen";
        console.log(`  Q_p=${Q_p.toFixed(2)} Q_i=${Q_i.toFixed(2)} Q_total=${Q_total.toFixed(2)} [${tier}]`);

        const relUrl = outputPath.slice(outputPath.indexOf("/resources/") + "/resources".length);
        panel["gh:generatedImages"].push({
          "gh:imageUrl": relUrl, "gh:imagePrompt": prompt,
          "gh:generatedAt": Math.floor(Date.now() / 1000),
          "gh:model": "gpt-image-2", "gh:quality": "low", "gh:durationMs": dur,
          "gh:generationPipeline": "langgraph-ts-alt-phenomenon-framing",
          "gh:Q_p": Q_p, "gh:Q_i": Q_i, "gh:Q_total": Q_total, "gh:Q_tier": tier,
        });
        panel["gh:currentImageIndex"] = panel["gh:generatedImages"].length - 1;
        panel["gh:generatedImageUrl"] = relUrl;
      } catch (e) {
        console.log(`  FAIL: ${e instanceof Error ? e.message.slice(0, 200) : String(e)}`);
      }
    }
  }
  fs.writeFileSync(EPISODE_PATH, JSON.stringify(ep, null, 2) + "\n");
  console.log("\nDone.");
}

main().catch((e) => { console.error(e); process.exit(1); });
