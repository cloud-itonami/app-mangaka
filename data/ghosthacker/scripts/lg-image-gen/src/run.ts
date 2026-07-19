/**
 * Orchestrator: read manifest → invoke LangGraph per panel → merge results into episode.jsonld.
 *
 * Usage:
 *   OPENROUTER_API_KEY=... npx tsx src/run.ts                    # generate all panels
 *   OPENROUTER_API_KEY=... npx tsx src/run.ts --panel-id panel:p21n1-v2  # single panel
 *   OPENROUTER_API_KEY=... npx tsx src/run.ts --page 1           # all panels on page 1
 *   OPENROUTER_API_KEY=... npx tsx src/run.ts --limit 3          # only first 3 panels
 *   OPENROUTER_API_KEY=... npx tsx src/run.ts --dry-run          # build prompts, skip API
 *
 * After successful generation, episode.jsonld panel entries are updated:
 *   - gh:generatedImageUrl: <relative URL>
 *   - gh:currentImageIndex: <new index>
 *   - gh:generatedImages: <array gets new entry with prompt + model + duration>
 *   - gh:needsImageGeneration: removed
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { buildGraph, type PanelManifestEntry, type PanelState } from "./graph.js";
import { buildGraph3Stage, type PanelState as PanelState3 } from "./graph-3stage.js";
import { buildGraphM2 } from "./graph-m2.js";
import { computeQp, computeQi, combineQ, type RichCritique } from "./lib/openai.js";

const REPO = "/Users/junkawasaki/github/ghosthacker/260123-jump";
const MANIFEST_PATH = `${REPO}/resources/episodes/arc0-1-origin/image-gen-manifest.json`;
const EPISODE_PATH = `${REPO}/resources/episodes/arc0-1-origin/episode.jsonld`;
const PROVIDER = (process.env.IMAGE_PROVIDER ?? "openai").toLowerCase();
const MODEL = process.env.LG_IMAGE_MODEL ?? (PROVIDER === "openrouter" ? "google/gemini-3-pro-image-preview" : "gpt-image-2");
const QUALITY = process.env.LG_IMAGE_QUALITY ?? "low";

interface CliArgs {
  panelId?: string;
  page?: number;
  limit?: number;
  dryRun: boolean;
  delayMs: number;
  onlyPending: boolean;
  pipeline: "1-stage" | "3-stage" | "m2ref";
  noRef: boolean;
}

function parseArgs(): CliArgs {
  const args = process.argv.slice(2);
  const out: CliArgs = { dryRun: false, delayMs: 1500, onlyPending: false, pipeline: "1-stage", noRef: false };
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--panel-id" && args[i + 1]) out.panelId = args[++i];
    else if (args[i] === "--page" && args[i + 1]) out.page = Number(args[++i]);
    else if (args[i] === "--limit" && args[i + 1]) out.limit = Number(args[++i]);
    else if (args[i] === "--dry-run") out.dryRun = true;
    else if (args[i] === "--only-pending") out.onlyPending = true;
    else if (args[i] === "--no-ref") out.noRef = true;
    else if (args[i] === "--delay-ms" && args[i + 1]) out.delayMs = Number(args[++i]);
    else if (args[i] === "--pipeline" && args[i + 1]) out.pipeline = args[++i] as "1-stage" | "3-stage" | "m2ref";
  }
  return out;
}

function loadManifest(): { panels: PanelManifestEntry[] } {
  return JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf-8"));
}

function loadEpisode(): any {
  return JSON.parse(fs.readFileSync(EPISODE_PATH, "utf-8"));
}

function saveEpisode(ep: any) {
  fs.writeFileSync(EPISODE_PATH, JSON.stringify(ep, null, 2) + "\n");
}

function updateEpisodePanel(ep: any, manifest: PanelManifestEntry, finalState: any, pipeline: "1-stage" | "3-stage" | "m2ref") {
  const page = ep["gh:pages"].find((p: any) => p["gh:pageNumber"] === manifest.pageNum);
  if (!page) return false;
  const panel = (page["gh:panels"] ?? []).find((pn: any) => pn["@id"] === manifest.panelId);
  if (!panel) return false;

  let stages: { name: string; durationMs: number }[];
  let totalDur: number;
  let promptUsed: string;
  let pipelineLabel: string;
  let resolvedRefs: any = manifest.referenceSelections;

  if (pipeline === "3-stage") {
    const s = finalState as PanelState3;
    stages = [
      { name: "buildPrompt",        durationMs: s.durationMs.build },
      { name: "pickReferences",     durationMs: s.durationMs.pickRef },
      { name: "generateBackground", durationMs: s.durationMs.bg },
      { name: "compositeCharacters",durationMs: s.durationMs.composite },
      { name: "persistImage",       durationMs: s.durationMs.persist },
    ];
    totalDur = stages.reduce((a, x) => a + x.durationMs, 0);
    promptUsed = `BG: ${s.bgPrompt}\n\nCOMPOSITE: ${s.compositePrompt}`;
    pipelineLabel = "langgraph-ts-3stage-bg-composite";
    resolvedRefs = s.resolvedReferences.map((r) => ({ character: r.character, variant: r.variant, refPath: r.refPath }));
  } else if (pipeline === "m2ref") {
    const d = finalState.durationMs ?? {};
    stages = Object.entries(d).map(([name, durationMs]) => ({ name, durationMs: durationMs as number }));
    totalDur = stages.reduce((a, x) => a + x.durationMs, 0);
    promptUsed = finalState.currentPrompt ?? "";
    pipelineLabel = `langgraph-ts-m2ref-agent-loop (iter=${finalState.iter ?? 1}, score=${finalState.bestScore ?? 0})`;
    resolvedRefs = (finalState.resolvedRefs ?? []).map((r: any) => ({ character: r.character, variant: r.variant, refPath: r.refPath }));
  } else {
    const s = finalState as PanelState;
    stages = [
      { name: "buildPrompt",  durationMs: s.durationMs.build },
      { name: "generateImage",durationMs: s.durationMs.generate },
      { name: "persistImage", durationMs: s.durationMs.persist },
    ];
    totalDur = stages.reduce((a, x) => a + x.durationMs, 0);
    promptUsed = s.refinedPrompt;
    pipelineLabel = "langgraph-ts-buildPrompt-generate-persist";
  }

  const newImage = {
    "gh:imageUrl": finalState.outputRelUrl,
    "gh:imagePrompt": promptUsed,
    "gh:generatedAt": Math.floor(Date.now() / 1000),
    "gh:model": MODEL,
    "gh:quality": QUALITY,
    "gh:durationMs": totalDur,
    "gh:generationPipeline": pipelineLabel,
    "gh:generationStages": stages,
    "gh:referenceCharacters": manifest.referenceCharacters,
    "gh:referenceSelections": resolvedRefs,
  };

  if (!panel["gh:generatedImages"]) panel["gh:generatedImages"] = [];
  panel["gh:generatedImages"].push(newImage);
  panel["gh:currentImageIndex"] = panel["gh:generatedImages"].length - 1;
  panel["gh:generatedImageUrl"] = finalState.outputRelUrl;
  panel["gh:imagePrompt"] = promptUsed;
  delete panel["gh:needsImageGeneration"];
  return true;
}

async function main() {
  const cli = parseArgs();
  const manifest = loadManifest();
  let panels = manifest.panels;
  if (cli.onlyPending) {
    const ep = loadEpisode();
    const pending = new Set<string>();
    for (const pg of ep["gh:pages"] ?? []) {
      for (const pn of pg["gh:panels"] ?? []) {
        if (pn["gh:needsImageGeneration"]) pending.add(pn["@id"]);
      }
    }
    panels = panels.filter((p) => pending.has(p.panelId));
    console.log(`--only-pending: ${panels.length} panel(s) still need image generation.`);
  }
  if (cli.panelId) panels = panels.filter((p) => p.panelId === cli.panelId);
  if (cli.page !== undefined) panels = panels.filter((p) => p.pageNum === cli.page);
  if (cli.limit !== undefined) panels = panels.slice(0, cli.limit);

  if (panels.length === 0) {
    console.log("No panels match filter. Exiting.");
    return;
  }

  const provider = (process.env.IMAGE_PROVIDER ?? "openai").toLowerCase();
  const requiredKey = provider === "openrouter" ? "OPENROUTER_API_KEY" : "OPENAI_API_KEY";
  if (!cli.dryRun && !process.env[requiredKey]) {
    console.error(`ERROR: ${requiredKey} env var is not set. Set it or pass --dry-run.`);
    process.exit(1);
  }

  console.log(`LangGraph image gen — ${panels.length} panel(s) to process${cli.dryRun ? " [DRY RUN]" : ""}`);
  console.log(`Provider: ${provider} / Model: ${MODEL} / Quality: ${QUALITY} / Pipeline: ${cli.pipeline}`);

  const graph =
    cli.pipeline === "3-stage" ? buildGraph3Stage() :
    cli.pipeline === "m2ref"   ? buildGraphM2() :
                                  buildGraph();
  const results: Array<{ panelId: string; pageNum: number; ok: boolean; error?: string; durationMs?: number; outputUrl?: string }> = [];
  const ep = loadEpisode();

  for (let i = 0; i < panels.length; i++) {
    const m = panels[i];
    const tag = `[${i + 1}/${panels.length}] p${m.pageNum} ${m.panelId}`;
    process.stdout.write(`${tag} → `);

    if (cli.dryRun) {
      // Just exercise buildPrompt by invoking with a noop generateImage — emulate a partial run
      const mockState: PanelState = {
        manifest: m,
        refinedPrompt: m.prompt,
        generatedDataUrl: null,
        outputAbsPath: null,
        outputRelUrl: null,
        errors: [],
        durationMs: { build: 0, generate: 0, persist: 0 },
      };
      console.log(`prompt-len=${m.prompt.length} → would write to ${m.outputPath.replace(REPO, ".")}`);
      results.push({ panelId: m.panelId, pageNum: m.pageNum, ok: true, durationMs: 0 });
      continue;
    }

    // Determine versioned output path
    const targetPage = ep["gh:pages"].find((p: any) => p["gh:pageNumber"] === m.pageNum);
    const targetPanel = targetPage?.["gh:panels"]?.find((pn: any) => pn["@id"] === m.panelId);
    const existingCount = targetPanel?.["gh:generatedImages"]?.length ?? 0;
    const versionedOutputPath = m.outputPath.replace(/_v\d+\.png$|_sketch_v\d+\.png$|\.png$/, `_v${existingCount + 1}.png`);
    const versionedManifest: any = { ...m, outputPath: versionedOutputPath };
    if (cli.noRef) {
      // Strip references entirely: planNode resolves refs from focusedCharacters OR allCharacters fallback,
      // so we must clear both to truly disable ref-image injection.
      versionedManifest.focusedCharacters = [];
      versionedManifest.referenceCharacters = [];
      versionedManifest.allCharacters = [];  // disable fallback ref resolution
      versionedManifest.characters = [];     // also clear legacy field
      versionedManifest._noRefAllCharsBackup = m.allCharacters; // preserve for description if needed
    }

    try {
      const final = (await graph.invoke({ manifest: versionedManifest })) as PanelState | PanelState3;
      if (final.errors.length > 0) {
        console.log(`FAIL: ${final.errors.join(" | ")}`);
        results.push({ panelId: m.panelId, pageNum: m.pageNum, ok: false, error: final.errors.join(" | ") });
      } else {
        const merged = updateEpisodePanel(ep, versionedManifest, final, cli.pipeline);
        const total = Object.values((final as any).durationMs).reduce((a: number, b: any) => a + (Number(b) || 0), 0);

        // Q-score (only meaningful if critique was run, i.e., m2ref pipeline)
        let qLine = "";
        const critique = (final as any).lastCritique as RichCritique | null;
        if (critique) {
          const { Q_p } = computeQp(versionedManifest);
          const { Q_i } = computeQi(critique, m.props ?? []);
          const Q_total = combineQ(Q_p, Q_i);
          const tier = Q_total >= 0.75 ? "ship" : Q_total >= 0.55 ? "review" : "regen";
          qLine = ` Q_p=${Q_p.toFixed(2)} Q_i=${Q_i.toFixed(2)} Q_total=${Q_total.toFixed(2)} [${tier}]`;
          // Persist Q-score on the panel's latest image entry
          const page = ep["gh:pages"].find((p: any) => p["gh:pageNumber"] === m.pageNum);
          const panel = page?.["gh:panels"]?.find((pn: any) => pn["@id"] === m.panelId);
          const lastImg = panel?.["gh:generatedImages"]?.[panel["gh:generatedImages"].length - 1];
          if (lastImg) {
            lastImg["gh:Q_p"] = Q_p;
            lastImg["gh:Q_i"] = Q_i;
            lastImg["gh:Q_total"] = Q_total;
            lastImg["gh:Q_tier"] = tier;
          }
        }

        console.log(`OK ${total}ms → ${final.outputRelUrl} ${merged ? "[merged]" : "[merge-skipped]"}${qLine}`);
        results.push({ panelId: m.panelId, pageNum: m.pageNum, ok: true, durationMs: total, outputUrl: final.outputRelUrl ?? undefined });
        saveEpisode(ep);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.log(`THROW: ${msg}`);
      results.push({ panelId: m.panelId, pageNum: m.pageNum, ok: false, error: msg });
    }

    // Rate-limit between calls
    if (i < panels.length - 1 && cli.delayMs > 0) await new Promise((r) => setTimeout(r, cli.delayMs));
  }

  // Final summary
  const ok = results.filter((r) => r.ok).length;
  const fail = results.length - ok;
  const totalMs = results.reduce((s, r) => s + (r.durationMs ?? 0), 0);
  console.log(`\n=== Summary ===`);
  console.log(`OK:    ${ok}/${results.length}`);
  console.log(`FAIL:  ${fail}/${results.length}`);
  console.log(`Total time: ${(totalMs / 1000).toFixed(1)}s`);
  if (fail > 0) {
    console.log(`\nFailures:`);
    for (const r of results.filter((x) => !x.ok)) console.log(`  - p${r.pageNum} ${r.panelId}: ${r.error}`);
  }

  // Write run report
  const reportPath = `${REPO}/resources/episodes/arc0-1-origin/lg-image-gen-runs/run-${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
  fs.mkdirSync(path.dirname(reportPath), { recursive: true });
  fs.writeFileSync(reportPath, JSON.stringify({ model: MODEL, dryRun: cli.dryRun, panelsProcessed: results.length, ok, fail, totalMs, results }, null, 2));
  console.log(`Report: ${reportPath}`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
