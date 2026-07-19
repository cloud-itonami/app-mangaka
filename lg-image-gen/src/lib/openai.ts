/**
 * OpenAI image API wrappers (generations + edits + vision critic).
 */
import * as fs from "node:fs";

const GEN_URL = "https://api.openai.com/v1/images/generations";
const EDIT_URL = "https://api.openai.com/v1/images/edits";
const CHAT_URL = "https://api.openai.com/v1/chat/completions";

export const MODEL = process.env.LG_IMAGE_MODEL ?? "gpt-image-2";
export const SIZE = process.env.LG_IMAGE_SIZE ?? "1024x1536";
export const QUALITY = process.env.LG_IMAGE_QUALITY ?? "low";
export const VISION_MODEL = process.env.LG_VISION_MODEL ?? "gpt-4o-mini";

if (MODEL.startsWith("gpt-image-1")) throw new Error("gpt-image-1 is forbidden");

function key(): string {
  const k = process.env.OPENAI_API_KEY;
  if (!k) throw new Error("OPENAI_API_KEY not set");
  return k;
}

export async function generate(prompt: string, opts: { size?: string; quality?: string } = {}): Promise<string> {
  const r = await fetch(GEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${key()}` },
    body: JSON.stringify({ model: MODEL, prompt: prompt.slice(0, 32000), size: opts.size ?? SIZE, quality: opts.quality ?? QUALITY, n: 1 }),
  });
  if (!r.ok) throw new Error(`generate HTTP ${r.status}: ${(await r.text()).slice(0, 300)}`);
  const j: any = await r.json();
  if (j.error) throw new Error(`generate: ${j.error.message}`);
  const b64 = j.data?.[0]?.b64_json;
  if (!b64) throw new Error("generate: no b64");
  return b64;
}

export async function edit(prompt: string, imagePaths: string[], opts: { size?: string; quality?: string } = {}): Promise<string> {
  const fd = new FormData();
  fd.append("model", MODEL);
  fd.append("prompt", prompt.slice(0, 32000));
  fd.append("size", opts.size ?? SIZE);
  fd.append("quality", opts.quality ?? QUALITY);
  fd.append("n", "1");
  for (const p of imagePaths) {
    const buf = fs.readFileSync(p);
    fd.append("image[]", new Blob([buf as any], { type: "image/png" }), p.split("/").pop()!);
  }
  const r = await fetch(EDIT_URL, {
    method: "POST",
    headers: { Authorization: `Bearer ${key()}` },
    body: fd as any,
  });
  if (!r.ok) throw new Error(`edit HTTP ${r.status}: ${(await r.text()).slice(0, 300)}`);
  const j: any = await r.json();
  if (j.error) throw new Error(`edit: ${j.error.message}`);
  const b64 = j.data?.[0]?.b64_json;
  if (!b64) throw new Error("edit: no b64");
  return b64;
}

export interface RichCritique {
  score: number;                    // 1-10
  settingMatch: boolean;
  charactersMatch: boolean;
  hasUnwantedText: boolean;
  compositionScore: number;         // 0-1
  expressionConcrete: boolean;
  propsRecognized: string[];
  notes: string;
}

/**
 * Vision critic with rich axes for Q_i computation.
 */
export async function critique(
  imagePath: string,
  expectedSetting: string,
  expectedCharacters: string[],
  shot: string,
  expectedProps: string[] = [],
  expectedSignals: { character: string; signals: string[] }[] = [],
  visualStyle: string = "",
): Promise<RichCritique> {
  const buf = fs.readFileSync(imagePath);
  const dataUrl = `data:image/png;base64,${buf.toString("base64")}`;
  const sys = "You are a strict manga art director critiquing a panel for a Weekly Shounen Jump publishing pipeline. Evaluate against multiple axes. Respond with VALID JSON only. No prose.";
  const propsLine = expectedProps.length > 0 ? `\n- Expected props (which appear in image?): ${expectedProps.join(", ")}` : "";
  const signalsLine = expectedSignals.length > 0
    ? `\n- Expected physical signals: ${expectedSignals.map((s) => `${s.character}: ${s.signals.join(", ")}`).join(" / ")}`
    : "";
  const styleLine = visualStyle ? `\n- Visual style intent: ${visualStyle}` : "";
  const user = `Evaluate this generated manga panel against the spec:
- Expected setting: ${expectedSetting}
- Expected characters: ${expectedCharacters.join(", ") || "none"}
- Shot type: ${shot}${propsLine}${signalsLine}${styleLine}

Return JSON exactly with these fields:
{
  "score": <int 1-10, overall manga quality>,
  "settingMatch": <bool>,
  "charactersMatch": <bool>,
  "hasUnwantedText": <bool>,
  "compositionScore": <float 0-1, manga composition: rule of thirds, depth, framing, dynamic line>,
  "expressionConcrete": <bool, focal char shows concrete physical signals (sweat/tear/wide eyes/etc) instead of generic emotion>,
  "propsRecognized": [<string>, ...],
  "notes": "<2 sentences max — strongest weakness>"
}`;
  const r = await fetch(CHAT_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${key()}` },
    body: JSON.stringify({
      model: VISION_MODEL,
      messages: [
        { role: "system", content: sys },
        { role: "user", content: [{ type: "text", text: user }, { type: "image_url", image_url: { url: dataUrl } }] },
      ],
      response_format: { type: "json_object" },
      max_tokens: 500,
    }),
  });
  if (!r.ok) throw new Error(`critique HTTP ${r.status}: ${(await r.text()).slice(0, 300)}`);
  const j: any = await r.json();
  const txt = j.choices?.[0]?.message?.content ?? "{}";
  try {
    const p = JSON.parse(txt);
    return {
      score: Number(p.score) || 0,
      settingMatch: Boolean(p.settingMatch),
      charactersMatch: Boolean(p.charactersMatch),
      hasUnwantedText: Boolean(p.hasUnwantedText),
      compositionScore: Math.max(0, Math.min(1, Number(p.compositionScore) || 0)),
      expressionConcrete: Boolean(p.expressionConcrete),
      propsRecognized: Array.isArray(p.propsRecognized) ? p.propsRecognized.map(String) : [],
      notes: String(p.notes ?? ""),
    };
  } catch {
    return { score: 0, settingMatch: false, charactersMatch: false, hasUnwantedText: false, compositionScore: 0, expressionConcrete: false, propsRecognized: [], notes: `parse-fail: ${txt.slice(0, 100)}` };
  }
}

/** Q_p (prompt quality) — 0..1 */
export function computeQp(manifest: any): { Q_p: number; breakdown: Record<string, number> } {
  const fields = [
    manifest.sceneSubject, manifest.focusCharacter,
    (manifest.allCharacters?.length ?? 0) > 0,
    (manifest.props?.length ?? 0) > 0,
    manifest.shot, manifest.panelLayout, manifest.dialogues, manifest.scriptEntryIndices ?? manifest.panelIndex,
  ];
  const completeness = fields.filter(Boolean).length / 8;
  const desc = String(manifest.visual ?? "");
  const words = desc.split(/\s+/).filter(Boolean);
  const specificity = words.length > 0 ? Math.min(1, new Set(words.map((w: string) => w.toLowerCase())).size / Math.max(1, words.length) * 2) : 0;
  const allChars: string[] = manifest.allCharacters ?? manifest.characters ?? [];
  const charDistinction = allChars.length <= 1 ? 1 : 1; // refined by image critic
  const continuity = ((manifest.precedingBeat ? 1 : 0) + (manifest.followingBeat ? 1 : 0)) / 2;
  const propDensity = Math.min(1, (manifest.props?.length ?? 0) / 2);
  const visualStyleClarity = manifest.visualStyle ? 1 : 0;
  const breakdown = {
    completeness: 0.25 * completeness,
    specificity: 0.20 * specificity,
    char_distinction: 0.20 * charDistinction,
    continuity: 0.15 * continuity,
    prop_density: 0.10 * propDensity,
    visualStyle_clarity: 0.10 * visualStyleClarity,
  };
  const Q_p = Object.values(breakdown).reduce((a, b) => a + b, 0);
  return { Q_p: Math.max(0, Math.min(1, Q_p)), breakdown };
}

/** Q_i (image quality) — 0..1 */
export function computeQi(c: RichCritique, expectedProps: string[] = []): { Q_i: number; breakdown: Record<string, number> } {
  const propsVisible = expectedProps.length === 0 ? 1 : Math.min(1, c.propsRecognized.length / expectedProps.length);
  const breakdown = {
    critic: 0.25 * (c.score / 10),
    setting: 0.15 * (c.settingMatch ? 1 : 0),
    char: 0.15 * (c.charactersMatch ? 1 : 0),
    text_clean: 0.10 * (c.hasUnwantedText ? 0 : 1),
    composition: 0.15 * c.compositionScore,
    expression: 0.10 * (c.expressionConcrete ? 1 : 0),
    props_visible: 0.10 * propsVisible,
  };
  const Q_i = Object.values(breakdown).reduce((a, b) => a + b, 0);
  return { Q_i: Math.max(0, Math.min(1, Q_i)), breakdown };
}

/** MiniMax combine: 0.5·min + 0.3·geo + 0.2·max */
export function combineQ(Q_p: number, Q_i: number): number {
  const Q_min = Math.min(Q_p, Q_i);
  const Q_geo = Math.sqrt(Math.max(0, Q_p * Q_i));
  const Q_max = Math.max(Q_p, Q_i);
  return Math.max(0, Math.min(1, 0.5 * Q_min + 0.3 * Q_geo + 0.2 * Q_max));
}
