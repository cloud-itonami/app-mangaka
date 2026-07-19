import * as fs from "node:fs";
import { buildGraphM2 } from "./graph-m2.js";
import { critique } from "./lib/openai.js";

const REPO = "/Users/junkawasaki/github/ghosthacker/260123-jump";
const COMPARE_DIR = `${REPO}/resources/episodes/arc0-1-origin/compare-runs`;
const manifest = JSON.parse(fs.readFileSync(`${REPO}/resources/episodes/arc0-1-origin/image-gen-manifest.json`, "utf-8"));

for (const pid of ["panel:p1n1-v2", "panel:p1n5-v2"]) {
  const m = manifest.panels.find((p: any) => p.panelId === pid);
  const safeId = pid.replace(/[^a-zA-Z0-9._-]/g, "_");
  const outputPath = `${COMPARE_DIR}/${safeId}_m2ref.png`;
  console.log(`\n=== M2+ref on ${pid} ===`);
  const t0 = Date.now();
  try {
    const final: any = await buildGraphM2().invoke({ manifest: { ...m, outputPath, outputDir: COMPARE_DIR } });
    console.log(`  ${Date.now() - t0}ms, iter=${final.iter}, errors=${final.errors?.length ?? 0}`);
    if (final.iterLog?.length) {
      for (const it of final.iterLog) console.log(`    iter ${it.iter}: score ${it.score} — ${it.notes.slice(0, 80)}`);
    }
    if (fs.existsSync(outputPath)) {
      const c = await critique(outputPath, final.setting ?? m.visual, m.characters, m.shot);
      console.log(`  final critic: ${c.score}/10, set=${c.settingMatch}, chr=${c.charactersMatch}, txt=${c.hasUnwantedText}`);
      console.log(`    notes: ${c.notes.slice(0, 150)}`);
    }
  } catch (e) {
    console.log(`  THREW: ${e instanceof Error ? e.message : String(e)}`);
  }
}
