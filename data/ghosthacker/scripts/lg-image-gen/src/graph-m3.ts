/**
 * Method 3: 3D-proxy = キャラ ref を「3D アセット」として直貼り (PEGEL: Plan-Execute-Eval)
 * Pattern: PEGEL (plan → parallel execute → mechanical assembly → evaluate)
 *
 * Stages:
 *   1. plan         — list assets needed: BG location + each char ref variant
 *   2. execute      — parallel: genBG (API) + selectRefs (file lookup, no API)
 *   3. assemble     — sharp mechanical paste of refs onto BG (NO API call)
 *   4. harmonize    — single /v1/images/edits pass to blend (optional)
 *   5. eval         — vision critic single check
 *   6. (no replan) — accept and ship; replan would mean rerunning plan with adjusted assets
 */
import { StateGraph, START, END, Annotation } from "@langchain/langgraph";
import * as fs from "node:fs";
import * as path from "node:path";
import sharp from "sharp";
import { generate, edit, critique } from "./lib/openai.js";
import { pickVariant, refPath, extractSetting, type Variant } from "./lib/refs.js";

export interface PanelManifestEntry {
  pageNum: number;
  panelId: string;
  panelIndex: number;
  pageTitle: string;
  shot: string;
  visual: string;
  characters: string[];
  dialogues: { speaker: string; text: string; emotion?: string }[];
  prompt: string;
  outputPath: string;
  outputDir: string;
  referenceCharacters: string[];
  referenceSelections: { character: string; variant: string; note: string }[];
}

type AssetPlan = {
  bg: { prompt: string; outPath: string };
  refs: { character: string; variant: Variant; refPath: string }[];
};

export const StateAnnotation = Annotation.Root({
  manifest: Annotation<PanelManifestEntry>(),
  setting: Annotation<string>(),
  visualNote: Annotation<string>(),
  assetPlan: Annotation<AssetPlan | null>({ default: () => null, reducer: (_, x) => x }),
  bgPath: Annotation<string | null>({ default: () => null, reducer: (_, x) => x }),
  assemblyPath: Annotation<string | null>({ default: () => null, reducer: (_, x) => x }),
  outputRelUrl: Annotation<string | null>({ default: () => null, reducer: (_, x) => x }),
  evalScore: Annotation<number>({ default: () => 0, reducer: (_, x) => x }),
  evalNotes: Annotation<string>({ default: () => "", reducer: (_, x) => x }),
  errors: Annotation<string[]>({ default: () => [], reducer: (a, b) => [...a, ...b] }),
  durationMs: Annotation<Record<string, number>>({ default: () => ({}), reducer: (a, b) => ({ ...a, ...b }) }),
});
export type State = typeof StateAnnotation.State;

async function planNode(state: State): Promise<Partial<State>> {
  const t0 = Date.now();
  const m = state.manifest;
  const { setting, visualNote } = extractSetting(m.prompt);
  const refs = m.characters
    .map((c) => {
      const v = pickVariant(c, m.dialogues, m.shot);
      const rp = refPath(c, v);
      return rp ? { character: c, variant: v, refPath: rp } : null;
    })
    .filter((x): x is { character: string; variant: Variant; refPath: string } => x !== null);

  const bgPrompt = [
    "Cinematic illustration of an empty location.",
    setting ? `LOCATION: ${setting}.` : "",
    visualNote ? `Set dressing: ${visualNote}.` : "",
    `Atmosphere: ${m.visual}.`.replace(/[人キャラ]/g, ""),
    `Shot framing: ${m.shot}.`,
    "ABSOLUTE: no people, no characters, no humans. Empty location only.",
    "Monochrome black-and-white anime line style with screen tones. NO text, NO labels, NO storyboard frames.",
  ].filter(Boolean).join(" ");

  const bgOut = m.outputPath.replace(/\.png$/, "_m3_bg.png");
  return {
    setting, visualNote,
    assetPlan: { bg: { prompt: bgPrompt, outPath: bgOut }, refs },
    durationMs: { plan: Date.now() - t0 },
  };
}

async function executeNode(state: State): Promise<Partial<State>> {
  const t0 = Date.now();
  if (!state.assetPlan) return { errors: ["no plan"] };
  try {
    const b64 = await generate(state.assetPlan.bg.prompt);
    fs.mkdirSync(path.dirname(state.assetPlan.bg.outPath), { recursive: true });
    fs.writeFileSync(state.assetPlan.bg.outPath, Buffer.from(b64, "base64"));
    return { bgPath: state.assetPlan.bg.outPath, durationMs: { execute: Date.now() - t0 } };
  } catch (e) {
    return { errors: [`execute: ${e instanceof Error ? e.message : String(e)}`], durationMs: { execute: Date.now() - t0 } };
  }
}

async function assembleNode(state: State): Promise<Partial<State>> {
  const t0 = Date.now();
  if (!state.bgPath || !state.assetPlan) return { errors: ["missing bg or plan"] };
  try {
    const bgMeta = await sharp(state.bgPath).metadata();
    const W = bgMeta.width ?? 1024, H = bgMeta.height ?? 1536;
    const refs = state.assetPlan.refs;
    const layers: any[] = [];
    for (let i = 0; i < refs.length; i++) {
      // White-to-alpha for the reference
      const charBuf = await sharp(refs[i].refPath)
        .ensureAlpha()
        .raw()
        .toBuffer({ resolveWithObject: true });
      const { data, info } = charBuf;
      const out = Buffer.from(data);
      for (let p = 0; p < info.width * info.height; p++) {
        const r = out[p * info.channels];
        const g = out[p * info.channels + 1];
        const b = out[p * info.channels + 2];
        if (r > 245 && g > 245 && b > 245) out[p * info.channels + 3] = 0;
      }
      // Reference variants are face-only, so render at moderate size (40% of BG height)
      const charH = Math.floor(H * 0.55);
      const charImg = await sharp(out, { raw: info }).resize({ height: charH }).png().toBuffer();
      const charMeta = await sharp(charImg).metadata();
      const charW = charMeta.width ?? Math.floor(W * 0.3);
      const xOff = refs.length === 1
        ? Math.floor((W - charW) / 2)
        : Math.floor(W * (0.2 + 0.55 * i / Math.max(1, refs.length - 1)));
      const yOff = Math.floor(H * 0.35); // upper-center, leave room for body if needed
      layers.push({ input: charImg, top: yOff, left: Math.max(0, Math.min(W - charW, xOff)) });
    }
    const assemblyPath = state.manifest.outputPath.replace(/\.png$/, "_m3_assembled.png");
    await sharp(state.bgPath).composite(layers).toFile(assemblyPath);
    return { assemblyPath, durationMs: { assemble: Date.now() - t0 } };
  } catch (e) {
    return { errors: [`assemble: ${e instanceof Error ? e.message : String(e)}`], durationMs: { assemble: Date.now() - t0 } };
  }
}

async function harmonizeNode(state: State): Promise<Partial<State>> {
  const t0 = Date.now();
  if (!state.assemblyPath) return { errors: ["no assembly"] };
  if (process.env.LG_M3_SKIP_HARMONIZE === "1") {
    fs.copyFileSync(state.assemblyPath, state.manifest.outputPath);
    const idx = state.manifest.outputPath.indexOf("/resources/");
    return { outputRelUrl: idx >= 0 ? state.manifest.outputPath.slice(idx + "/resources".length) : state.manifest.outputPath, durationMs: { harmonize: Date.now() - t0 } };
  }
  try {
    const m = state.manifest;
    const prompt = [
      "Treat this image as a 3D render placed against a 2D background.",
      "Re-illustrate it as a single cohesive manga panel: harmonize the line weight, screen-tone density, and lighting between characters and background.",
      "ABSOLUTE: do NOT change the location, do NOT change the characters' faces (preserve identity exactly), do NOT change the composition.",
      `Apply scene-appropriate clothing (Japanese middle-school uniform) and the pose described: ${m.visual}.`,
      "Monochrome black-and-white. NO text, NO speech bubbles, NO captions, NO labels.",
    ].join(" ");
    const b64 = await edit(prompt, [state.assemblyPath]);
    fs.writeFileSync(state.manifest.outputPath, Buffer.from(b64, "base64"));
    const idx = state.manifest.outputPath.indexOf("/resources/");
    return { outputRelUrl: idx >= 0 ? state.manifest.outputPath.slice(idx + "/resources".length) : state.manifest.outputPath, durationMs: { harmonize: Date.now() - t0 } };
  } catch (e) {
    fs.copyFileSync(state.assemblyPath, state.manifest.outputPath);
    const idx = state.manifest.outputPath.indexOf("/resources/");
    return { outputRelUrl: idx >= 0 ? state.manifest.outputPath.slice(idx + "/resources".length) : state.manifest.outputPath, errors: [`harmonize-soft-fail: ${e instanceof Error ? e.message : String(e)}`], durationMs: { harmonize: Date.now() - t0 } };
  }
}

async function evalNode(state: State): Promise<Partial<State>> {
  const t0 = Date.now();
  const target = state.manifest.outputPath;
  if (!fs.existsSync(target)) return { errors: ["no output to eval"] };
  if (process.env.LG_M3_SKIP_EVAL === "1") {
    return { evalScore: 0, evalNotes: "skipped", durationMs: { eval: Date.now() - t0 } };
  }
  try {
    const c = await critique(target, state.setting || state.manifest.visual, state.manifest.characters, state.manifest.shot);
    return { evalScore: c.score, evalNotes: c.notes, durationMs: { eval: Date.now() - t0 } };
  } catch (e) {
    return { errors: [`eval: ${e instanceof Error ? e.message : String(e)}`], durationMs: { eval: Date.now() - t0 } };
  }
}

export function buildGraphM3() {
  const g = new StateGraph(StateAnnotation)
    .addNode("plan", planNode)
    .addNode("execute", executeNode)
    .addNode("assemble", assembleNode)
    .addNode("harmonize", harmonizeNode)
    .addNode("eval", evalNode)
    .addEdge(START, "plan")
    .addEdge("plan", "execute")
    .addEdge("execute", "assemble")
    .addEdge("assemble", "harmonize")
    .addEdge("harmonize", "eval")
    .addEdge("eval", END);
  return g.compile();
}
