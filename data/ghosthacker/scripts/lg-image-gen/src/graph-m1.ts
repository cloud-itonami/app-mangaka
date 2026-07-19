/**
 * Method 1: 伝統的 manga 制作スタイル (キャラ層→背景層→機械合成)
 * Pattern: Graph (deterministic DAG)
 *
 * Stages:
 *   1. plan       — extract location anchor, pose for each char from refs+dialogue
 *   2. genChars   — for each char, /v1/images/edits using ref → full-body on white BG
 *                    (parallel, but invoked sequentially in this graph)
 *   3. genBG      — /v1/images/generations location-only (no characters)
 *   4. composite  — sharp white-to-alpha + paste characters onto BG
 *   5. harmonize  — light /v1/images/edits pass to blend lighting (optional, skip if cost-sensitive)
 *   6. persist
 */
import { StateGraph, START, END, Annotation } from "@langchain/langgraph";
import * as fs from "node:fs";
import * as path from "node:path";
import sharp from "sharp";
import { generate, edit, MODEL, SIZE, QUALITY } from "./lib/openai.js";
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

type CharLayer = { character: string; variant: Variant; cutoutPath: string };

export const StateAnnotation = Annotation.Root({
  manifest: Annotation<PanelManifestEntry>(),
  setting: Annotation<string>(),
  visualNote: Annotation<string>(),
  charLayers: Annotation<CharLayer[]>({ default: () => [], reducer: (_, x) => x }),
  bgPath: Annotation<string | null>({ default: () => null, reducer: (_, x) => x }),
  compositePath: Annotation<string | null>({ default: () => null, reducer: (_, x) => x }),
  outputRelUrl: Annotation<string | null>({ default: () => null, reducer: (_, x) => x }),
  errors: Annotation<string[]>({ default: () => [], reducer: (a, b) => [...a, ...b] }),
  durationMs: Annotation<Record<string, number>>({ default: () => ({}), reducer: (a, b) => ({ ...a, ...b }) }),
});
export type State = typeof StateAnnotation.State;

async function planNode(state: State): Promise<Partial<State>> {
  const t0 = Date.now();
  const m = state.manifest;
  const { setting, visualNote } = extractSetting(m.prompt);
  return { setting, visualNote, durationMs: { plan: Date.now() - t0 } };
}

async function genCharsNode(state: State): Promise<Partial<State>> {
  const t0 = Date.now();
  const m = state.manifest;
  const layers: CharLayer[] = [];
  const errs: string[] = [];
  for (const char of m.characters) {
    const variant = pickVariant(char, m.dialogues, m.shot);
    const ref = refPath(char, variant);
    if (!ref) { errs.push(`no ref for ${char}`); continue; }
    const charDialogue = m.dialogues.find((d) => d.speaker === char);
    const poseHint = charDialogue?.emotion
      ? `pose: emotion ${charDialogue.emotion}`
      : `pose: neutral standing`;
    const prompt = [
      "Redraw this character at full body for a manga panel.",
      "PURE WHITE background — solid #FFFFFF, no scene, no shadow on the floor.",
      "Character: middle-school student, Japanese, age 15. Apply Japanese middle-school uniform: gakuran (boys) or sailor uniform (girls).",
      poseHint,
      `Shot framing: ${m.shot}`,
      "Manga line style, monochrome with screen tones. NO text, NO speech bubbles, NO captions, NO labels.",
      "Use the supplied reference for face identity ONLY (face shape, eye design, hairstyle, age impression).",
    ].join(" ");
    try {
      const b64 = await edit(prompt, [ref], { size: SIZE, quality: QUALITY });
      const cutoutPath = m.outputPath.replace(/\.png$/, `_m1_char_${char}.png`);
      fs.mkdirSync(path.dirname(cutoutPath), { recursive: true });
      fs.writeFileSync(cutoutPath, Buffer.from(b64, "base64"));
      layers.push({ character: char, variant, cutoutPath });
    } catch (e) {
      errs.push(`genChar ${char}: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
  return { charLayers: layers, errors: errs, durationMs: { genChars: Date.now() - t0 } };
}

async function genBGNode(state: State): Promise<Partial<State>> {
  const t0 = Date.now();
  const m = state.manifest;
  const prompt = [
    "Cinematic illustration of an empty location.",
    state.setting ? `LOCATION: ${state.setting}` : "",
    state.visualNote ? `Set dressing: ${state.visualNote}` : "",
    `Specific framing/atmosphere: ${m.visual}.`.replace(/[人キャラ]/g, ""),
    `Shot framing: ${m.shot}`,
    "ABSOLUTE: no people, no characters, no figures, no humans. Empty location only.",
    "Monochrome black-and-white anime line style with screen tones. NO text, NO labels, NO scene numbers, NO storyboard frames.",
  ].filter(Boolean).join(" ");
  try {
    const b64 = await generate(prompt);
    const bgPath = state.manifest.outputPath.replace(/\.png$/, "_m1_bg.png");
    fs.mkdirSync(path.dirname(bgPath), { recursive: true });
    fs.writeFileSync(bgPath, Buffer.from(b64, "base64"));
    return { bgPath, durationMs: { genBG: Date.now() - t0 } };
  } catch (e) {
    return { errors: [`genBG: ${e instanceof Error ? e.message : String(e)}`], durationMs: { genBG: Date.now() - t0 } };
  }
}

async function compositeNode(state: State): Promise<Partial<State>> {
  const t0 = Date.now();
  if (!state.bgPath) return { errors: ["no bgPath"], durationMs: { composite: Date.now() - t0 } };
  try {
    let result = sharp(state.bgPath);
    const meta = await result.metadata();
    const W = meta.width ?? 1024, H = meta.height ?? 1536;

    // For each char layer: white→alpha + resize, then composite
    const layers: any[] = [];
    const numChars = state.charLayers.length;
    for (let i = 0; i < numChars; i++) {
      const cl = state.charLayers[i];
      // White-to-alpha: simple threshold
      const charBuf = await sharp(cl.cutoutPath)
        .ensureAlpha()
        .raw()
        .toBuffer({ resolveWithObject: true });
      const { data, info } = charBuf;
      // Threshold: if r,g,b all > 245, set alpha=0
      const out = Buffer.from(data);
      for (let p = 0; p < info.width * info.height; p++) {
        const r = out[p * info.channels];
        const g = out[p * info.channels + 1];
        const b = out[p * info.channels + 2];
        if (r > 245 && g > 245 && b > 245) out[p * info.channels + 3] = 0;
      }
      // Resize char to ~50-70% of BG height, place horizontally distributed
      const charH = Math.floor(H * 0.7);
      const charImg = await sharp(out, { raw: info })
        .resize({ height: charH })
        .png()
        .toBuffer();
      const charMeta = await sharp(charImg).metadata();
      const charW = charMeta.width ?? Math.floor(W * 0.4);
      const xOffset = numChars === 1
        ? Math.floor((W - charW) / 2)
        : Math.floor(W * (0.15 + 0.55 * i / Math.max(1, numChars - 1)));
      const yOffset = H - charH; // bottom-aligned
      layers.push({ input: charImg, top: yOffset, left: Math.max(0, Math.min(W - charW, xOffset)) });
    }

    const compositePath = state.manifest.outputPath.replace(/\.png$/, "_m1_composite.png");
    await result.composite(layers).toFile(compositePath);
    return { compositePath, durationMs: { composite: Date.now() - t0 } };
  } catch (e) {
    return { errors: [`composite: ${e instanceof Error ? e.message : String(e)}`], durationMs: { composite: Date.now() - t0 } };
  }
}

async function harmonizeNode(state: State): Promise<Partial<State>> {
  const t0 = Date.now();
  if (!state.compositePath) return { errors: ["no compositePath"], durationMs: { harmonize: Date.now() - t0 } };
  // Skip if env says so
  if (process.env.LG_M1_SKIP_HARMONIZE === "1") {
    fs.copyFileSync(state.compositePath, state.manifest.outputPath);
    const idx = state.manifest.outputPath.indexOf("/resources/");
    return { outputRelUrl: idx >= 0 ? state.manifest.outputPath.slice(idx + "/resources".length) : state.manifest.outputPath, durationMs: { harmonize: Date.now() - t0 } };
  }
  try {
    const prompt = "Lightly harmonize the lighting, line weight, and screen-tone density across this manga panel so the composited characters and background read as a single illustration. Do NOT change the location, character identity, or composition. Do NOT add any text, speech bubbles, or labels. Keep the monochrome black-and-white style.";
    const b64 = await edit(prompt, [state.compositePath]);
    fs.writeFileSync(state.manifest.outputPath, Buffer.from(b64, "base64"));
    const idx = state.manifest.outputPath.indexOf("/resources/");
    return { outputRelUrl: idx >= 0 ? state.manifest.outputPath.slice(idx + "/resources".length) : state.manifest.outputPath, durationMs: { harmonize: Date.now() - t0 } };
  } catch (e) {
    // Fall back to composite if harmonize fails
    fs.copyFileSync(state.compositePath, state.manifest.outputPath);
    const idx = state.manifest.outputPath.indexOf("/resources/");
    return { outputRelUrl: idx >= 0 ? state.manifest.outputPath.slice(idx + "/resources".length) : state.manifest.outputPath, errors: [`harmonize-soft-fail: ${e instanceof Error ? e.message : String(e)}`], durationMs: { harmonize: Date.now() - t0 } };
  }
}

export function buildGraphM1() {
  const g = new StateGraph(StateAnnotation)
    .addNode("plan", planNode)
    .addNode("genChars", genCharsNode)
    .addNode("genBG", genBGNode)
    .addNode("composite", compositeNode)
    .addNode("harmonize", harmonizeNode)
    .addEdge(START, "plan")
    .addEdge("plan", "genChars")
    .addEdge("genChars", "genBG")
    .addEdge("genBG", "composite")
    .addEdge("composite", "harmonize")
    .addEdge("harmonize", END);
  return g.compile();
}
