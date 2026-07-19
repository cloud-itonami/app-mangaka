/**
 * Last-ditch generation for moderation-stuck panels:
 * - Strip ALL character descriptions
 * - Use abstract atmospheric manga prompt only
 * - Skip refs entirely
 * - Soft style suffix only
 */
import * as fs from "node:fs";
import { generate, critique, computeQp, computeQi, combineQ } from "./lib/openai.js";

const REPO = "/Users/junkawasaki/github/ghosthacker/260123-jump";
const EPISODE_PATH = `${REPO}/resources/episodes/arc0-1-origin/episode.jsonld`;

const STUCK_IDS = ["panel:p32n7-v3", "panel:p34n4-v3", "panel:p37n1-v3", "panel:p40n1-v3", "panel:p43n7-v3"];

async function main() {
  const ep = JSON.parse(fs.readFileSync(EPISODE_PATH, "utf-8"));
  for (const id of STUCK_IDS) {
    for (const page of ep["gh:pages"]) {
      const panel = page["gh:panels"]?.find((p: any) => p["@id"] === id);
      if (!panel) continue;

      // Build minimal abstract prompt — no characters, no school, no specific descriptors
      const prompt = [
        "Atmospheric manga illustration, monochrome with screen tones, single full-bleed image.",
        `Mood / scene: ${panel["gh:visualDescription"]}`,
        "Pure atmospheric composition, no text, no labels, no captions, no scene markers.",
        "Rendered in shounen manga style with cinematic composition, depth of field, and tonal gradients.",
      ].join(" ");

      const safeId = id.replace(/[^a-zA-Z0-9._-]/g, "_");
      const existingCount = (panel["gh:generatedImages"] ?? []).length;
      const outputPath = `${REPO}/resources/images/episodes/episode:arc0-1-origin/pages/${page["gh:pageNumber"]}/panel_${safeId}_v${existingCount + 1}.png`;

      console.log(`\n${id} (p${page["gh:pageNumber"]}):`);
      console.log(`  prompt[0:200]: ${prompt.slice(0, 200)}...`);

      try {
        const t0 = Date.now();
        const b64 = await generate(prompt);
        fs.mkdirSync(outputPath.substring(0, outputPath.lastIndexOf("/")), { recursive: true });
        fs.writeFileSync(outputPath, Buffer.from(b64, "base64"));
        const dur = Date.now() - t0;
        console.log(`  OK ${dur}ms → ${outputPath.split("/").pop()}`);

        // Critique + Q-score
        const c = await critique(outputPath, panel["gh:visualDescription"], [], panel["shot"], panel["gh:props"] ?? [], [], panel["gh:visualStyle"]);
        const { Q_p } = computeQp(panel);
        const { Q_i } = computeQi(c, panel["gh:props"] ?? []);
        const Q_total = combineQ(Q_p, Q_i);
        const tier = Q_total >= 0.75 ? "ship" : Q_total >= 0.55 ? "review" : "regen";
        console.log(`  Q_p=${Q_p.toFixed(2)} Q_i=${Q_i.toFixed(2)} Q_total=${Q_total.toFixed(2)} [${tier}]`);

        // Update panel
        const relUrl = outputPath.slice(outputPath.indexOf("/resources/") + "/resources".length);
        const imageEntry = {
          "gh:imageUrl": relUrl,
          "gh:imagePrompt": prompt,
          "gh:generatedAt": Math.floor(Date.now() / 1000),
          "gh:model": "gpt-image-2",
          "gh:quality": "low",
          "gh:durationMs": dur,
          "gh:generationPipeline": "langgraph-ts-minimal-fallback-no-ref",
          "gh:referenceCharacters": [],
          "gh:referenceSelections": [],
          "gh:Q_p": Q_p, "gh:Q_i": Q_i, "gh:Q_total": Q_total, "gh:Q_tier": tier,
        };
        if (!panel["gh:generatedImages"]) panel["gh:generatedImages"] = [];
        panel["gh:generatedImages"].push(imageEntry);
        panel["gh:currentImageIndex"] = panel["gh:generatedImages"].length - 1;
        panel["gh:generatedImageUrl"] = relUrl;
        delete panel["gh:needsImageGeneration"];
      } catch (e) {
        console.log(`  FAIL: ${e instanceof Error ? e.message.slice(0, 200) : String(e)}`);
      }
    }
  }
  fs.writeFileSync(EPISODE_PATH, JSON.stringify(ep, null, 2) + "\n");
  console.log("\nDone.");
}

main().catch((e) => { console.error(e); process.exit(1); });
