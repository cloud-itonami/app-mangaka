#!/usr/bin/env -S deno run --allow-read --allow-write --allow-net --allow-env --allow-run
/**
 * Generate composited page images from storyboard.jsonld.
 *
 * Flow per chapter:
 *   storyboard.jsonld → per-panel manga prompt
 *                     → POST /xrpc/com.etzhayyim.apps.llm.generateImage (flux-1-schnell, 1024x1024)
 *                     → cache in prompts/{sha256(prompt)}.jpg
 *   per-page: ImageMagick `convert ... -resize 1080x -append` → pages/page-{N}.jpg (1080 wide, webtoon vertical)
 *   write sidecar page-images.json  (consumed by import-sip.ts stagePages)
 *
 * MVP choices (ratified):
 *   - All layouts collapse to vertical stack (webtoon smartphone reading)
 *   - flux-1-schnell 4 steps (speed priority; tune via --num-steps)
 *   - x-kotodama-verified header for PDS image-gen auth (mirrors mangaka cmdGenerateImage)
 *   - prompt cache by SHA-256 of final prompt (idempotent, mid-run resumable)
 *
 * Env:
 *   SIP_SOURCE_DIR   default: /Users/junkawasaki/github/260208-spirit-in-physics
 *   PDS_BASE         default: https://atproto.etzhayyim.com/xrpc
 *
 * Flags:
 *   --dry-run         enumerate targets, no network / image calls
 *   --limit=<n>       cap chapters processed (smoke)
 *   --only=<vol>/<ch> explicit target (e.g. --only=vol01-loneliness/chapter02)
 *   --num-steps=<n>   flux sampling steps (default 4; 8=balanced; 25=high-quality)
 *   --overwrite       regenerate page-{N}.jpg even when cached
 *   --panel-cache=<dir>  central panel cache dir (default: $SIP_SOURCE_DIR/.panel-cache)
 */

const SOURCE_DIR  = Deno.env.get("SIP_SOURCE_DIR") ?? "/Users/junkawasaki/github/260208-spirit-in-physics";
const PDS_BASE    = Deno.env.get("PDS_BASE") ?? "https://atproto.etzhayyim.com/xrpc";
const DRY_RUN     = Deno.args.includes("--dry-run");
const OVERWRITE   = Deno.args.includes("--overwrite");
const LIMIT       = parseInt((Deno.args.find((a) => a.startsWith("--limit=")) ?? "--limit=0").split("=")[1], 10) || 0;
const ONLY        = (Deno.args.find((a) => a.startsWith("--only=")) ?? "--only=").split("=")[1];
const NUM_STEPS   = parseInt((Deno.args.find((a) => a.startsWith("--num-steps=")) ?? "--num-steps=4").split("=")[1], 10) || 4;
const PANEL_CACHE = (Deno.args.find((a) => a.startsWith("--panel-cache=")) ?? `--panel-cache=${SOURCE_DIR}/.panel-cache`).split("=")[1];

const TARGET_PAGE_WIDTH = 1080;

// ── prompt construction ──────────────────────────────────────────────────

const STYLE_SUFFIX = "black and white ink, clean lines, screentone shading, professional manga art, high contrast, dramatic composition";

function buildPanelPrompt(panel: PanelDef): string {
  const parts = [
    "manga panel illustration,",
    panel.camera ? `${panel.camera},` : "",
    panel.location ? `${panel.location},` : "",
    panel["gh:description"] ?? "",
    panel.emotion ? `mood: ${panel.emotion},` : "",
    panel.colorNote ? `${panel.colorNote},` : "",
    STYLE_SUFFIX,
  ].filter(Boolean).join(" ").trim();
  return parts.replace(/\s+/g, " ").slice(0, 1500);
}

// ── types from storyboard.jsonld ─────────────────────────────────────────

interface PanelDef {
  "@type"?: string;
  "gh:panelId"?: string;
  "gh:size"?: string;
  camera?: string;
  location?: string;
  "gh:description"?: string;
  dialogue?: Array<{ speaker?: string; text?: string }>;
  narration?: string;
  characters?: string[];
  emotion?: string;
  colorNote?: string;
}

interface PageDef {
  "@type"?: string;
  "gh:pageNumber"?: number;
  layout?: string;
  "gh:description"?: string;
  panels?: PanelDef[];
}

interface Storyboard {
  "@id"?: string;
  "schema:name"?: string;
  "gh:pageCount"?: number;
  "gh:pages"?: PageDef[];
}

// ── helpers ──────────────────────────────────────────────────────────────

function fileExists(path: string): boolean { try { Deno.statSync(path); return true; } catch { return false; } }
function ensureDir(path: string): void { try { Deno.mkdirSync(path, { recursive: true }); } catch { /* exists */ } }
function listDirs(base: string): string[] { try { return [...Deno.readDirSync(base)].filter((e) => e.isDirectory).map((e) => e.name).sort(); } catch { return []; } }

async function sha256Hex(bytes: Uint8Array | string): Promise<string> {
  const buf = typeof bytes === "string" ? new TextEncoder().encode(bytes) : bytes;
  const h = await crypto.subtle.digest("SHA-256", buf);
  return [...new Uint8Array(h)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Sanitize a prompt flagged by flux-1-schnell's NSFW filter. Strip close-range body
 *  modifiers + intimate terms, add safe-for-work modifiers. Not perfect (small-model
 *  classifier is unpredictable) but recovers most romantic-but-non-sexual panels. */
function sanitizePrompt(prompt: string): string {
  let out = prompt
    .replace(/extreme close-up \/ [^,]*/gi, "medium shot")
    .replace(/close-up \/ [^,]*'s (lips|mouth|face|eyes|jawline|neck|hands?)/gi, "medium shot character portrait")
    .replace(/close-up \/ [^,]*(lips|mouth)/gi, "medium shot character face")
    .replace(/(lips|mouth|bare|intimate|alone together|bedroom|bed)/gi, "")
    .replace(/\s+/g, " ")
    .trim();
  out += ", clothed, formal, safe for work, illustrative, non-sexual, atmospheric background";
  return out.slice(0, 1500);
}

async function callImageGen(prompt: string): Promise<Response> {
  return await fetch(`${PDS_BASE}/com.etzhayyim.apps.llm.generateImage`, {
    method: "POST",
    headers: { "content-type": "application/json", "x-kotodama-verified": "true" },
    body: JSON.stringify({ prompt, num_steps: NUM_STEPS }),
  });
}

async function generatePanelImage(prompt: string, cachedPath: string): Promise<{ hit: boolean; bytes: number; elapsedMs: number; sanitized: boolean }> {
  if (fileExists(cachedPath) && !OVERWRITE) {
    const info = Deno.statSync(cachedPath);
    return { hit: true, bytes: info.size, elapsedMs: 0, sanitized: false };
  }
  const t0 = Date.now();
  let r = await callImageGen(prompt);
  let sanitized = false;

  // NSFW filter (AiError: 3030) — retry once with sanitized prompt
  if (!r.ok) {
    const text = await r.text();
    if (text.includes("3030") || text.toLowerCase().includes("nsfw")) {
      sanitized = true;
      const safePrompt = sanitizePrompt(prompt);
      r = await callImageGen(safePrompt);
    } else {
      throw new Error(`image-gen ${r.status}: ${text.slice(0, 200)}`);
    }
  }
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`image-gen ${r.status} (post-sanitize): ${text.slice(0, 200)}`);
  }

  const buf = new Uint8Array(await r.arrayBuffer());
  await Deno.writeFile(cachedPath, buf);
  return { hit: false, bytes: buf.byteLength, elapsedMs: Date.now() - t0, sanitized };
}

async function composePage(panelPaths: string[], outPath: string): Promise<{ width: number; height: number }> {
  if (panelPaths.length === 0) throw new Error("no panels to compose");
  // Vertical stack, resize each to TARGET_PAGE_WIDTH wide.
  // ImageMagick: resize per input + vertical append + quality 88 JPEG
  const cmd = ["convert"];
  for (const p of panelPaths) cmd.push(p, "-resize", `${TARGET_PAGE_WIDTH}x`);
  cmd.push("-append", "-quality", "88", outPath);
  const proc = new Deno.Command(cmd[0], { args: cmd.slice(1), stdout: "null", stderr: "piped" });
  const { code, stderr } = await proc.output();
  if (code !== 0) throw new Error(`convert failed: ${new TextDecoder().decode(stderr).slice(0, 300)}`);

  // Probe output dims via ImageMagick `identify`
  const idProc = new Deno.Command("identify", { args: ["-format", "%w %h", outPath], stdout: "piped", stderr: "null" });
  const { code: ic, stdout } = await idProc.output();
  if (ic !== 0) return { width: TARGET_PAGE_WIDTH, height: 0 };
  const [wStr, hStr] = new TextDecoder().decode(stdout).trim().split(" ");
  return { width: parseInt(wStr, 10), height: parseInt(hStr, 10) };
}

// ── target enumeration ──────────────────────────────────────────────────

interface Target { volumeId: string; chapterDir: string; chapterPath: string; storyboardPath: string; outDir: string; cacheDir: string; sidecarPath: string; }

function enumerateTargets(): Target[] {
  const out: Target[] = [];
  for (const volumeId of listDirs(`${SOURCE_DIR}/volumes`)) {
    for (const chDir of listDirs(`${SOURCE_DIR}/volumes/${volumeId}`)) {
      if (!chDir.startsWith("chapter")) continue;
      const chapterPath = `${SOURCE_DIR}/volumes/${volumeId}/${chDir}`;
      const storyboardPath = `${chapterPath}/storyboard.jsonld`;
      if (!fileExists(storyboardPath)) continue;
      if (ONLY && `${volumeId}/${chDir}` !== ONLY) continue;
      out.push({
        volumeId,
        chapterDir: chDir,
        chapterPath,
        storyboardPath,
        outDir: `${chapterPath}/pages`,
        cacheDir: PANEL_CACHE,
        sidecarPath: `${chapterPath}/page-images.json`,
      });
    }
  }
  return LIMIT > 0 ? out.slice(0, LIMIT) : out;
}

// ── per-chapter processing ──────────────────────────────────────────────

interface PageSidecar {
  file: string;          // relative path from chapter dir, e.g. "pages/page-01.jpg"
  sha256: string;        // content hash (for uploadBlob CAS dedup)
  width: number;
  height: number;
  panelCount: number;
  panelIds: string[];    // e.g. ["02-01", "02-02"]
  bytes: number;
}

async function processChapter(t: Target): Promise<{ pages: number; panelsGenerated: number; panelsCached: number; skipped: number }> {
  const sb: Storyboard = JSON.parse(Deno.readTextFileSync(t.storyboardPath));
  const pages = sb["gh:pages"] ?? [];
  if (pages.length === 0) return { pages: 0, panelsGenerated: 0, panelsCached: 0, skipped: 0 };

  ensureDir(t.outDir);
  ensureDir(t.cacheDir);

  let panelsGenerated = 0;
  let panelsCached = 0;
  const sidecar: Record<string, PageSidecar> = {};

  for (const pg of pages) {
    const pageNum = pg["gh:pageNumber"] ?? 0;
    const panels = pg.panels ?? [];
    if (panels.length === 0) continue;
    const panelPaths: string[] = [];
    const panelIds: string[] = [];

    for (const panel of panels) {
      const prompt = buildPanelPrompt(panel);
      const promptHash = await sha256Hex(prompt);
      const cachedPath = `${t.cacheDir}/${promptHash}.jpg`;

      if (DRY_RUN) {
        panelPaths.push(cachedPath);
        panelIds.push(panel["gh:panelId"] ?? "-");
        console.log(`    [dry] panel ${panel["gh:panelId"]} prompt.sha=${promptHash.slice(0, 8)} (${prompt.slice(0, 80)}…)`);
        continue;
      }

      try {
        const r = await generatePanelImage(prompt, cachedPath);
        const tag = r.sanitized ? " (sanitized)" : "";
        if (r.hit) { panelsCached++; console.log(`    ✓cache panel ${panel["gh:panelId"]}  ${r.bytes} bytes`); }
        else       { panelsGenerated++; console.log(`    ✓gen${tag}   panel ${panel["gh:panelId"]}  ${r.bytes} bytes  ${(r.elapsedMs / 1000).toFixed(1)}s`); }
        panelPaths.push(cachedPath);
        panelIds.push(panel["gh:panelId"] ?? "-");
      } catch (e) {
        console.error(`    ✗ panel ${panel["gh:panelId"]} failed: ${(e as Error).message}`);
      }
    }

    if (panelPaths.length === 0) continue;

    const pageFile = `pages/page-${String(pageNum).padStart(2, "0")}.jpg`;
    const pageOutPath = `${t.chapterPath}/${pageFile}`;

    if (DRY_RUN) {
      sidecar[`p${String(pageNum).padStart(2, "0")}`] = { file: pageFile, sha256: "dry", width: TARGET_PAGE_WIDTH, height: 0, panelCount: panels.length, panelIds, bytes: 0 };
      continue;
    }
    if (fileExists(pageOutPath) && !OVERWRITE) {
      const info = Deno.statSync(pageOutPath);
      const bytes = await Deno.readFile(pageOutPath);
      const hash = await sha256Hex(bytes);
      const idProc = new Deno.Command("identify", { args: ["-format", "%w %h", pageOutPath], stdout: "piped", stderr: "null" });
      const { stdout } = await idProc.output();
      const [wStr, hStr] = new TextDecoder().decode(stdout).trim().split(" ");
      sidecar[`p${String(pageNum).padStart(2, "0")}`] = {
        file: pageFile, sha256: hash,
        width: parseInt(wStr, 10) || TARGET_PAGE_WIDTH,
        height: parseInt(hStr, 10) || 0,
        panelCount: panels.length, panelIds, bytes: info.size,
      };
      console.log(`  ·cache page ${pageNum}  ${info.size} bytes  sha=${hash.slice(0, 8)}`);
      continue;
    }

    const { width, height } = await composePage(panelPaths, pageOutPath);
    const bytes = await Deno.readFile(pageOutPath);
    const hash = await sha256Hex(bytes);
    sidecar[`p${String(pageNum).padStart(2, "0")}`] = { file: pageFile, sha256: hash, width, height, panelCount: panels.length, panelIds, bytes: bytes.byteLength };
    console.log(`  ✓page ${pageNum}  ${bytes.byteLength} bytes  ${width}x${height}  sha=${hash.slice(0, 8)}`);
  }

  if (!DRY_RUN) {
    await Deno.writeTextFile(t.sidecarPath, JSON.stringify(sidecar, null, 2) + "\n");
  }
  return { pages: pages.length, panelsGenerated, panelsCached, skipped: 0 };
}

// ── entry ───────────────────────────────────────────────────────────────

const targets = enumerateTargets();

console.log(`Page-image pipe`);
console.log(`  source:       ${SOURCE_DIR}`);
console.log(`  pds:          ${PDS_BASE}`);
console.log(`  targets:      ${targets.length} chapters`);
console.log(`  num-steps:    ${NUM_STEPS}`);
console.log(`  panel-cache:  ${PANEL_CACHE}`);
console.log(`  dry:          ${DRY_RUN}`);
console.log(`  overwrite:    ${OVERWRITE}`);
console.log(`  only:         ${ONLY || "(all)"}`);

let okC = 0, failC = 0;
for (const t of targets) {
  const label = `${t.volumeId}/${t.chapterDir}`;
  console.log(`\n== ${label} ==`);
  try {
    const r = await processChapter(t);
    okC++;
    console.log(`   pages=${r.pages} genPanels=${r.panelsGenerated} cachedPanels=${r.panelsCached}`);
  } catch (e) {
    failC++;
    console.error(`   ✗ ${(e as Error).message}`);
  }
}
console.log(`\n=== done: ok=${okC} fail=${failC} ===`);
console.log(`Sidecar per chapter: {chapterPath}/page-images.json`);
console.log(`Ingest:              etzhayyim_TOKEN=$(etzhayyim auth token) ./import-sip.ts --stage=pages`);
