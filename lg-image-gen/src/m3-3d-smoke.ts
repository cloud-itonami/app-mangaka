/**
 * Smoke runner for the M3-3D hybrid pipeline (P14 of ADR-2605141200).
 *
 *   npx tsx src/m3-3d-smoke.ts
 *
 * Pulls a few panel manifests from arc0-1-origin and exercises the M3-3D
 * graph end-to-end. Mirrors `m3-only.ts` so the η-score race in
 * `quality-compare-3d.ts` can drop in a fair side-by-side against M2+ref.
 *
 * Env required:
 *   OPENAI_API_KEY                — diffusion edit pass + vision critic.
 *   LG_MANGAKA_BASE               — pod address (defaults to the in-cluster DNS).
 *   B2_PUBLIC_BASE                — public-read base URL for blob fetches.
 *   LG_API_KEY (optional)         — when the pod enforces x-api-key.
 *
 * Skips panels whose 3D render isn't ready (returns only pending-*
 * placeholder blob keys) — typical when the lg-mangaka pod hasn't yet
 * been built with the P11 kami wheel.
 */

import * as fs from "node:fs";
import { buildGraphM3_3D } from "./graph-m3-3d.js";
import { critique } from "./lib/openai.js";

const REPO =
  process.env.GH_REPO_DIR ?? "/Users/junkawasaki/github/ghosthacker/260123-jump";
const COMPARE_DIR = `${REPO}/resources/episodes/arc0-1-origin/compare-runs`;
const MANIFEST_PATH = `${REPO}/resources/episodes/arc0-1-origin/image-gen-manifest.json`;

async function main() {
  if (!fs.existsSync(MANIFEST_PATH)) {
    console.error(`manifest not found: ${MANIFEST_PATH}`);
    process.exit(1);
  }
  const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf-8"));
  fs.mkdirSync(COMPARE_DIR, { recursive: true });

  // 2 panels — enough to spot trends, not so many that one failing pod
  // call eats the whole smoke budget.
  const targets = ["panel:p1n1-v2", "panel:p1n5-v2"];

  for (const pid of targets) {
    const m = manifest.panels.find((p: any) => p.panelId === pid);
    if (!m) {
      console.log(`! skip ${pid}: not in manifest`);
      continue;
    }
    const safeId = pid.replace(/[^a-zA-Z0-9._-]/g, "_");
    const outputPath = `${COMPARE_DIR}/${safeId}_m3-3d.png`;
    console.log(`\n=== M3-3D on ${pid} ===`);
    const t0 = Date.now();
    try {
      const final: any = await buildGraphM3_3D().invoke({
        manifest: { ...m, panelRkey: m.panelId, outputPath, outputDir: COMPARE_DIR },
      });
      console.log(
        `  ${Date.now() - t0}ms, errors=${final.errors?.length ?? 0}, bestScore=${final.bestScore ?? 0}`,
      );
      if (final.errors?.length) {
        for (const e of final.errors.slice(0, 3)) console.log(`    err: ${e}`);
      }
      if (final.bestPath && fs.existsSync(final.bestPath)) {
        fs.copyFileSync(final.bestPath, outputPath);
        const c = await critique(outputPath, m.visual, m.characters, m.shot);
        console.log(
          `  critic: ${c.score}/10, set=${c.settingMatch}, chr=${c.charactersMatch}, txt=${c.hasUnwantedText}, notes=${c.notes.slice(0, 100)}`,
        );
      } else {
        console.log(`  ! no best render — M3-3D likely fell back (pending-* renders)`);
      }
    } catch (e) {
      console.log(`  THREW: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
