/**
 * Gemini 3 Pro Image via OpenRouter — provider for hybrid pipeline.
 */
const URL = "https://openrouter.ai/api/v1/chat/completions";
const MODEL = process.env.LG_GEMINI_MODEL ?? "google/gemini-3-pro-image-preview";

export async function generateGemini(prompt: string): Promise<string> {
  const apiKey = process.env.OPENROUTER_API_KEY;
  if (!apiKey) throw new Error("OPENROUTER_API_KEY not set");
  const r = await fetch(URL, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
      "HTTP-Referer": "https://ghosthacker.etzhayyim.com",
      "X-Title": "ghosthacker-arc0-1-hybrid",
    },
    body: JSON.stringify({
      model: MODEL,
      messages: [{ role: "user", content: [{ type: "text", text: prompt }] }],
      modalities: ["text", "image"],
      image_config: { aspect_ratio: "3:4", image_size: "1K" },
      stream: false,
    }),
  });
  if (!r.ok) throw new Error(`Gemini HTTP ${r.status}: ${(await r.text()).slice(0, 300)}`);
  const j: any = await r.json();
  if (j.error) throw new Error(`Gemini: ${j.error.message ?? JSON.stringify(j.error)}`);
  const raw = j.choices?.[0]?.message?.images?.[0]?.image_url;
  const url = typeof raw === "string" ? raw : raw?.url;
  if (!url) throw new Error(`Gemini: no image (${(j.choices?.[0]?.message?.content ?? "").slice(0, 200)})`);
  if (url.startsWith("data:")) return url.slice(url.indexOf(",") + 1);
  const r2 = await fetch(url);
  return Buffer.from(await r2.arrayBuffer()).toString("base64");
}

/** Route by tone: Gemini for atmospheric / dark, OpenAI for clean / dynamic */
const GEMINI_TONES = new Set(["ominous", "tense", "contemplative", "quiet", "emotional"]);
const OPENAI_TONES = new Set(["action", "triumph", "comedic"]);

export function selectProvider(tone: string | undefined, visualStyle: string | undefined): "openai" | "gemini" {
  const t = (tone ?? "").toLowerCase();
  const vs = (visualStyle ?? "").toLowerCase();
  if (GEMINI_TONES.has(t)) return "gemini";
  if (OPENAI_TONES.has(t)) return "openai";
  // visualStyle fallback
  if (vs === "cinematic-close") return "gemini";
  if (vs === "anime-action") return "openai";
  // default
  return "openai";
}
