/**
 * 3-stage LangGraph pipeline for character-consistent panel generation.
 *
 * Stages:
 *   1. buildPrompt       — finalize prompts (split into bg-prompt + composite-prompt)
 *   2. pickReferences    — choose face variant per character (based on emotion/shot heuristics)
 *   3. generateBackground — POST /v1/images/generations with bg-only prompt → bg.png
 *   4. compositeCharacters — POST /v1/images/edits with [bg.png, ref1.png, ref2.png, ...] → final.png
 *   5. persistImage      — write final PNG, return relative URL
 *
 * Reference images:
 *   resources/characters/{Name}/reference_variants/{variant}.png
 *   variants: action_shout, angry_3q_left, anxious_front, downcast_sad, focused_3q_right,
 *             gentle_smile_3q, neutral_front, profile_left, profile_right, surprised_front,
 *             three_quarter_left_neutral, three_quarter_right_neutral
 */
import { StateGraph, START, END, Annotation } from "@langchain/langgraph";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO = process.env.GH_REPO ?? path.resolve(__dirname, "../../data/ghosthacker");
const OPENAI_GEN = "https://api.openai.com/v1/images/generations";
const OPENAI_EDIT = "https://api.openai.com/v1/images/edits";
const MODEL = process.env.LG_IMAGE_MODEL ?? "gpt-image-2";
const SIZE = process.env.LG_IMAGE_SIZE ?? "1024x1536";
const QUALITY = process.env.LG_IMAGE_QUALITY ?? "low";

if (MODEL === "gpt-image-1" || MODEL.startsWith("gpt-image-1")) {
  throw new Error("gpt-image-1 is forbidden. Use gpt-image-2.");
}

// Refined to avoid the gpt-image-2 "storyboard document layout" misfire.
// Avoid the words "storyboard", "panel", "manga" in BG prompt (they trigger document-layout artifacts).
const BG_STYLE = "Cinematic scene-establishing illustration of an empty environment. NO characters, NO people, NO figures in the frame — just the location, props, lighting, and atmosphere. Monochrome black-and-white illustration with screen tones, anime-manga line style. NO text, NO labels, NO annotations, NO camera diagrams, NO scene numbers, NO grids, NO frames. Single full-bleed image only.";
const COMP_STYLE = "Maintain the existing scene location and atmosphere from the supplied background image. Place the supplied character(s) naturally within that exact same location — do not change the setting. Match the monochrome black-and-white illustration style with screen tones. Use the supplied references as FACE-IDENTITY ONLY (face shape, eye design, hairstyle, age impression). Apply scene-appropriate clothing and pose described in the scene description. CRITICAL: NO speech bubbles, NO dialogue text, NO captions, NO sound-effect text, NO labels, NO annotations, NO scene numbers anywhere in the image. Render the scene visually only — dialogue is for typesetting later.";

type Variant =
  | "action_shout" | "angry_3q_left" | "anxious_front" | "downcast_sad" | "focused_3q_right"
  | "gentle_smile_3q" | "neutral_front" | "profile_left" | "profile_right" | "surprised_front"
  | "three_quarter_left_neutral" | "three_quarter_right_neutral";

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

export const PanelStateAnnotation = Annotation.Root({
  manifest: Annotation<PanelManifestEntry>(),
  bgPrompt: Annotation<string>(),
  compositePrompt: Annotation<string>(),
  resolvedReferences: Annotation<{ character: string; variant: Variant; refPath: string }[]>({ default: () => [], reducer: (_, x) => x }),
  bgB64: Annotation<string | null>({ default: () => null, reducer: (_, x) => x }),
  bgPath: Annotation<string | null>({ default: () => null, reducer: (_, x) => x }),
  finalB64: Annotation<string | null>({ default: () => null, reducer: (_, x) => x }),
  outputAbsPath: Annotation<string | null>({ default: () => null, reducer: (_, x) => x }),
  outputRelUrl: Annotation<string | null>({ default: () => null, reducer: (_, x) => x }),
  errors: Annotation<string[]>({ default: () => [], reducer: (a, b) => [...a, ...b] }),
  durationMs: Annotation<{ build: number; pickRef: number; bg: number; composite: number; persist: number }>({
    default: () => ({ build: 0, pickRef: 0, bg: 0, composite: 0, persist: 0 }),
    reducer: (_, x) => x,
  }),
});

export type PanelState = typeof PanelStateAnnotation.State;

function pickVariantForCharacter(char: string, dialogue: { speaker: string; text: string; emotion?: string }[], shot: string): Variant {
  const ownDialogue = dialogue.find((d) => d.speaker === char);
  const emotion = (ownDialogue?.emotion ?? "").toLowerCase();
  const text = (ownDialogue?.text ?? "").toLowerCase();

  if (emotion.includes("shout") || emotion.includes("叫") || text.includes("！") || text.includes("!!")) return "action_shout";
  if (emotion.includes("angry") || emotion.includes("怒")) return "angry_3q_left";
  if (emotion.includes("anxious") || emotion.includes("不安") || emotion.includes("怯")) return "anxious_front";
  if (emotion.includes("sad") || emotion.includes("downcast") || emotion.includes("悲") || emotion.includes("涙")) return "downcast_sad";
  if (emotion.includes("smile") || emotion.includes("笑") || emotion.includes("gentle")) return "gentle_smile_3q";
  if (emotion.includes("surprise") || emotion.includes("驚")) return "surprised_front";
  if (emotion.includes("focused") || emotion.includes("集中")) return "focused_3q_right";
  if (shot.toLowerCase().includes("close up")) return "neutral_front";
  if (shot.toLowerCase().includes("profile")) return "profile_right";
  return "three_quarter_right_neutral"; // default neutral 3q
}

function refPathFor(character: string, variant: Variant): string | null {
  const p = `${REPO}/resources/characters/${character}/reference_variants/${variant}.png`;
  if (fs.existsSync(p)) return p;
  // Fall back to neutral_front, then main.png
  const fallback = `${REPO}/resources/characters/${character}/reference_variants/neutral_front.png`;
  if (fs.existsSync(fallback)) return fallback;
  const main = `${REPO}/resources/characters/${character}/main.png`;
  if (fs.existsSync(main)) return main;
  return null;
}

function extractSetting(prompt: string): { setting?: string; visualNote?: string } {
  const settingMatch = prompt.match(/Setting:\s*([^.]+(?:\.[^A-Z][^.]*)*)\./);
  const visualNoteMatch = prompt.match(/Visual note:\s*([^.]+(?:\.[^A-Z][^.]*)*)\./);
  return { setting: settingMatch?.[1]?.trim(), visualNote: visualNoteMatch?.[1]?.trim() };
}

async function buildPromptNode(state: PanelState): Promise<Partial<PanelState>> {
  const t0 = Date.now();
  const m = state.manifest;
  const { setting, visualNote } = extractSetting(m.prompt);

  const beatSummary = m.dialogues.length > 0
    ? `${m.characters.length} character(s) interacting silently. Mood: ${(m.dialogues[0].emotion ?? "calm")}.`
    : "Quiet scene.";
  const shotText = `Shot framing: ${m.shot}.`;
  const charText = m.characters.length > 0 ? `Characters present: ${m.characters.join(" and ")}.` : "Empty scene — no characters.";

  // PRIMARY LOCATION anchor — comes FIRST so the model treats it as the dominant constraint
  const locationAnchor = setting
    ? `PRIMARY LOCATION (do not change): ${setting}`
    : "";
  const visualNoteText = visualNote ? `Set dressing: ${visualNote}.` : "";
  const sceneDetailText = `Specific moment in this location: ${m.visual}.`;

  // Background prompt: location-first, no characters
  const bgPrompt = [
    "Generate a cinematic illustration of the following location.",
    locationAnchor,
    visualNoteText,
    sceneDetailText.replace(/[人キャラ]/g, ""),
    shotText,
    "ABSOLUTE: this image must show the location described above. Render no people, no characters, no figures, no humans — empty location only.",
    BG_STYLE,
  ].filter(Boolean).join(" ");

  // Composite prompt: preserve bg location explicitly
  const compositePrompt = [
    "Edit the supplied background image: place the supplied character reference(s) naturally into the EXACT SAME location shown in the background.",
    "ABSOLUTE: do not change the location, building, room, lighting, props, or atmosphere of the background. The setting must remain the supplied background.",
    locationAnchor,
    sceneDetailText,
    charText,
    shotText,
    beatSummary,
    "Each supplied reference image (after the first/background image) provides FACE-IDENTITY ONLY for one character: face shape, eye design, hairstyle, age impression. Do NOT copy the reference's clothing, pose, or background. Apply Japanese middle-school uniform (gakuran for boys, sailor uniform for girls) unless the scene specifies different clothing.",
    COMP_STYLE,
  ].filter(Boolean).join(" ");

  return {
    bgPrompt,
    compositePrompt,
    durationMs: { ...state.durationMs, build: Date.now() - t0 },
  };
}

async function pickReferencesNode(state: PanelState): Promise<Partial<PanelState>> {
  const t0 = Date.now();
  const m = state.manifest;
  const resolved: { character: string; variant: Variant; refPath: string }[] = [];
  for (const c of m.characters) {
    const variant = pickVariantForCharacter(c, m.dialogues, m.shot);
    const refPath = refPathFor(c, variant);
    if (refPath) resolved.push({ character: c, variant, refPath });
  }
  return {
    resolvedReferences: resolved,
    durationMs: { ...state.durationMs, pickRef: Date.now() - t0 },
  };
}

interface OpenAIResponse {
  data?: Array<{ b64_json?: string; url?: string }>;
  error?: { message?: string; code?: string };
}

async function generateBackgroundNode(state: PanelState): Promise<Partial<PanelState>> {
  const t0 = Date.now();
  const apiKey = process.env.OPENAI_API_KEY;
  if (!apiKey) return { errors: ["OPENAI_API_KEY not set"], durationMs: { ...state.durationMs, bg: Date.now() - t0 } };
  try {
    const response = await fetch(OPENAI_GEN, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
      body: JSON.stringify({
        model: MODEL,
        prompt: state.bgPrompt.slice(0, 32000),
        size: SIZE,
        quality: QUALITY,
        n: 1,
      }),
    });
    if (!response.ok) {
      const errText = await response.text();
      return { errors: [`generateBackground HTTP ${response.status}: ${errText.slice(0, 400)}`], durationMs: { ...state.durationMs, bg: Date.now() - t0 } };
    }
    const result = (await response.json()) as OpenAIResponse;
    if (result.error) return { errors: [`generateBackground: ${result.error.message}`], durationMs: { ...state.durationMs, bg: Date.now() - t0 } };
    const b64 = result.data?.[0]?.b64_json;
    if (!b64) return { errors: [`generateBackground: no b64 in response`], durationMs: { ...state.durationMs, bg: Date.now() - t0 } };
    // Save bg to disk for /v1/images/edits to consume
    const bgPath = state.manifest.outputPath.replace(/\.png$/, "_bg.png");
    fs.mkdirSync(path.dirname(bgPath), { recursive: true });
    fs.writeFileSync(bgPath, Buffer.from(b64, "base64"));
    return { bgB64: b64, bgPath, durationMs: { ...state.durationMs, bg: Date.now() - t0 } };
  } catch (err) {
    return { errors: [`generateBackground threw: ${err instanceof Error ? err.message : String(err)}`], durationMs: { ...state.durationMs, bg: Date.now() - t0 } };
  }
}

async function compositeCharactersNode(state: PanelState): Promise<Partial<PanelState>> {
  const t0 = Date.now();
  const apiKey = process.env.OPENAI_API_KEY;
  if (!apiKey) return { errors: ["OPENAI_API_KEY not set"], durationMs: { ...state.durationMs, composite: Date.now() - t0 } };
  if (!state.bgPath) return { errors: ["No bg image to composite onto"], durationMs: { ...state.durationMs, composite: Date.now() - t0 } };

  // If no references resolved, just promote bg → final
  if (state.resolvedReferences.length === 0) {
    return { finalB64: state.bgB64, durationMs: { ...state.durationMs, composite: Date.now() - t0 } };
  }

  try {
    const fd = new FormData();
    fd.append("model", MODEL);
    fd.append("prompt", state.compositePrompt.slice(0, 32000));
    fd.append("size", SIZE);
    fd.append("quality", QUALITY);
    fd.append("n", "1");
    // First image = bg (target to edit)
    const bgBuf = fs.readFileSync(state.bgPath);
    fd.append("image[]", new Blob([bgBuf as any], { type: "image/png" }), "background.png");
    // Subsequent = character refs
    for (const ref of state.resolvedReferences) {
      const buf = fs.readFileSync(ref.refPath);
      fd.append("image[]", new Blob([buf as any], { type: "image/png" }), `${ref.character}_${ref.variant}.png`);
    }

    const response = await fetch(OPENAI_EDIT, {
      method: "POST",
      headers: { Authorization: `Bearer ${apiKey}` },
      body: fd as any,
    });
    if (!response.ok) {
      const errText = await response.text();
      return { errors: [`compositeCharacters HTTP ${response.status}: ${errText.slice(0, 400)}`], durationMs: { ...state.durationMs, composite: Date.now() - t0 } };
    }
    const result = (await response.json()) as OpenAIResponse;
    if (result.error) return { errors: [`compositeCharacters: ${result.error.message}`], durationMs: { ...state.durationMs, composite: Date.now() - t0 } };
    const b64 = result.data?.[0]?.b64_json;
    if (!b64) return { errors: [`compositeCharacters: no b64 in response`], durationMs: { ...state.durationMs, composite: Date.now() - t0 } };
    return { finalB64: b64, durationMs: { ...state.durationMs, composite: Date.now() - t0 } };
  } catch (err) {
    return { errors: [`compositeCharacters threw: ${err instanceof Error ? err.message : String(err)}`], durationMs: { ...state.durationMs, composite: Date.now() - t0 } };
  }
}

async function persistImageNode(state: PanelState): Promise<Partial<PanelState>> {
  const t0 = Date.now();
  if (!state.finalB64) return { errors: ["No final image to persist"], durationMs: { ...state.durationMs, persist: Date.now() - t0 } };
  try {
    const outAbs = state.manifest.outputPath;
    fs.mkdirSync(path.dirname(outAbs), { recursive: true });
    fs.writeFileSync(outAbs, Buffer.from(state.finalB64, "base64"));
    const idx = outAbs.indexOf("/resources/");
    const relUrl = idx >= 0 ? outAbs.slice(idx + "/resources".length) : outAbs;
    return { outputAbsPath: outAbs, outputRelUrl: relUrl, durationMs: { ...state.durationMs, persist: Date.now() - t0 } };
  } catch (err) {
    return { errors: [`persistImage threw: ${err instanceof Error ? err.message : String(err)}`], durationMs: { ...state.durationMs, persist: Date.now() - t0 } };
  }
}

export function buildGraph3Stage() {
  const g = new StateGraph(PanelStateAnnotation)
    .addNode("buildPrompt", buildPromptNode)
    .addNode("pickReferences", pickReferencesNode)
    .addNode("generateBackground", generateBackgroundNode)
    .addNode("compositeCharacters", compositeCharactersNode)
    .addNode("persistImage", persistImageNode)
    .addEdge(START, "buildPrompt")
    .addEdge("buildPrompt", "pickReferences")
    .addEdge("pickReferences", "generateBackground")
    .addEdge("generateBackground", "compositeCharacters")
    .addEdge("compositeCharacters", "persistImage")
    .addEdge("persistImage", END);
  return g.compile();
}

export const PIPELINE_INFO_3STAGE = { pipeline: "3-stage", model: MODEL, size: SIZE, quality: QUALITY };
