/**
 * Method 3-3D: kami-mangaka-scene 3D render → gpt-image-2 / Gemini 3 Pro
 * Image edit pass → critique.
 *
 * P14 of ADR-2605141200. Different from `graph-m3.ts` (PEGEL with static
 * character refs): here the reference image is the headless 3D render
 * produced by `com.etzhayyim.mangaka.composeScene3d`. The 3D scene gives
 * the LLM diffusion model a concrete camera + character placement +
 * silhouette to copy, which (per ADR-0057) should amortise the upfront
 * VRM authoring cost over the rest of the series.
 *
 * Stages:
 *   1. fetchScene3d   — POST /xrpc/com.etzhayyim.mangaka.composeScene3d
 *                       against the lg-mangaka pod, pull best render PNG
 *                       to a temp file.
 *   2. editFromScene  — single `edit()` pass with the 3D PNG as the
 *                       only image reference; prompt asks for manga
 *                       inking + tone over the 3D silhouette.
 *   3. critique       — same 7-axis vision critic as graph-m2.
 *   4. fallback?      — when the 3D pod returns only `pending-*` blobs,
 *                       the graph emits an empty result + an error so
 *                       the caller can route to M2+ref.
 */

import { StateGraph, START, END, Annotation } from "@langchain/langgraph";
import * as fs from "node:fs";
import * as path from "node:path";
import { generate, edit, critique } from "./lib/openai.js";
import {
  composeScene3d,
  fetchBestRenderToFile,
  type ComposeScene3dOutput,
  type RenderEntry,
} from "./lib/compose-scene-3d-client.js";

const MAX_ITERS = Number(process.env.LG_M3_3D_MAX_ITERS ?? 2);
const ACCEPT_SCORE = Number(process.env.LG_M3_3D_ACCEPT_SCORE ?? 7);

export interface PanelManifestEntry {
  pageNum: number;
  panelId: string;
  panelRkey: string;       // resolved on the pod side as kind='panel'
  shot: string;
  visual: string;
  characters: string[];
  prompt: string;
  outputPath: string;
  outputDir: string;
}

export const StateAnnotation = Annotation.Root({
  manifest: Annotation<PanelManifestEntry>(),
  sceneOut: Annotation<ComposeScene3dOutput | null>({ default: () => null, reducer: (_, x) => x }),
  scenePath: Annotation<string | null>({ default: () => null, reducer: (_, x) => x }),
  iter: Annotation<number>({ default: () => 0, reducer: (_, x) => x }),
  currentPrompt: Annotation<string>({ default: () => "", reducer: (_, x) => x }),
  candidatePath: Annotation<string | null>({ default: () => null, reducer: (_, x) => x }),
  bestPath: Annotation<string | null>({ default: () => null, reducer: (_, x) => x }),
  bestScore: Annotation<number>({ default: () => 0, reducer: (_, x) => x }),
  errors: Annotation<string[]>({ default: () => [], reducer: (a, b) => [...a, ...b] }),
  durationMs: Annotation<Record<string, number>>({
    default: () => ({}),
    reducer: (a, b) => ({ ...a, ...b }),
  }),
});

export type State = typeof StateAnnotation.State;

/**
 * Build the user prompt the diffusion model sees. The 3D render carries
 * the spatial / camera information so the prompt focuses on style.
 */
export function buildScenePrompt(state: State): string {
  const m = state.manifest;
  const charSet = (m.characters ?? []).join(" / ") || "the focal character";
  const shot = m.shot || "MediumShot";
  return [
    `Render the attached 3D scene as a manga panel.`,
    `Shot: ${shot}. Characters: ${charSet}.`,
    `Style: high-contrast monochrome manga ink, clean silhouette lines from the 3D reference,`,
    `screentone shading for mid-values, dramatic chiaroscuro lighting,`,
    `professional sequential-art composition.`,
    `Beat: ${m.visual ?? m.prompt}.`,
    `Use the 3D reference for character placement, camera framing, and pose silhouettes —`,
    `do NOT reproduce its colour palette; convert to ink + tone.`,
    `Do NOT add any text, speech bubbles, captions, or sound effects.`,
  ].join("\n");
}

async function fetchScene3dNode(state: State): Promise<Partial<State>> {
  const t0 = Date.now();
  try {
    const sceneOut = await composeScene3d({
      panelRkey: state.manifest.panelRkey,
      maxIter: 3,
      renderAngles: 3,
    });
    if (sceneOut.error) {
      return {
        sceneOut,
        errors: [`composeScene3d error: ${sceneOut.error}`],
        durationMs: { fetchScene3d: Date.now() - t0 },
      };
    }
    const scenePath = await fetchBestRenderToFile(sceneOut, {
      tmpDir: state.manifest.outputDir,
      filenameHint: `m3-3d-ref_${state.manifest.panelId.replace(/[^a-z0-9._-]/gi, "_")}`,
    });
    if (!scenePath) {
      return {
        sceneOut,
        errors: ["composeScene3d returned only pending-* placeholders — falling back"],
        durationMs: { fetchScene3d: Date.now() - t0 },
      };
    }
    return {
      sceneOut,
      scenePath,
      currentPrompt: buildScenePrompt(state),
      durationMs: { fetchScene3d: Date.now() - t0 },
    };
  } catch (e) {
    return {
      errors: [`fetchScene3d threw: ${e instanceof Error ? e.message : String(e)}`],
      durationMs: { fetchScene3d: Date.now() - t0 },
    };
  }
}

async function editFromSceneNode(state: State): Promise<Partial<State>> {
  if (!state.scenePath) return { errors: ["editFromScene: no scenePath"] };
  const t0 = Date.now();
  const iter = state.iter + 1;
  try {
    const b64 = await edit(state.currentPrompt, [state.scenePath]);
    const candidatePath = path.join(
      state.manifest.outputDir,
      `${state.manifest.panelId.replace(/[^a-z0-9._-]/gi, "_")}_m3-3d_iter${iter}.png`,
    );
    fs.writeFileSync(candidatePath, Buffer.from(b64, "base64"));
    return {
      iter,
      candidatePath,
      durationMs: { [`edit_iter${iter}`]: Date.now() - t0 },
    };
  } catch (e) {
    return {
      iter,
      errors: [`editFromScene threw: ${e instanceof Error ? e.message : String(e)}`],
      durationMs: { [`edit_iter${iter}`]: Date.now() - t0 },
    };
  }
}

async function critiqueNode(state: State): Promise<Partial<State>> {
  if (!state.candidatePath) return {};
  const t0 = Date.now();
  try {
    const m = state.manifest;
    const c = await critique(state.candidatePath, m.visual, m.characters, m.shot);
    const candidateScore = Number(c.score) || 0;
    const bestScore = candidateScore > state.bestScore ? candidateScore : state.bestScore;
    const bestPath = candidateScore > state.bestScore ? state.candidatePath : state.bestPath;
    return {
      bestScore,
      bestPath,
      durationMs: { [`critique_iter${state.iter}`]: Date.now() - t0 },
    };
  } catch (e) {
    return {
      errors: [`critique threw: ${e instanceof Error ? e.message : String(e)}`],
      durationMs: { [`critique_iter${state.iter}`]: Date.now() - t0 },
    };
  }
}

function routeAfterCritique(state: State): "editFromScene" | typeof END {
  if (state.bestScore >= ACCEPT_SCORE) return END;
  if (state.iter >= MAX_ITERS) return END;
  return "editFromScene";
}

export function buildGraphM3_3D() {
  const g = new StateGraph(StateAnnotation)
    .addNode("fetchScene3d", fetchScene3dNode)
    .addNode("editFromScene", editFromSceneNode)
    .addNode("critique", critiqueNode)
    .addEdge(START, "fetchScene3d")
    .addEdge("fetchScene3d", "editFromScene")
    .addEdge("editFromScene", "critique")
    .addConditionalEdges("critique", routeAfterCritique, {
      editFromScene: "editFromScene",
      [END]: END,
    });
  return g.compile();
}
