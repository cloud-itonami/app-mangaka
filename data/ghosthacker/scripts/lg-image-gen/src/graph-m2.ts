/**
 * Method 2: 1枚絵 + edit/mask 修正
 * Pattern: Agent Loop (vision-critic-driven retry)
 *
 * Stages (loop, max 3 iterations):
 *   1. buildPrompt (initial or refined based on critique)
 *   2. generate
 *   3. critique (gpt-4o-mini-vision)
 *   4. if score >= 7 → done; else if iter < max → refine prompt → retry; else → ship best
 */
import { StateGraph, START, END, Annotation } from "@langchain/langgraph";
import * as fs from "node:fs";
import * as path from "node:path";
import { generate, edit, critique, MODEL } from "./lib/openai.js";
import { generateGemini, selectProvider } from "./lib/gemini.js";
import { extractSetting, pickVariant, refPath, characterDescriptor, type Variant } from "./lib/refs.js";

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

const MAX_ITERS = Number(process.env.LG_M2_MAX_ITERS ?? 3);
const ACCEPT_SCORE = Number(process.env.LG_M2_ACCEPT_SCORE ?? 7);

export const StateAnnotation = Annotation.Root({
  manifest: Annotation<PanelManifestEntry>(),
  setting: Annotation<string>(),
  visualNote: Annotation<string>(),
  resolvedRefs: Annotation<{ character: string; variant: Variant; refPath: string }[]>({ default: () => [], reducer: (_, x) => x }),
  iter: Annotation<number>({ default: () => 0, reducer: (_, x) => x }),
  currentPrompt: Annotation<string>(),
  candidatePath: Annotation<string | null>({ default: () => null, reducer: (_, x) => x }),
  bestPath: Annotation<string | null>({ default: () => null, reducer: (_, x) => x }),
  bestScore: Annotation<number>({ default: () => 0, reducer: (_, x) => x }),
  lastCritique: Annotation<{ score: number; settingMatch: boolean; charactersMatch: boolean; hasUnwantedText: boolean; compositionScore?: number; expressionConcrete?: boolean; propsRecognized?: string[]; notes: string } | null>({ default: () => null, reducer: (_, x) => x }),
  outputRelUrl: Annotation<string | null>({ default: () => null, reducer: (_, x) => x }),
  errors: Annotation<string[]>({ default: () => [], reducer: (a, b) => [...a, ...b] }),
  durationMs: Annotation<Record<string, number>>({ default: () => ({}), reducer: (a, b) => ({ ...a, ...b }) }),
  iterLog: Annotation<{ iter: number; score: number; notes: string }[]>({ default: () => [], reducer: (a, b) => [...a, ...b] }),
});
export type State = typeof StateAnnotation.State;

// Visual-style suffixes — drive the panel's artistic treatment
const VISUAL_STYLE_SUFFIX: Record<string, string> = {
  "cinematic-close": "Style: cinematic emotional close-up — extreme depth of field, rim lighting carving the focal character's silhouette, dramatic chiaroscuro shadow play, eyes as the dominant focal element with sharp catchlights, atmospheric particles. Inspired by 攻殻機動隊 (士郎正宗) quiet contemplation panels and One Piece emotional close-ups.",
  "anime-action":     "Style: dynamic shounen action — exaggerated foreshortening, motion lines streaking from the focal character, speed effects, Dutch angle, impact stars/burst effects, halftone speed dust. Inspired by 岸本斉史 Naruto fight panels and 尾田栄一郎 One Piece battle compositions.",
  "film-medium":      "Style: cinematic film-like medium shot — rule-of-thirds composition, layered foreground/midground/background with depth of field, set dressing visible, characters staged with intentional spacing, soft tonal gradients. Inspired by 攻殻機動隊 (押井守 film aesthetics) calm dialogue scenes and Aria's tonal medium shots.",
  "establishing-illustration": "Style: detailed atmospheric establishing illustration — full environmental detail with weather/light condition, scenic depth, architectural specificity, texture-rich screen tones. Inspired by 天野こずえ Aria's establishing pages — peaceful, world-grounding detail.",
};

// Shot-type composition requirements (Naruto/OP/GitS quality)
const SHOT_REQUIREMENTS: Record<string, string> = {
  "Extreme Close Up": "Composition: eyes occupy at least 30% of frame area; pupils, irises, and catchlights must be sharply rendered; at least one physical signal (sweat, tear, blush, biting lip, dilated pupils) must be visible; rim light or reflected light source on face indicating story motivation.",
  "Close Up":         "Composition: head and shoulders frame; expression-driving features (eyes + mouth) sharply rendered; subtle background bokeh; one expressive physical signal required; lighting indicates the emotional motivation.",
  "Medium Shot":      "Composition: rule-of-thirds with focal character on golden ratio line; at least one prop in mid-ground; hand position and body language must be intentional and readable; depth of field with softened background.",
  "Wide Shot":        "Composition: foreground / midground / background three-layer depth; focal character silhouette readable; environmental anchor (window light, doorway, key prop) clearly placed; rule of thirds.",
  "Insert":           "Composition: single object filling 60-80% of frame; strong directional lighting with cast shadow; texture and material precisely rendered; surrounding context softened or framed.",
  "Over the Shoulder": "Composition: foreground character's shoulder/back as silhouette occupying 30-40% of frame edge; focal character at golden ratio; depth of field separating layers.",
  "POV":              "Composition: first-person view from one character; subject framed as if seen through their eyes; perspective lines driving toward what they're looking at.",
};

function shotRequirements(shot: string): string {
  // Match by case-insensitive starts-with
  const key = Object.keys(SHOT_REQUIREMENTS).find((k) => shot.toLowerCase().startsWith(k.toLowerCase().slice(0, 5)));
  return key ? SHOT_REQUIREMENTS[key] : SHOT_REQUIREMENTS["Medium Shot"];
}

function basePrompt(state: State): string {
  const m = state.manifest as any;

  // Phase 3.4 rich-schema fields
  const sceneSubject = m.sceneSubject ?? "";
  const focusCharacter = m.focusCharacter ?? "";
  const allChars: string[] = m.allCharacters ?? m.characters ?? [];
  const props: string[] = m.props ?? [];
  const visualDesc = m.gh_visualDescription ?? m.visualDescription ?? m.visual ?? "";
  const precedingBeat = m.precedingBeat ?? "";
  const followingBeat = m.followingBeat ?? "";
  const visualStyle = m.visualStyle ?? "film-medium";
  const tone = m.tone ?? "quiet";
  const emotionSignals: { character: string; signals: string[] }[] = m.emotionPhysicalSignals ?? [];

  // Per-character descriptors
  const charDescriptors = allChars
    .map((c, i) => {
      const desc = characterDescriptor(c);
      const isFocus = c === focusCharacter ? " [FOCUS]" : "";
      const positionHint = allChars.length > 1 ? ` (in-frame ${i + 1}/${allChars.length})` : "";
      return desc ? `${c}${isFocus}${positionHint}: ${desc}` : `${c}${isFocus}${positionHint}`;
    })
    .join(" / ");

  // Emotion signals — concrete physical indicators
  const signalsLine = emotionSignals.length > 0
    ? `Required physical signals (must be visible in the rendered image): ${emotionSignals.map((s) => `${s.character}: ${s.signals.join(", ")}`).join(" / ")}.`
    : "";

  const refsHint = state.resolvedRefs.length > 0
    ? `Character face-identity references are supplied as input images IN THIS ORDER: ${state.resolvedRefs.map((r, i) => `(${i + 1}) ${r.character}`).join(", ")}. Use each reference ONLY for face identity (face shape, eye design, hairstyle, age impression) of the named character — do NOT copy reference clothing, pose, or background. Apply standard Japanese school uniform appropriate to the setting and the pose described.`
    : "";

  const propsLine = props.length > 0
    ? `KEY PROPS in scene (must be visible and recognizable, integrated into the composition): ${props.join(", ")}.`
    : "";

  const subjectLine = sceneSubject
    ? `SCENE SUBJECT (the panel's main beat): ${sceneSubject}.`
    : "";

  const continuityLine = (precedingBeat || followingBeat)
    ? `Story continuity — preceding beat: ${precedingBeat || "(none)"} / following beat: ${followingBeat || "(none)"}. The panel must visually connect with these beats.`
    : "";

  const styleLine = VISUAL_STYLE_SUFFIX[visualStyle] ?? VISUAL_STYLE_SUFFIX["film-medium"];
  const toneLine = `Emotional tone: ${tone}.`;
  const shotReqLine = shotRequirements(m.shot ?? "Medium Shot");

  return [
    "ONE SINGLE manga-style illustration filling the entire image, black-and-white monochrome with screen tones. NOT a manga page with multiple sub-frames — just ONE single contiguous illustration. Fictional original characters for Weekly Shounen Jump style fiction publication, entirely fictional and not based on any real persons.",
    state.setting ? `LOCATION (do not change): ${state.setting}.` : "",
    state.visualNote ? `Set dressing: ${state.visualNote}.` : "",
    subjectLine,
    `Visual to render: ${visualDesc}.`,
    propsLine,
    `Shot framing: ${m.shot}.`,
    shotReqLine,
    allChars.length > 0 ? `Characters in frame (each must be visually distinct): ${charDescriptors}.` : "Empty scene.",
    focusCharacter && focusCharacter !== "shared" ? `Compositional focus is on ${focusCharacter}.` : "",
    signalsLine,
    toneLine,
    styleLine,
    continuityLine,
    refsHint,
    "ABSOLUTE: ONE seamless full-bleed image only. NO sub-panels, NO panel dividers, NO multi-frame layout, NO text, NO speech bubbles, NO captions, NO labels, NO storyboard frames or scene numbers.",
  ].filter(Boolean).join(" ");
}

async function planNode(state: State): Promise<Partial<State>> {
  const t0 = Date.now();
  const m = state.manifest as any;
  // Phase 3.4 rich schema: prefer top-level setting/visualNote from outline if injected by manifest
  const fromPrompt = extractSetting(m.prompt ?? "");
  const setting = m.setting ?? fromPrompt.setting;
  const visualNote = m.visualNote ?? fromPrompt.visualNote;

  // Reference characters: prefer focused (active subjects); fall back to all in-frame chars (capped at 3)
  const focused: string[] = m.focusedCharacters ?? [];
  const allChars: string[] = m.allCharacters ?? state.manifest.characters ?? [];
  const refTargets = focused.length > 0 ? focused : allChars.slice(0, 3);

  const resolvedRefs = refTargets
    .map((c) => {
      const v = pickVariant(c, state.manifest.dialogues, state.manifest.shot);
      const rp = refPath(c, v);
      return rp ? { character: c, variant: v, refPath: rp } : null;
    })
    .filter((x): x is { character: string; variant: Variant; refPath: string } => x !== null);
  const initial = basePrompt({ ...state, setting, visualNote, resolvedRefs } as State);
  return { setting, visualNote, resolvedRefs, currentPrompt: initial, iter: 0, durationMs: { plan: Date.now() - t0 } };
}

async function generateNode(state: State): Promise<Partial<State>> {
  const t0 = Date.now();
  const m = state.manifest as any;
  // Hybrid provider routing: gemini for ominous/tense/contemplative, openai otherwise
  const provider = process.env.LG_FORCE_PROVIDER === "openai" ? "openai"
                 : process.env.LG_FORCE_PROVIDER === "gemini" ? "gemini"
                 : selectProvider(m.tone, m.visualStyle);
  try {
    let b64: string;
    if (provider === "gemini") {
      // Gemini doesn't support ref-image injection in the same way; use prompt-only
      b64 = await generateGemini(state.currentPrompt);
    } else if (state.resolvedRefs.length > 0) {
      b64 = await edit(state.currentPrompt, state.resolvedRefs.map((r) => r.refPath));
    } else {
      b64 = await generate(state.currentPrompt);
    }
    const candidatePath = state.manifest.outputPath.replace(/\.png$/, `_${provider}_iter${state.iter + 1}.png`);
    fs.mkdirSync(path.dirname(candidatePath), { recursive: true });
    fs.writeFileSync(candidatePath, Buffer.from(b64, "base64"));
    const dur = state.durationMs.generate ?? 0;
    return { candidatePath, iter: state.iter + 1, durationMs: { generate: dur + (Date.now() - t0) } };
  } catch (e) {
    return { errors: [`generate (${provider}): ${e instanceof Error ? e.message : String(e)}`], durationMs: { generate: Date.now() - t0 } };
  }
}

async function critiqueNode(state: State): Promise<Partial<State>> {
  const t0 = Date.now();
  if (!state.candidatePath) return { errors: ["no candidate"] };
  try {
    const m = state.manifest as any;
    const c = await critique(
      state.candidatePath,
      state.setting || m.visual,
      m.allCharacters ?? state.manifest.characters,
      state.manifest.shot,
      m.props ?? [],
      m.emotionPhysicalSignals ?? [],
      m.visualStyle ?? "",
    );
    const better = c.score > state.bestScore;
    const dur = state.durationMs.critique ?? 0;
    return {
      lastCritique: c,
      bestScore: better ? c.score : state.bestScore,
      bestPath: better ? state.candidatePath : state.bestPath,
      iterLog: [{ iter: state.iter, score: c.score, notes: c.notes }],
      durationMs: { critique: dur + (Date.now() - t0) },
    };
  } catch (e) {
    return { errors: [`critique: ${e instanceof Error ? e.message : String(e)}`], durationMs: { critique: Date.now() - t0 } };
  }
}

async function refineNode(state: State): Promise<Partial<State>> {
  const t0 = Date.now();
  const c = state.lastCritique;
  const m = state.manifest;
  const fixes: string[] = [];
  if (c) {
    if (!c.settingMatch) fixes.push(`The previous attempt did NOT show the correct location. Strictly render: ${state.setting}. Do not drift to any other setting (no jewelry shop, no street, no home — only the school location specified).`);
    if (!c.charactersMatch) {
      const charDescs = m.characters.map((ch) => `${ch}: ${characterDescriptor(ch) || "as per reference"}`).join(" / ");
      fixes.push(`The previous attempt did NOT differentiate or match the expected characters. Each character must be visually distinct: ${charDescs}. Use the supplied reference images strictly for face identity.`);
    }
    if (c.hasUnwantedText) fixes.push("The previous attempt had unwanted text/speech bubbles. Remove ALL text, captions, labels, scene numbers, dialogue overlays.");
    if (c.notes) fixes.push(`Critic notes: ${c.notes}. Address these.`);
  }
  const refined = [basePrompt(state), ...fixes].join(" ");
  return { currentPrompt: refined, durationMs: { refine: (state.durationMs.refine ?? 0) + (Date.now() - t0) } };
}

function shouldStop(state: State): "stop" | "retry" {
  const c = state.lastCritique;
  if (!c) return "stop";
  if (c.score >= ACCEPT_SCORE) return "stop";
  if (state.iter >= MAX_ITERS) return "stop";
  return "retry";
}

async function persistNode(state: State): Promise<Partial<State>> {
  const t0 = Date.now();
  const src = state.bestPath ?? state.candidatePath;
  if (!src) return { errors: ["nothing to persist"] };
  fs.copyFileSync(src, state.manifest.outputPath);
  const idx = state.manifest.outputPath.indexOf("/resources/");
  const url = idx >= 0 ? state.manifest.outputPath.slice(idx + "/resources".length) : state.manifest.outputPath;
  return { outputRelUrl: url, durationMs: { persist: Date.now() - t0 } };
}

export function buildGraphM2() {
  const g = new StateGraph(StateAnnotation)
    .addNode("plan", planNode)
    .addNode("generate", generateNode)
    .addNode("critique", critiqueNode)
    .addNode("refine", refineNode)
    .addNode("persist", persistNode)
    .addEdge(START, "plan")
    .addEdge("plan", "generate")
    .addEdge("generate", "critique")
    .addConditionalEdges("critique", shouldStop, { stop: "persist", retry: "refine" })
    .addEdge("refine", "generate")
    .addEdge("persist", END);
  return g.compile();
}
