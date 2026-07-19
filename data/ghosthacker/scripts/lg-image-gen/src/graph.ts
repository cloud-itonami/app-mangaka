/**
 * LangGraph state graph for single-panel image generation.
 *
 * Provider: OpenAI (default) or OpenRouter (set IMAGE_PROVIDER=openrouter).
 *   - OpenAI:     POST https://api.openai.com/v1/images/generations  model=gpt-image-1
 *   - OpenRouter: POST https://openrouter.ai/api/v1/chat/completions  model=google/gemini-3-pro-image-preview
 *
 * Nodes:
 *   1. buildPrompt   — finalize prompt string from manifest entry
 *   2. generateImage — call provider API, receive base64 (OpenAI) or data URL (OpenRouter)
 *   3. persistImage  — decode → write PNG, return relative URL
 */
import { StateGraph, START, END, Annotation } from "@langchain/langgraph";
import * as fs from "node:fs";
import * as path from "node:path";

const PROVIDER = (process.env.IMAGE_PROVIDER ?? "openai").toLowerCase();

const OPENAI_URL = "https://api.openai.com/v1/images/generations";
const OPENAI_MODEL = process.env.LG_IMAGE_MODEL ?? "gpt-image-2";
const OPENAI_SIZE = process.env.LG_IMAGE_SIZE ?? "1024x1536"; // portrait, manga-panel ratio
const OPENAI_QUALITY = process.env.LG_IMAGE_QUALITY ?? "low";   // low | medium | high | auto

if (OPENAI_MODEL === "gpt-image-1" || OPENAI_MODEL.startsWith("gpt-image-1")) {
  throw new Error("gpt-image-1 is forbidden. Use gpt-image-2 (default) or set LG_IMAGE_MODEL to a non-gpt-image-1 model.");
}

const OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions";
const OPENROUTER_MODEL = process.env.LG_IMAGE_MODEL ?? "google/gemini-3-pro-image-preview";

const STYLE_SUFFIX = "Rough sketch aesthetic, monochrome with screen tones, cinematic composition, manga panel layout reference, no text or speech bubbles in the image (dialogue rendered separately).";
const REF_GUIDANCE = "Character identity reference: face shape, eye design, hairstyle, age impression, manga line style. Do not preserve outfit, body pose, background, or props from any references — clothing and setting must follow the panel description (school setting, scene-specific). Generate a single-panel storyboard image for the described scene.";

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
  refinedPrompt: Annotation<string>(),
  generatedDataUrl: Annotation<string | null>({ default: () => null, reducer: (_, x) => x }),
  generatedB64: Annotation<string | null>({ default: () => null, reducer: (_, x) => x }),
  outputAbsPath: Annotation<string | null>({ default: () => null, reducer: (_, x) => x }),
  outputRelUrl: Annotation<string | null>({ default: () => null, reducer: (_, x) => x }),
  errors: Annotation<string[]>({ default: () => [], reducer: (a, b) => [...a, ...b] }),
  durationMs: Annotation<{ build: number; generate: number; persist: number }>({
    default: () => ({ build: 0, generate: 0, persist: 0 }),
    reducer: (_, x) => x,
  }),
  providerUsed: Annotation<string>({ default: () => "", reducer: (_, x) => x }),
  modelUsed: Annotation<string>({ default: () => "", reducer: (_, x) => x }),
});

export type PanelState = typeof PanelStateAnnotation.State;

async function buildPromptNode(state: PanelState): Promise<Partial<PanelState>> {
  const t0 = Date.now();
  const m = state.manifest;
  let base = m.prompt
    .replace(/Use the supplied face reference image\(s\)[^.]*\./g, "")
    .replace(/Use the supplied character reference[^.]*\./g, "")
    .replace(/Generate a new single-panel storyboard image[^.]*\./g, "")
    .replace(STYLE_SUFFIX, "")
    .trim();
  const refined = `${base} ${STYLE_SUFFIX} ${REF_GUIDANCE}`.replace(/\s+/g, " ").trim();
  return { refinedPrompt: refined, durationMs: { ...state.durationMs, build: Date.now() - t0 } };
}

interface OpenAIResponse {
  data?: Array<{ b64_json?: string; url?: string; revised_prompt?: string }>;
  error?: { message?: string; type?: string; code?: string };
}

async function generateOpenAI(state: PanelState): Promise<Partial<PanelState>> {
  const apiKey = process.env.OPENAI_API_KEY;
  if (!apiKey) return { errors: ["OPENAI_API_KEY not set"] };
  const t0 = Date.now();
  try {
    const response = await fetch(OPENAI_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
      body: JSON.stringify({
        model: OPENAI_MODEL,
        prompt: state.refinedPrompt.slice(0, 32000),
        size: OPENAI_SIZE,
        quality: OPENAI_QUALITY,
        n: 1,
      }),
    });
    if (!response.ok) {
      const errText = await response.text();
      return { errors: [`OpenAI HTTP ${response.status}: ${errText.slice(0, 400)}`], durationMs: { ...state.durationMs, generate: Date.now() - t0 } };
    }
    const result = (await response.json()) as OpenAIResponse;
    if (result.error) return { errors: [`OpenAI error: ${result.error.message ?? JSON.stringify(result.error)}`], durationMs: { ...state.durationMs, generate: Date.now() - t0 } };
    const item = result.data?.[0];
    if (!item) return { errors: [`OpenAI: no data in response`], durationMs: { ...state.durationMs, generate: Date.now() - t0 } };
    if (item.b64_json) return { generatedB64: item.b64_json, providerUsed: "openai", modelUsed: OPENAI_MODEL, durationMs: { ...state.durationMs, generate: Date.now() - t0 } };
    if (item.url) return { generatedDataUrl: item.url, providerUsed: "openai", modelUsed: OPENAI_MODEL, durationMs: { ...state.durationMs, generate: Date.now() - t0 } };
    return { errors: [`OpenAI: no image data in response`], durationMs: { ...state.durationMs, generate: Date.now() - t0 } };
  } catch (err) {
    return { errors: [`OpenAI threw: ${err instanceof Error ? err.message : String(err)}`], durationMs: { ...state.durationMs, generate: Date.now() - t0 } };
  }
}

interface OpenRouterResponse {
  choices?: Array<{ message?: { images?: Array<{ image_url?: string | { url?: string } }>; content?: string } }>;
  error?: { message?: string };
}

async function generateOpenRouter(state: PanelState): Promise<Partial<PanelState>> {
  const apiKey = process.env.OPENROUTER_API_KEY;
  if (!apiKey) return { errors: ["OPENROUTER_API_KEY not set"] };
  const t0 = Date.now();
  try {
    const response = await fetch(OPENROUTER_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${apiKey}`,
        "HTTP-Referer": "https://ghosthacker.etzhayyim.com",
        "X-Title": "ghosthacker-arc0-1-lg-image-gen",
      },
      body: JSON.stringify({
        model: OPENROUTER_MODEL,
        messages: [{ role: "user", content: state.refinedPrompt }],
        modalities: ["text", "image"],
        image_config: { aspect_ratio: "3:4", image_size: "1K" },
        stream: false,
      }),
    });
    if (!response.ok) {
      const errText = await response.text();
      return { errors: [`OpenRouter HTTP ${response.status}: ${errText.slice(0, 300)}`], durationMs: { ...state.durationMs, generate: Date.now() - t0 } };
    }
    const result = (await response.json()) as OpenRouterResponse;
    if (result.error) return { errors: [`OpenRouter error: ${result.error.message ?? JSON.stringify(result.error)}`], durationMs: { ...state.durationMs, generate: Date.now() - t0 } };
    const raw = result.choices?.[0]?.message?.images?.[0]?.image_url;
    const dataUrl = typeof raw === "string" ? raw : raw?.url;
    if (!dataUrl || typeof dataUrl !== "string") {
      const fallbackText = result.choices?.[0]?.message?.content ?? "no image";
      return { errors: [`OpenRouter: no image. ${fallbackText.slice(0, 200)}`], durationMs: { ...state.durationMs, generate: Date.now() - t0 } };
    }
    return { generatedDataUrl: dataUrl, providerUsed: "openrouter", modelUsed: OPENROUTER_MODEL, durationMs: { ...state.durationMs, generate: Date.now() - t0 } };
  } catch (err) {
    return { errors: [`OpenRouter threw: ${err instanceof Error ? err.message : String(err)}`], durationMs: { ...state.durationMs, generate: Date.now() - t0 } };
  }
}

async function generateImageNode(state: PanelState): Promise<Partial<PanelState>> {
  if (PROVIDER === "openrouter") return generateOpenRouter(state);
  return generateOpenAI(state);
}

async function persistImageNode(state: PanelState): Promise<Partial<PanelState>> {
  const t0 = Date.now();
  try {
    let buf: Buffer;
    if (state.generatedB64) {
      buf = Buffer.from(state.generatedB64, "base64");
    } else if (state.generatedDataUrl) {
      const u = state.generatedDataUrl;
      if (u.startsWith("data:")) {
        buf = Buffer.from(u.slice(u.indexOf(",") + 1), "base64");
      } else if (u.startsWith("http")) {
        const r = await fetch(u);
        buf = Buffer.from(await r.arrayBuffer());
      } else {
        return { errors: [`Unrecognized data URL: ${u.slice(0, 30)}`], durationMs: { ...state.durationMs, persist: Date.now() - t0 } };
      }
    } else {
      return { errors: ["No image data to persist"], durationMs: { ...state.durationMs, persist: Date.now() - t0 } };
    }
    const outAbs = state.manifest.outputPath;
    fs.mkdirSync(path.dirname(outAbs), { recursive: true });
    fs.writeFileSync(outAbs, buf);
    const idx = outAbs.indexOf("/resources/");
    const relUrl = idx >= 0 ? outAbs.slice(idx + "/resources".length) : outAbs;
    return { outputAbsPath: outAbs, outputRelUrl: relUrl, durationMs: { ...state.durationMs, persist: Date.now() - t0 } };
  } catch (err) {
    return { errors: [`persistImage threw: ${err instanceof Error ? err.message : String(err)}`], durationMs: { ...state.durationMs, persist: Date.now() - t0 } };
  }
}

export function buildGraph() {
  const g = new StateGraph(PanelStateAnnotation)
    .addNode("buildPrompt", buildPromptNode)
    .addNode("generateImage", generateImageNode)
    .addNode("persistImage", persistImageNode)
    .addEdge(START, "buildPrompt")
    .addEdge("buildPrompt", "generateImage")
    .addEdge("generateImage", "persistImage")
    .addEdge("persistImage", END);
  return g.compile();
}

export const PROVIDER_INFO = { provider: PROVIDER, openaiModel: OPENAI_MODEL, openrouterModel: OPENROUTER_MODEL };
