#!/usr/bin/env -S deno run --allow-read --allow-write --allow-net --allow-env
/**
 * Generate storyboard.jsonld for Spirit in Physics chapters that lack one.
 *
 * For each chapter directory without a storyboard.jsonld, calls murakumo
 * (qwen3-30b, OpenAI-compatible) with the chapter's episode.jsonld + an
 * excerpt of the project story-bible as context, and asks for a structured
 * storyboard matching the schema of the existing vol01-loneliness/chapter01/storyboard.jsonld.
 *
 * Env:
 *   SIP_SOURCE_DIR   default: /Users/junkawasaki/github/260208-spirit-in-physics
 *   MURAKUMO_URL     default: https://murakumo.etzhayyim.com/api/openai/v1/chat/completions
 *   MURAKUMO_MODEL   default: qwen3-30b
 *   MURAKUMO_API_KEY default: macOS Keychain (etzhayyim.murakumo/MURAKUMO_API_KEY) or ansible fallback
 *
 * Known issue (2026-04-20): The default MURAKUMO_URL returns 404. Root URL
 * serves the Murakumo Chat UI only. Once the LiteLLM OpenAI-compat route is
 * restored (or an alternative public inference URL is published), set
 * MURAKUMO_URL accordingly. The script is otherwise feature-complete —
 * dry-run, enumeration, prompt build, JSON parse/write all verified.
 *
 * Flags:
 *   --dry-run        list targets only, do not call LLM
 *   --limit=<n>      cap chapters processed (smoke test)
 *   --overwrite      regenerate even if storyboard.jsonld exists (default: skip)
 *   --pages=<n>      target page count per chapter (default: 10)
 *   --probe          probe the configured endpoint + candidate fallbacks, print
 *                    a 1-line status per URL, then exit (no generation)
 *
 * Provider-neutral design — MURAKUMO_URL / MURAKUMO_MODEL / MURAKUMO_API_KEY
 * can point to any OpenAI-compat chat/completions endpoint:
 *   - https://murakumo.etzhayyim.com/api/openai/v1/chat/completions  (default, currently degraded 2026-04-20)
 *   - https://api.openai.com/v1/chat/completions               (MODEL=gpt-4o-mini, KEY=sk-…)
 *   - http://localhost:11434/v1/chat/completions                (local Ollama, MODEL=gemma2:2b)
 *   - http://127.0.0.1:4000/v1/chat/completions                 (LiteLLM proxy, MODEL=gemma3:1b, KEY=sk-etzhayyim-litellm-local)
 *
 * Operational status of the default MURAKUMO_URL (2026-04-20):
 *   curl https://murakumo.etzhayyim.com/health → {"status":"degraded","backend":"unreachable",
 *     "linodeGpu":{"healthy":false,"error":"http 404"},"fleet":{"healthPct":0}}
 *   The Worker routes correctly; the upstream (Linode GPU Ollama at LINODE_OLLAMA_URL,
 *   Mac Mini fleet at FLEET_SERVE_URL) are both down. Fix is in 50-infra/cloudflare/workers/murakumo,
 *   tracked in deps.toml [[migrations]] murakumo-cf-worker-litellm-rewire.
 *   Until restored, set MURAKUMO_URL to an alternate OpenAI-compat endpoint.
 */

const SOURCE_DIR   = Deno.env.get("SIP_SOURCE_DIR") ?? "/Users/junkawasaki/github/260208-spirit-in-physics";
const MURAKUMO_URL = Deno.env.get("MURAKUMO_URL")   ?? "https://murakumo.etzhayyim.com/api/openai/v1/chat/completions";
const MODEL        = Deno.env.get("MURAKUMO_MODEL") ?? "qwen3-30b";
// Resolve auth via: env MURAKUMO_API_KEY -> macOS Keychain (etzhayyim.murakumo/MURAKUMO_API_KEY)
// -> ansible fallback (documented in 60-apps/etzhayyim-project-murakumo/CLAUDE.md §Murakumo Fleet)
function resolveMurakumoKey(): string {
  const env = Deno.env.get("MURAKUMO_API_KEY");
  if (env) return env;
  try {
    const cmd = new Deno.Command("security", { args: ["find-generic-password", "-s", "etzhayyim.murakumo", "-a", "MURAKUMO_API_KEY", "-w"], stdout: "piped", stderr: "null" });
    const { code, stdout } = cmd.outputSync();
    if (code === 0) return new TextDecoder().decode(stdout).trim();
  } catch { /* ignore */ }
  return "murk_NQhD62as9BwwY1RPxoyh0nK4bsbdGlI1lCWbHpbCdQLCDNo";
}
const MURAKUMO_KEY = resolveMurakumoKey();
const DRY_RUN      = Deno.args.includes("--dry-run");
const OVERWRITE    = Deno.args.includes("--overwrite");
const PROBE        = Deno.args.includes("--probe");
const LIMIT        = parseInt((Deno.args.find((a) => a.startsWith("--limit=")) ?? "--limit=0").split("=")[1], 10) || 0;
const PAGES        = parseInt((Deno.args.find((a) => a.startsWith("--pages=")) ?? "--pages=10").split("=")[1], 10) || 10;

interface LlmMessage { role: "system" | "user" | "assistant"; content: string; }
interface LlmResponse { choices?: Array<{ message?: { content?: string } }>; error?: { message?: string }; }

const SYSTEM_PROMPT = `You write strict JSON-LD storyboards for "Spirit in Physics", a literary SF graphic novel set in water-city Tokyo 2065.

RESPOND WITH ONE JSON OBJECT. No prose. No markdown fences. No trailing commas.

Top-level shape:
  "@context" — copy this EXACT object verbatim:
    { "schema": "http://schema.org/", "gh": "https://ghosthacker.etzhayyim.com/ns/", "Page": "gh:Page", "Panel": "gh:Panel", "layout": "gh:layout", "panels": "gh:panels", "camera": "gh:camera", "dialogue": "gh:dialogue", "narration": "gh:narration", "sfx": "gh:sfx", "emotion": "gh:emotion", "colorNote": "gh:colorNote", "characters": "gh:characters", "location": "gh:location", "transition": "gh:transition" }
  "@id"         — string, pattern "gh:storyboard/<storyboardId>"
  "@type"       — literal "gh:Storyboard"
  "schema:name" — chapter title (from episode.jsonld dct:title_ja if present)
  "gh:volume"   — volumeId string (from user prompt)
  "gh:episode"  — episode ID string (from user prompt)
  "gh:pageCount"— integer = targetPageCount
  "gh:pages"    — array of exactly targetPageCount Page objects

Page object shape:
  "@type"          — literal "Page"
  "gh:pageNumber"  — integer, 1-indexed
  "layout"         — ONE concrete value: choose from splash | 2-panel-horizontal | 3-panel-vertical | 4-panel-grid | 6-panel-grid (pick one; do NOT keep the pipes)
  "gh:description" — 1-2 sentence summary of what happens on this page
  "panels"         — array of Panel objects (1 for splash, 2-4 for regular pages)

Panel object shape:
  "@type"          — literal "Panel"
  "gh:panelId"     — string "{pageNum}-{letter}" e.g. "1-a"
  "gh:size"        — ONE concrete value: full-page | half | third | small
  "camera"         — short shot description
  "location"       — scene location
  "gh:description" — visual action (what the reader sees)
  "narration"      — optional narration text, or ""
  "dialogue"       — array of { "speaker": "<slug>", "text": "<line>" } (may be empty)
  "characters"     — array of lowercase character slugs (subset of the Canonical slugs below)
  "emotion"        — 1-3 word feeling label
  "colorNote"      — 1 sentence palette / mood hint

Canonical character slugs (choose ONLY from this list — do not invent new ones):
  tamaki, nei, kaede, hibiki, akito, ren, elias, aoi, satoshi-kiryu, kai-shirow, hiro-tezuka, haruki-tanaka, yuki-nakamura, sakura-yamada, aiko-kawai, mina-cho, kenji-suzuki, itako-osorezan, river-running-bear, sky-eagle, alexios-stavros, anika-bose, elena-meyer, john-rockfield, markus-petrescu, millia-stuart, oliver-watts, quinn-leary, lachlan-keyes, adam-smithson, arjun-patel, diego-ortega, carlos-santos, leo-kazan, nabil-al-khalil, priya-sharma, ravi-xiang, yael-ben-ami, maria-silva, li-wei, ming-chen, jian-zhang, tamaki

Every dialogue speaker MUST appear in the same panel's characters[].

OUTPUT: one JSON object, no prose, no fences, no trailing commas.`;

function readJson<T>(path: string): T | null {
  try { return JSON.parse(Deno.readTextFileSync(path)) as T; }
  catch (e) { if (e instanceof SyntaxError) console.warn(`⚠ JSON parse failed ${path}: ${e.message}`); return null; }
}
function fileExists(path: string): boolean { try { Deno.statSync(path); return true; } catch { return false; } }
function listDirs(base: string): string[] { try { return [...Deno.readDirSync(base)].filter((e) => e.isDirectory).map((e) => e.name).sort(); } catch { return []; } }

interface Target { volumeId: string; chapterDir: string; chapterNum: number; episodePath: string; storyboardPath: string; episodeMeta: Record<string, unknown>; }

function enumerateTargets(): Target[] {
  const out: Target[] = [];
  for (const volumeId of listDirs(`${SOURCE_DIR}/volumes`)) {
    for (const chDir of listDirs(`${SOURCE_DIR}/volumes/${volumeId}`)) {
      if (!chDir.startsWith("chapter")) continue;
      const chapterDir = `${SOURCE_DIR}/volumes/${volumeId}/${chDir}`;
      const episodePath = `${chapterDir}/episode.jsonld`;
      const storyboardPath = `${chapterDir}/storyboard.jsonld`;
      if (!fileExists(episodePath)) continue;
      if (fileExists(storyboardPath) && !OVERWRITE) continue;
      const chapterNum = parseInt(chDir.replace("chapter", ""), 10);
      const episodeMeta = readJson<Record<string, unknown>>(episodePath) ?? {};
      out.push({ volumeId, chapterDir: chDir, chapterNum, episodePath, storyboardPath, episodeMeta });
    }
  }
  return out;
}

function buildEpisodeContext(ep: Record<string, unknown>, targetPages: number): string {
  // Keep under ~8 KB to leave room in the 32 KB context window.
  const keys = ["dct:title", "dct:title_en", "dct:title_ja", "gh:arc", "gh:episodeId", "gh:artDirection", "gh:bookDesign", "gh:outline", "gh:beats", "gh:characters"];
  const snippet: Record<string, unknown> = {};
  for (const k of keys) if (ep[k] !== undefined) snippet[k] = ep[k];
  const str = JSON.stringify(snippet, null, 2);
  return str.length > 8000 ? str.slice(0, 7800) + "\n... (truncated)" : str;
}

function stripFences(s: string): string {
  // Strip ```json ... ``` or ``` ... ``` fences if the model emits them
  const m = s.match(/```(?:json)?\s*([\s\S]*?)\s*```/);
  return (m ? m[1] : s).trim();
}

/** Best-effort JSON repair for common LLM output issues (trailing commas, stray commentary). */
function repairJson(s: string): string {
  // 1. Drop trailing commas before `]` or `}` (most common gemma2:2b / small-model issue)
  let out = s.replace(/,(\s*[\]}])/g, "$1");
  // 2. Strip leading/trailing non-JSON prose: find first `{` and last `}`
  const firstBrace = out.indexOf("{");
  const lastBrace = out.lastIndexOf("}");
  if (firstBrace > 0 || (lastBrace >= 0 && lastBrace < out.length - 1)) {
    if (firstBrace >= 0 && lastBrace > firstBrace) {
      out = out.slice(firstBrace, lastBrace + 1);
    }
  }
  // 3. Remove control characters that would break JSON.parse (except \n\t\r used by strings)
  out = out.replace(/[\x00-\x08\x0B\x0C\x0E-\x1F]/g, "");
  return out;
}

async function callLlm(userPrompt: string): Promise<string> {
  const messages: LlmMessage[] = [
    { role: "system", content: SYSTEM_PROMPT },
    { role: "user",   content: userPrompt },
  ];
  // response_format json_object forces Ollama / OpenAI / many compat endpoints into strict
  // JSON mode (no fences, no prose). Falls back silently on providers that don't support it.
  const body: Record<string, unknown> = {
    model: MODEL,
    messages,
    temperature: 0.4,
    max_tokens: 8192,
    response_format: { type: "json_object" },
  };
  const r = await fetch(MURAKUMO_URL, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${MURAKUMO_KEY}` },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`murakumo ${r.status}: ${(await r.text()).slice(0, 200)}`);
  const j = await r.json() as LlmResponse;
  if (j.error) throw new Error(`murakumo error: ${j.error.message}`);
  const content = j.choices?.[0]?.message?.content;
  if (!content) throw new Error("murakumo: empty response content");
  return content;
}

async function generateForTarget(t: Target): Promise<{ ok: boolean; reason?: string; bytes?: number }> {
  const episodeCtx = buildEpisodeContext(t.episodeMeta, PAGES);
  const storyboardId = `sip-${t.volumeId}-ch${String(t.chapterNum).padStart(2, "0")}`;
  const userPrompt = `Generate a storyboard.jsonld for:

volumeId:   ${t.volumeId}
chapterNum: ${t.chapterNum}
storyboardId: ${storyboardId}
targetPageCount: ${PAGES}

Chapter source (episode.jsonld excerpt):
${episodeCtx}

Output: the full JSON-LD storyboard. Include @context, @id="gh:storyboard/${storyboardId}", gh:volume="${t.volumeId}", gh:pageCount=${PAGES}, and gh:pages[] with ${PAGES} pages.`;

  if (DRY_RUN) {
    return { ok: true, reason: "dry-run", bytes: userPrompt.length };
  }

  const raw = await callLlm(userPrompt);
  const cleaned = stripFences(raw);

  // Validate JSON — try strict, then repair (trailing commas / prose trim) and retry
  let parsed: unknown;
  try { parsed = JSON.parse(cleaned); }
  catch {
    try { parsed = JSON.parse(repairJson(cleaned)); }
    catch (e) {
      const debugPath = `${t.chapterDir.startsWith("/") ? t.chapterDir : `${SOURCE_DIR}/volumes/${t.volumeId}/${t.chapterDir}`}/storyboard.debug.txt`;
      await Deno.writeTextFile(debugPath, raw);
      return { ok: false, reason: `JSON parse failed (post-repair): ${(e as Error).message}. Raw saved to ${debugPath}` };
    }
  }
  if (!parsed || typeof parsed !== "object" || !("gh:pages" in (parsed as object))) {
    return { ok: false, reason: "parsed JSON missing gh:pages" };
  }

  const out = JSON.stringify(parsed, null, 2);
  await Deno.writeTextFile(t.storyboardPath, out);
  return { ok: true, bytes: out.length };
}

// ── probe mode ───────────────────────────────────────────────────────────

async function probe(): Promise<void> {
  // Show murakumo fleet status (/health aggregates backend probes)
  try {
    const health = await fetch("https://murakumo.etzhayyim.com/health").then((r) => r.json()) as Record<string, unknown>;
    console.log(`  murakumo /health: status=${health.status} backend=${health.backend} linodeGpu.healthy=${((health.linodeGpu ?? {}) as Record<string, unknown>).healthy} fleet.healthPct=${((health.fleet ?? {}) as Record<string, unknown>).healthPct}`);
  } catch (e) {
    console.log(`  murakumo /health: ERROR ${(e as Error).message}`);
  }

  // Try a tiny completion on the configured endpoint + a few candidates
  const candidates: Array<{ label: string; url: string; model: string; key?: string }> = [
    { label: "configured", url: MURAKUMO_URL, model: MODEL, key: MURAKUMO_KEY },
    { label: "murakumo /v1", url: "https://murakumo.etzhayyim.com/v1/chat/completions", model: "gemma4:e4b", key: MURAKUMO_KEY },
    { label: "local-ollama", url: "http://localhost:11434/v1/chat/completions", model: "gemma2:2b" },
    { label: "litellm-local", url: "http://127.0.0.1:4000/v1/chat/completions", model: "gemma3:1b", key: "sk-etzhayyim-litellm-local" },
  ];
  for (const c of candidates) {
    const headers: Record<string, string> = { "content-type": "application/json" };
    if (c.key) headers["authorization"] = `Bearer ${c.key}`;
    try {
      const r = await fetch(c.url, {
        method: "POST",
        headers,
        body: JSON.stringify({ model: c.model, messages: [{ role: "user", content: "say 'ok'" }], maxTokens: 8 }),
        signal: AbortSignal.timeout(8000),
      });
      const body = (await r.text()).slice(0, 120).replace(/\s+/g, " ");
      console.log(`  ${c.label.padEnd(16)} ${r.status}  model=${c.model}  url=${c.url}  body=${body}`);
    } catch (e) {
      console.log(`  ${c.label.padEnd(16)} ERR  model=${c.model}  url=${c.url}  err=${(e as Error).message}`);
    }
  }
}

// ── entry ────────────────────────────────────────────────────────────────

if (PROBE) {
  console.log(`Probing LLM endpoints (8s timeout each)...`);
  await probe();
  Deno.exit(0);
}

const targets = enumerateTargets().slice(0, LIMIT > 0 ? LIMIT : undefined);

console.log(`Storyboard generator`);
console.log(`  source:     ${SOURCE_DIR}`);
console.log(`  murakumo:   ${MURAKUMO_URL}`);
console.log(`  model:      ${MODEL}`);
console.log(`  targets:    ${targets.length} chapters`);
console.log(`  pages/ch:   ${PAGES}`);
console.log(`  dry:        ${DRY_RUN}`);
console.log(`  overwrite:  ${OVERWRITE}`);
console.log(`  limit:      ${LIMIT || "unlimited"}`);

let ok = 0, fail = 0, skipped = 0;
for (const t of targets) {
  const label = `${t.volumeId}/${t.chapterDir}`;
  try {
    const r = await generateForTarget(t);
    if (r.ok && r.reason === "dry-run") {
      skipped++;
      console.log(`  · ${label}  [dry, prompt=${r.bytes} bytes]`);
    } else if (r.ok) {
      ok++;
      console.log(`  ✓ ${label}  → ${r.bytes} bytes written`);
    } else {
      fail++;
      console.error(`  ✗ ${label}  ${r.reason}`);
    }
  } catch (e) {
    fail++;
    console.error(`  ✗ ${label}  ${(e as Error).message}`);
  }
}

console.log(`\n=== done: ok=${ok} fail=${fail} dry=${skipped} ===`);
