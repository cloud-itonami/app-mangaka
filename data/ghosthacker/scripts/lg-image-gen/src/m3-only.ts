import * as fs from "node:fs";
import { buildGraphM3 } from "./graph-m3.js";
import { critique } from "./lib/openai.js";
const REPO = "/Users/junkawasaki/github/ghosthacker/260123-jump";
const COMPARE_DIR = `${REPO}/resources/episodes/arc0-1-origin/compare-runs`;
const manifest = JSON.parse(fs.readFileSync(`${REPO}/resources/episodes/arc0-1-origin/image-gen-manifest.json`, "utf-8"));
for (const pid of ["panel:p1n1-v2", "panel:p1n5-v2"]) {
  const m = manifest.panels.find((p: any) => p.panelId === pid);
  const safeId = pid.replace(/[^a-zA-Z0-9._-]/g, "_");
  const outputPath = `${COMPARE_DIR}/${safeId}_m3.png`;
  console.log(`\n=== M3 on ${pid} ===`);
  const t0 = Date.now();
  try {
    const final: any = await buildGraphM3().invoke({ manifest: { ...m, outputPath, outputDir: COMPARE_DIR } });
    console.log(`  ${Date.now() - t0}ms, errors=${final.errors?.length ?? 0}`);
    if (fs.existsSync(outputPath)) {
      const c = await critique(outputPath, final.setting ?? m.visual, m.characters, m.shot);
      console.log(`  critic: ${c.score}/10, set=${c.settingMatch}, chr=${c.charactersMatch}, txt=${c.hasUnwantedText}, notes=${c.notes.slice(0, 100)}`);
    } else {
      console.log(`  ! output missing: ${outputPath}`);
    }
  } catch (e) {
    console.log(`  THREW: ${e instanceof Error ? e.message : String(e)}`);
  }
}
