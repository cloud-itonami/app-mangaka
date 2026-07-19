/**
 * 3-method comparison runner.
 *
 * Runs Method 1 (graph-m1), Method 2 (graph-m2), Method 3 (graph-m3) on the same panel(s)
 * to compare output stability. Writes outputs to compare-runs/{panelId}-{method}.png
 * and a single comparison report JSON.
 *
 * Usage:
 *   OPENAI_API_KEY=... npx tsx src/compare.ts panel:p1n1-v2 panel:p1n5-v2
 *
 * Does NOT modify episode.jsonld (these are experimental).
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { buildGraphM1 } from "./graph-m1.js";
import { buildGraphM2 } from "./graph-m2.js";
import { buildGraphM3 } from "./graph-m3.js";
import { critique, MODEL, QUALITY } from "./lib/openai.js";

const REPO = "/Users/junkawasaki/github/ghosthacker/260123-jump";
const MANIFEST_PATH = `${REPO}/resources/episodes/arc0-1-origin/image-gen-manifest.json`;
const COMPARE_DIR = `${REPO}/resources/episodes/arc0-1-origin/compare-runs`;

if (!process.env.OPENAI_API_KEY) {
  console.error("ERROR: OPENAI_API_KEY not set.");
  process.exit(1);
}

const targetPanelIds = process.argv.slice(2);
if (targetPanelIds.length === 0) {
  console.error("Usage: npx tsx src/compare.ts panel:p1n1-v2 [panel:p1n5-v2 ...]");
  process.exit(1);
}

fs.mkdirSync(COMPARE_DIR, { recursive: true });
const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf-8"));

interface MethodResult {
  method: "m1" | "m2" | "m3";
  panelId: string;
  outputPath: string;
  durationMs: Record<string, number>;
  totalMs: number;
  errors: string[];
  critic: { score: number; settingMatch: boolean; charactersMatch: boolean; hasUnwantedText: boolean; notes: string } | null;
  iterCount?: number; // for m2
}

async function runOne(panelId: string): Promise<MethodResult[]> {
  const m = manifest.panels.find((p: any) => p.panelId === panelId);
  if (!m) throw new Error(`Panel ${panelId} not in manifest`);
  const safeId = panelId.replace(/[^a-zA-Z0-9._-]/g, "_");
  const results: MethodResult[] = [];

  for (const method of ["m1", "m2", "m3"] as const) {
    const outputPath = `${COMPARE_DIR}/${safeId}_${method}.png`;
    const altManifest = { ...m, outputPath, outputDir: COMPARE_DIR };
    console.log(`\n=== ${method.toUpperCase()} on ${panelId} ===`);
    const t0 = Date.now();
    try {
      let final: any;
      if (method === "m1") final = await buildGraphM1().invoke({ manifest: altManifest });
      else if (method === "m2") final = await buildGraphM2().invoke({ manifest: altManifest });
      else final = await buildGraphM3().invoke({ manifest: altManifest });
      const totalMs = Date.now() - t0;
      const durationMs: Record<string, number> = final.durationMs ?? {};

      let critic = null;
      if (fs.existsSync(outputPath)) {
        try {
          critic = await critique(outputPath, final.setting ?? m.visual, m.characters, m.shot);
          console.log(`  → critic: score ${critic.score}/10, setting=${critic.settingMatch}, chars=${critic.charactersMatch}, text=${critic.hasUnwantedText}`);
        } catch (e) {
          console.log(`  → critic FAILED: ${e instanceof Error ? e.message : String(e)}`);
        }
      }

      results.push({
        method,
        panelId,
        outputPath,
        durationMs,
        totalMs,
        errors: final.errors ?? [],
        critic,
        ...(method === "m2" ? { iterCount: final.iter ?? 0 } : {}),
      });
      console.log(`  ${method} OK ${totalMs}ms${final.errors?.length ? " (with " + final.errors.length + " soft errors)" : ""}`);
    } catch (e) {
      console.log(`  ${method} THREW: ${e instanceof Error ? e.message : String(e)}`);
      results.push({
        method,
        panelId,
        outputPath,
        durationMs: {},
        totalMs: Date.now() - t0,
        errors: [String(e)],
        critic: null,
      });
    }
  }
  return results;
}

async function main() {
  console.log(`Comparing methods on ${targetPanelIds.length} panel(s).`);
  console.log(`Model: ${MODEL} / Quality: ${QUALITY}`);
  console.log(`Output dir: ${COMPARE_DIR}`);

  const allResults: MethodResult[] = [];
  for (const pid of targetPanelIds) {
    const r = await runOne(pid);
    allResults.push(...r);
  }

  // Build comparison report
  const reportPath = `${COMPARE_DIR}/report-${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
  const report = {
    runAt: new Date().toISOString(),
    model: MODEL,
    quality: QUALITY,
    panels: targetPanelIds,
    results: allResults,
    summary: {
      m1: summarize(allResults.filter((r) => r.method === "m1")),
      m2: summarize(allResults.filter((r) => r.method === "m2")),
      m3: summarize(allResults.filter((r) => r.method === "m3")),
    },
  };
  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));

  // Print table
  console.log(`\n=== Comparison Report ===`);
  console.log(`${"Panel".padEnd(22)} ${"M".padEnd(3)} ${"Time".padEnd(8)} ${"Score".padEnd(7)} ${"Set".padEnd(4)} ${"Chr".padEnd(4)} ${"Txt".padEnd(4)} Notes`);
  for (const r of allResults) {
    const s = r.critic;
    const tm = `${(r.totalMs / 1000).toFixed(0)}s`;
    const sc = s ? `${s.score}/10` : "-";
    const set = s ? (s.settingMatch ? "✓" : "✗") : "-";
    const chr = s ? (s.charactersMatch ? "✓" : "✗") : "-";
    const txt = s ? (s.hasUnwantedText ? "✗" : "✓") : "-";
    console.log(`${r.panelId.padEnd(22)} ${r.method.padEnd(3)} ${tm.padEnd(8)} ${sc.padEnd(7)} ${set.padEnd(4)} ${chr.padEnd(4)} ${txt.padEnd(4)} ${(s?.notes ?? "").slice(0, 60)}`);
  }
  console.log(`\nReport: ${reportPath}`);

  // Print summary table
  console.log(`\n=== Summary by Method ===`);
  for (const m of ["m1", "m2", "m3"] as const) {
    const s = report.summary[m];
    console.log(`${m}: avg score ${s.avgScore.toFixed(1)} / setting OK ${s.settingOK}/${s.n} / chars OK ${s.charsOK}/${s.n} / text-clean ${s.textClean}/${s.n} / avg ${(s.avgMs / 1000).toFixed(0)}s`);
  }
}

function summarize(rs: MethodResult[]) {
  const n = rs.length;
  const scored = rs.filter((r) => r.critic);
  const avgScore = scored.length > 0 ? scored.reduce((a, r) => a + (r.critic!.score ?? 0), 0) / scored.length : 0;
  const settingOK = scored.filter((r) => r.critic!.settingMatch).length;
  const charsOK = scored.filter((r) => r.critic!.charactersMatch).length;
  const textClean = scored.filter((r) => !r.critic!.hasUnwantedText).length;
  const avgMs = n > 0 ? rs.reduce((a, r) => a + r.totalMs, 0) / n : 0;
  return { n, avgScore, settingOK, charsOK, textClean, avgMs };
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
