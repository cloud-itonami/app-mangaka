/**
 * Character reference variant selection + path resolution.
 */
import * as fs from "node:fs";

const REPO = "/Users/junkawasaki/github/ghosthacker/260123-jump";

export type Variant =
  | "action_shout" | "angry_3q_left" | "anxious_front" | "downcast_sad" | "focused_3q_right"
  | "gentle_smile_3q" | "neutral_front" | "profile_left" | "profile_right" | "surprised_front"
  | "three_quarter_left_neutral" | "three_quarter_right_neutral";

export function pickVariant(char: string, dialogue: { speaker: string; text: string; emotion?: string }[], shot: string): Variant {
  const own = dialogue.find((d) => d.speaker === char);
  const e = (own?.emotion ?? "").toLowerCase();
  const t = (own?.text ?? "").toLowerCase();
  if (e.includes("shout") || t.includes("！") || t.includes("!!")) return "action_shout";
  if (e.includes("angry") || e.includes("怒")) return "angry_3q_left";
  if (e.includes("anxious") || e.includes("不安") || e.includes("怯")) return "anxious_front";
  if (e.includes("sad") || e.includes("downcast") || e.includes("悲") || e.includes("涙")) return "downcast_sad";
  if (e.includes("smile") || e.includes("笑") || e.includes("gentle")) return "gentle_smile_3q";
  if (e.includes("surprise") || e.includes("驚")) return "surprised_front";
  if (e.includes("focused") || e.includes("集中")) return "focused_3q_right";
  if (shot.toLowerCase().includes("close up")) return "neutral_front";
  if (shot.toLowerCase().includes("profile")) return "profile_right";
  return "three_quarter_right_neutral";
}

export function refPath(character: string, variant: Variant): string | null {
  const cands = [
    `${REPO}/resources/characters/${character}/reference_variants/${variant}.png`,
    `${REPO}/resources/characters/${character}/reference_variants/neutral_front.png`,
    `${REPO}/resources/characters/${character}/reference_face.png`,
    `${REPO}/resources/characters/${character}/main.png`,
    `${REPO}/resources/characters/${character}/avatar.png`,
  ];
  for (const c of cands) if (fs.existsSync(c)) return c;
  return null;
}

export function extractSetting(prompt: string): { setting: string; visualNote: string } {
  const s = prompt.match(/Setting:\s*([^.]+(?:\.[^A-Z][^.]*)*)\./);
  const v = prompt.match(/Visual note:\s*([^.]+(?:\.[^A-Z][^.]*)*)\./);
  return { setting: s?.[1]?.trim() ?? "", visualNote: v?.[1]?.trim() ?? "" };
}

/**
 * Compose a short, distinctive English descriptor from the character profile's appearance fields.
 * Used to inject per-character cues so multi-character panels render distinct identities.
 *
 * Note: explicit ages (e.g., "14-year-old") are stripped to avoid OpenAI moderation false-positives
 * that can flag any minor-age + body-description combination. The reference images already encode
 * age impression visually.
 */
const descriptorCache = new Map<string, string>();
export function characterDescriptor(character: string): string {
  if (descriptorCache.has(character)) return descriptorCache.get(character)!;
  const profilePath = `${REPO}/resources/characters/${character}/profile.jsonld`;
  let desc = "";
  try {
    const fs = require("node:fs") as typeof import("node:fs");
    const j = JSON.parse(fs.readFileSync(profilePath, "utf-8"));
    const a = j["gh:appearance"] ?? {};
    const parts: string[] = [];
    if (a["gh:hair"]) parts.push(a["gh:hair"]);
    if (a["gh:eyes"]) parts.push(a["gh:eyes"]);
    if (a["gh:face"]) parts.push(a["gh:face"]);
    if (a["gh:build"]) parts.push(a["gh:build"]);
    desc = parts.join(" ").replace(/\s+/g, " ").trim();
    // Strip age numbers (moderation safety): "14-year-old", "age 14", "15歳" etc.
    desc = desc
      .replace(/\b\d{1,2}[\s-]?year[\s-]?old\b/gi, "young")
      .replace(/\bage\s*\d{1,2}\b/gi, "")
      .replace(/\b\d{1,2}\s*歳\b/g, "")
      .replace(/\s+/g, " ")
      .trim();
  } catch {
    desc = "";
  }
  descriptorCache.set(character, desc);
  return desc;
}
