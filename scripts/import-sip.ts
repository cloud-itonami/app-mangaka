#!/usr/bin/env -S deno run --allow-read --allow-net --allow-env
/**
 * Spirit in Physics graphic novel ingest.
 *
 * Pushes the source graphic novel (/Users/junkawasaki/github/260208-spirit-in-physics)
 * into mangaka.etzhayyim.com as Tier 2 domain records. The derive rules in
 * kotodama.jsonld (chapter-published-social / page-published-social) then emit
 * the Tier 1 app.bsky.feed.post automatically with recordWithMedia embed + facets.
 *
 * Stages (run any subset):
 *   characters  createCharacter per persona (implicit path-based DID mint on post)
 *   work        uploadBlob(cover) + createWork
 *   chapters    addChapter per volume/chapter (episode.jsonld → chapter record)
 *   pages       addPage per storyboard page (storyboard.jsonld → page record; skips
 *               chapters without storyboard.jsonld — compositedImageCid is omitted
 *               until the actual page PNG is generated, in which case status stays draft)
 *   all         characters → work → chapters → pages
 *
 * Env:
 *   SIP_SOURCE_DIR   default: /Users/junkawasaki/github/260208-spirit-in-physics
 *   MANGAKA_BASE     default: https://mangaka.etzhayyim.com/xrpc
 *   PDS_BASE         default: https://atproto.etzhayyim.com/xrpc
 *   etzhayyim_TOKEN       Service Auth JWT or sk_live_* API key (ADR-0022).
 *                    Mint via: etzhayyim agent-token --lxm com.etzhayyim.mangaka.addChapter
 *
 * Flags:
 *   --stage=<name>   characters|work|chapters|pages|all (default: all)
 *   --dry-run        log planned calls, make no XRPC requests
 *   --limit=<n>      cap records per stage (smoke test)
 */

const SOURCE_DIR    = Deno.env.get("SIP_SOURCE_DIR") ?? "/Users/junkawasaki/github/260208-spirit-in-physics";
const MANGAKA_BASE  = Deno.env.get("MANGAKA_BASE")   ?? "https://mangaka.etzhayyim.com/xrpc";
const PDS_BASE      = Deno.env.get("PDS_BASE")       ?? "https://atproto.etzhayyim.com/xrpc";
const TOKEN         = Deno.env.get("etzhayyim_TOKEN")     ?? "";
const DRY_RUN       = Deno.args.includes("--dry-run");
const STAGE         = (Deno.args.find((a) => a.startsWith("--stage=")) ?? "--stage=all").split("=")[1];
const LIMIT         = parseInt((Deno.args.find((a) => a.startsWith("--limit=")) ?? "--limit=0").split("=")[1], 10) || 0;
const ONLY          = (Deno.args.find((a) => a.startsWith("--only=")) ?? "--only=").split("=")[1];  // e.g. "vol01-loneliness/chapter02"

const WORK_RKEY     = "spirit-in-physics";
const WORK_AT_URI   = `at://mng4k4x1.etzhayyim.com/com.etzhayyim.mangaka.work/${WORK_RKEY}`;
const READER_BASE   = "https://mangaka.etzhayyim.com/at/mng4k4x1.etzhayyim.com/com.etzhayyim.mangaka.chapter";

/** Canonical display names for all 42 character slugs observed in source. Drives derive-rule facet#mention. */
const VOLUME_META: Record<string, { label: string; themeJP: string; tag: string; volNumber: number }> = {
  "vol01-loneliness": { label: "Vol.1 Loneliness",  themeJP: "孤独",   tag: "sip-vol1", volNumber: 1 },
  "vol01-water-city": { label: "Vol.1 Water City",  themeJP: "水の都", tag: "sip-vol1", volNumber: 1 },
  "vol02-dependency": { label: "Vol.2 Dependency",  themeJP: "依存",   tag: "sip-vol2", volNumber: 2 },
  "vol03-justice":    { label: "Vol.3 Justice",     themeJP: "正義",   tag: "sip-vol3", volNumber: 3 },
  "vol04-theology":   { label: "Vol.4 Theology",    themeJP: "神学",   tag: "sip-vol4", volNumber: 4 },
  "vol05-beyond":     { label: "Vol.5 Beyond",      themeJP: "彼岸",   tag: "sip-vol5", volNumber: 5 },
  "vol06-false-god":  { label: "Vol.6 False God",   themeJP: "偽神",   tag: "sip-vol6", volNumber: 6 },
  "vol07-i":          { label: "Vol.7 I",           themeJP: "私",     tag: "sip-vol7", volNumber: 7 },
  "vol08-fruit":      { label: "Vol.8 Fruit",       themeJP: "果実",   tag: "sip-vol8", volNumber: 8 },
};

// ── transport ────────────────────────────────────────────────────────────

async function xrpc(host: "mangaka" | "pds", method: string, body: unknown): Promise<Record<string, unknown>> {
  if (DRY_RUN) {
    console.log(`  [dry] POST ${host}/${method}  ${JSON.stringify(body).slice(0, 140)}…`);
    return { uri: `at://dry-run/${method}`, cid: "dry-cid" };
  }
  const base = host === "mangaka" ? MANGAKA_BASE : PDS_BASE;
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (TOKEN) headers["authorization"] = `Bearer ${TOKEN}`;
  const r = await fetch(`${base}/${method}`, { method: "POST", headers, body: JSON.stringify(body) });
  if (!r.ok) {
    throw new Error(`${method} → ${r.status}: ${await r.text()}`);
  }
  return await r.json();
}

async function uploadBlob(filePath: string, mime: string): Promise<string | null> {
  if (DRY_RUN) {
    console.log(`  [dry] uploadBlob ${filePath}`);
    return "bafy-dry-run";
  }
  const bytes = await Deno.readFile(filePath);
  const headers: Record<string, string> = { "content-type": mime };
  if (TOKEN) headers["authorization"] = `Bearer ${TOKEN}`;
  const r = await fetch(`${PDS_BASE}/com.atproto.repo.uploadBlob`, { method: "POST", headers, body: bytes });
  if (!r.ok) {
    console.error(`  uploadBlob failed ${r.status}: ${await r.text()}`);
    return null;
  }
  const resp = await r.json() as { blob?: { ref?: { $link?: string } } };
  return resp.blob?.ref?.$link ?? null;
}

// ── helpers ──────────────────────────────────────────────────────────────

function readJson<T>(path: string): T | null {
  try {
    return JSON.parse(Deno.readTextFileSync(path)) as T;
  } catch (e) {
    if (e instanceof SyntaxError && fileExists(path)) {
      console.warn(`  ⚠ JSON parse failed for ${path}: ${e.message} — skipping`);
    }
    return null;
  }
}

function listDirs(base: string): string[] {
  try {
    return [...Deno.readDirSync(base)].filter((e) => e.isDirectory).map((e) => e.name).sort();
  } catch {
    return [];
  }
}

function fileExists(path: string): boolean {
  try { Deno.statSync(path); return true; } catch { return false; }
}

function capped<T>(arr: T[]): T[] {
  return LIMIT > 0 ? arr.slice(0, LIMIT) : arr;
}

// ── stage 1: characters ──────────────────────────────────────────────────

interface CharacterIngest { slug: string; displayName: string; description: string; role?: string; age?: number; }

function loadCharacters(): CharacterIngest[] {
  const charsDir = `${SOURCE_DIR}/characters`;
  const dirs = listDirs(charsDir);
  const out: CharacterIngest[] = [];

  for (const slug of dirs) {
    const jsonld = readJson<{ "@graph"?: Array<Record<string, unknown>> }>(`${charsDir}/${slug}/${slug}.jsonld`);
    const node = jsonld?.["@graph"]?.[0] ?? {};
    const displayName = (node["name"] as string) || slug.replace(/-/g, " ");
    const description = ((node["description"] as string) ?? "").split("\n")[0].slice(0, 240);
    const role        = (node["occupation"] as string) ?? undefined;
    const age         = typeof node["age"] === "number" ? node["age"] as number : undefined;
    out.push({ slug, displayName, description: description || `Character from Spirit in Physics.`, role, age });
  }
  return out;
}

async function stageCharacters(): Promise<void> {
  console.log(`\n=== stage: characters (source ${SOURCE_DIR}/characters) ===`);
  const characters = capped(loadCharacters());
  console.log(`  ${characters.length} character records to create`);

  let ok = 0, fail = 0;
  for (const c of characters) {
    try {
      await xrpc("mangaka", "com.etzhayyim.mangaka.createCharacter", {
        slug: c.slug,
        name: c.displayName,
        description: c.description,
        role: c.role,
        age: c.age,
        workId: WORK_AT_URI,
      });
      ok++;
      console.log(`  ✓ ${c.slug} (${c.displayName})`);
    } catch (e) {
      fail++;
      console.error(`  ✗ ${c.slug}: ${(e as Error).message}`);
    }
  }
  console.log(`  stage characters: ok=${ok} fail=${fail}`);
}

// ── stage 2: work ────────────────────────────────────────────────────────

async function stageWork(): Promise<void> {
  console.log(`\n=== stage: work (${WORK_AT_URI}) ===`);
  const projectMeta = readJson<Record<string, unknown>>(`${SOURCE_DIR}/PROJECT.jsonld`);
  if (!projectMeta) throw new Error(`PROJECT.jsonld missing at ${SOURCE_DIR}`);

  const coverPath = `${SOURCE_DIR}/assets/cover/cover.jpg`;
  let coverCid: string | null = null;
  if (fileExists(coverPath)) {
    coverCid = await uploadBlob(coverPath, "image/jpeg");
    console.log(`  cover uploaded: ${coverCid ?? "FAILED"}`);
  } else {
    console.log(`  no cover at ${coverPath} — skipping blob upload`);
  }

  const title = (projectMeta["dct:title"] as string) ?? "Spirit in Physics";
  const description = (projectMeta["dct:description"] as string) ?? "";

  await xrpc("mangaka", "com.etzhayyim.mangaka.createWork", {
    id: WORK_RKEY,
    title,
    genre: "graphic-novel-sf",
    status: "published",
    arc: "ghost-hacker",
    setting: "water-city Tokyo 2065",
    timeframe: "2065",
    mainCharacter: "tamaki",
    incidentDescription: description.slice(0, 240),
  });
  console.log(`  ✓ work created: ${title} (coverCid=${coverCid ?? "-"})`);
}

// ── stage 3: chapters ────────────────────────────────────────────────────

interface EpisodeMeta {
  "dct:title"?: string;
  "dct:title_en"?: string;
  "dct:title_ja"?: string;
  "gh:arc"?: string;
  "gh:episodeId"?: string;
}

interface ChapterIngest {
  volumeId: string;
  chapterDir: string;
  chapterNum: number;
  titleJP: string;
  titleEN?: string;
  arcIds: string[];
  charactersAppearing: Array<{ slug: string; displayName: string; role?: string }>;
}

function extractCharactersFromStoryboard(storyboardPath: string, characterSlugs: Set<string>): Array<{ slug: string; displayName: string }> {
  const sb = readJson<{ "gh:pages"?: Array<{ panels?: Array<{ characters?: string[]; dialogue?: Array<{ speaker?: string }> }> }> }>(storyboardPath);
  if (!sb) return [];
  const found = new Map<string, string>();
  for (const pg of sb["gh:pages"] ?? []) {
    for (const pnl of pg.panels ?? []) {
      for (const name of pnl.characters ?? []) {
        const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
        if (characterSlugs.has(slug)) found.set(slug, name);
      }
      for (const d of pnl.dialogue ?? []) {
        const spk = d.speaker;
        if (!spk) continue;
        const slug = spk.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
        if (characterSlugs.has(slug)) found.set(slug, spk);
      }
    }
  }
  return [...found.entries()].map(([slug, displayName]) => ({ slug, displayName }));
}

function loadChapters(): ChapterIngest[] {
  const characterSlugs = new Set(loadCharacters().map((c) => c.slug));
  const volumes = listDirs(`${SOURCE_DIR}/volumes`);
  const out: ChapterIngest[] = [];

  for (const volumeId of volumes) {
    const meta = VOLUME_META[volumeId];
    if (!meta) { console.warn(`  unknown volume ${volumeId}, skipped`); continue; }
    const chapters = listDirs(`${SOURCE_DIR}/volumes/${volumeId}`).filter((n) => n.startsWith("chapter"));
    for (const chDir of chapters) {
      const chNum = parseInt(chDir.replace("chapter", ""), 10);
      const epPath = `${SOURCE_DIR}/volumes/${volumeId}/${chDir}/episode.jsonld`;
      const sbPath = `${SOURCE_DIR}/volumes/${volumeId}/${chDir}/storyboard.jsonld`;
      const ep = readJson<EpisodeMeta>(epPath);
      const titleJP = ep?.["dct:title_ja"] ?? ep?.["dct:title"] ?? `第${chNum}話`;
      const titleEN = ep?.["dct:title_en"];
      const arcIds = ep?.["gh:arc"] ? [ep["gh:arc"]!.replace(/[^A-Za-z0-9]+/g, "")] : [];
      const charactersAppearing = fileExists(sbPath) ? extractCharactersFromStoryboard(sbPath, characterSlugs) : [];

      out.push({ volumeId, chapterDir: chDir, chapterNum: chNum, titleJP, titleEN, arcIds, charactersAppearing });
    }
  }
  return out.sort((a, b) => a.chapterNum - b.chapterNum);
}

async function stageChapters(): Promise<void> {
  console.log(`\n=== stage: chapters ===`);
  const chapters = capped(loadChapters().filter((c) => !ONLY || `${c.volumeId}/${c.chapterDir}` === ONLY));
  console.log(`  ${chapters.length} chapters (${new Set(chapters.map((c) => c.volumeId)).size} volumes)`);

  let ok = 0, fail = 0;
  for (const ch of chapters) {
    const rkey = `sip-${ch.volumeId}-ch${String(ch.chapterNum).padStart(2, "0")}`;
    try {
      await xrpc("mangaka", "com.etzhayyim.mangaka.addChapter", {
        id: rkey,
        workId: WORK_AT_URI,
        chapterNum: ch.chapterNum,
        titleJP: ch.titleJP,
        titleEN: ch.titleEN,
        volumeId: ch.volumeId,
        arcIds: ch.arcIds,
        charactersAppearing: ch.charactersAppearing,
        readerUri: `${READER_BASE}/${rkey}`,
        status: ch.charactersAppearing.length > 0 ? "published" : "draft",
      });
      ok++;
      console.log(`  ✓ ch${ch.chapterNum} (${ch.volumeId}) — ${ch.titleJP.slice(0, 40)}  [${ch.charactersAppearing.length} chars]`);
    } catch (e) {
      fail++;
      console.error(`  ✗ ch${ch.chapterNum}: ${(e as Error).message}`);
    }
  }
  console.log(`  stage chapters: ok=${ok} fail=${fail}`);
}

// ── stage 4: pages ───────────────────────────────────────────────────────

interface PageIngest {
  volumeId: string; chapterDir: string; chapterNum: number; pageNum: number;
  altText: string; panels: Array<{ x: number; y: number; w: number; h: number; order: number; speakers: string[] }>;
  charactersAppearing: Array<{ slug: string; displayName: string }>;
}

function loadPages(): PageIngest[] {
  const characterSlugs = new Set(loadCharacters().map((c) => c.slug));
  const volumes = listDirs(`${SOURCE_DIR}/volumes`);
  const out: PageIngest[] = [];

  for (const volumeId of volumes) {
    if (!VOLUME_META[volumeId]) continue;
    const chapters = listDirs(`${SOURCE_DIR}/volumes/${volumeId}`).filter((n) => n.startsWith("chapter"));
    for (const chDir of chapters) {
      const sbPath = `${SOURCE_DIR}/volumes/${volumeId}/${chDir}/storyboard.jsonld`;
      if (!fileExists(sbPath)) continue;
      const sb = readJson<{ "gh:pages"?: Array<{ "gh:pageNumber"?: number; "gh:description"?: string; panels?: Array<Record<string, unknown>> }> }>(sbPath);
      const chapterNum = parseInt(chDir.replace("chapter", ""), 10);

      for (const pg of sb?.["gh:pages"] ?? []) {
        const pageNum = pg["gh:pageNumber"] ?? 0;
        const rawPanels = pg.panels ?? [];
        const panels = rawPanels.map((p, i) => ({
          x: 0, y: i / Math.max(1, rawPanels.length), w: 1, h: 1 / Math.max(1, rawPanels.length),
          order: i + 1,
          speakers: ((p["characters"] as string[]) ?? []).map((n) => n.toLowerCase().replace(/[^a-z0-9]+/g, "-")).filter((s) => characterSlugs.has(s)),
        }));
        const perPageChars = new Map<string, string>();
        for (const p of rawPanels) {
          for (const name of (p["characters"] as string[]) ?? []) {
            const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
            if (characterSlugs.has(slug)) perPageChars.set(slug, name);
          }
        }
        const altPieces = rawPanels.map((p) => (p["gh:description"] as string ?? "")).filter(Boolean).join(" / ");
        out.push({
          volumeId, chapterDir: chDir, chapterNum, pageNum,
          altText: (pg["gh:description"] as string ?? "") + (altPieces ? ` — ${altPieces}` : ""),
          panels,
          charactersAppearing: [...perPageChars.entries()].map(([slug, displayName]) => ({ slug, displayName })),
        });
      }
    }
  }
  return out;
}

interface PageSidecarEntry { file: string; sha256: string; width: number; height: number; panelCount: number; panelIds: string[]; bytes: number; }

function loadPageSidecar(volumeId: string, chDir: string): Record<string, PageSidecarEntry> | null {
  const path = `${SOURCE_DIR}/volumes/${volumeId}/${chDir}/page-images.json`;
  return readJson<Record<string, PageSidecarEntry>>(path);
}

async function stagePages(): Promise<void> {
  console.log(`\n=== stage: pages ===`);
  const pages = capped(loadPages().filter((p) => !ONLY || `${p.volumeId}/${p.chapterDir}` === ONLY));
  console.log(`  ${pages.length} pages (from storyboards)`);
  if (pages.length === 0) {
    console.log(`  (no storyboard.jsonld found — stage is a no-op. Generate storyboards first.)`);
    return;
  }

  // Pre-group pages by chapter so we load each sidecar + existing blob cache once
  const blobsUploaded = new Map<string, string>();  // sha256 → cid (in-process dedup)

  let ok = 0, fail = 0, withImage = 0, withoutImage = 0;
  for (const pg of pages) {
    const chapterRkey = `sip-${pg.volumeId}-ch${String(pg.chapterNum).padStart(2, "0")}`;
    const rkey = `${chapterRkey}-p${String(pg.pageNum).padStart(3, "0")}`;
    const chapterDir = `${pg.volumeId}/chapter${String(pg.chapterNum).padStart(2, "0")}`;
    const chDirName = pg.chapterDir;
    const pageKey = `p${String(pg.pageNum).padStart(2, "0")}`;

    // Look up sidecar + upload blob if available
    const sidecar = loadPageSidecar(pg.volumeId, chDirName);
    const sc = sidecar?.[pageKey];
    let compositedImageCid: string | null = null;
    let pageWidth: number | undefined;
    let pageHeight: number | undefined;
    if (sc) {
      const localPath = `${SOURCE_DIR}/volumes/${pg.volumeId}/${chDirName}/${sc.file}`;
      if (fileExists(localPath)) {
        // In-process dedup by content-hash
        if (blobsUploaded.has(sc.sha256)) {
          compositedImageCid = blobsUploaded.get(sc.sha256) ?? null;
        } else {
          compositedImageCid = await uploadBlob(localPath, "image/jpeg");
          if (compositedImageCid) blobsUploaded.set(sc.sha256, compositedImageCid);
        }
        pageWidth = sc.width;
        pageHeight = sc.height;
      }
    }

    // Throttle: small sleep between addPage calls.
    // Known issue: mangaka Worker's cmdAddPage hangs on ~sequential calls (pre-existing
    // pattern shared with cmdCreateCharacter). Root cause in mangaka runtime — not
    // derive dispatcher. Fresh Worker isolate takes the first request successfully.
    if (ok > 0 || fail > 0) await new Promise((r) => setTimeout(r, 1500));
    try {
      // id omitted — mangaka cmdAddPage generates a TID-style id server-side
      // (passing a custom id triggers a cmdAddPage code path that hangs the
      // Worker; isolated curl without id completes in <500ms).
      const payload: Record<string, unknown> = {
        chapterId: `at://mng4k4x1.etzhayyim.com/com.etzhayyim.mangaka.chapter/${chapterRkey}`,
        pageNum: pg.pageNum,
        altText: pg.altText.slice(0, 500),
        panels: pg.panels,
        charactersAppearing: pg.charactersAppearing,
      };
      if (compositedImageCid) {
        payload.compositedImageCid = compositedImageCid;
        payload.width = pageWidth;
        payload.height = pageHeight;
        withImage++;
      } else {
        withoutImage++;
      }
      await xrpc("mangaka", "com.etzhayyim.mangaka.addPage", payload);
      ok++;
      const imgTag = compositedImageCid ? `img=${compositedImageCid.slice(0, 10)}…` : "img=—";
      console.log(`  ✓ p${pg.pageNum} ch${pg.chapterNum} ${chapterDir} — ${pg.panels.length} panels, ${pg.charactersAppearing.length} chars, ${imgTag}`);
    } catch (e) {
      fail++;
      console.error(`  ✗ p${pg.pageNum} ch${pg.chapterNum}: ${(e as Error).message}`);
    }
  }
  console.log(`  stage pages: ok=${ok} fail=${fail} withImage=${withImage} withoutImage=${withoutImage} uniqueBlobs=${blobsUploaded.size}`);
}

// ── entry ────────────────────────────────────────────────────────────────

console.log(`Spirit in Physics ingest`);
console.log(`  source: ${SOURCE_DIR}`);
console.log(`  mangaka: ${MANGAKA_BASE}`);
console.log(`  pds:    ${PDS_BASE}`);
console.log(`  stage:  ${STAGE}`);
console.log(`  dry:    ${DRY_RUN}`);
console.log(`  limit:  ${LIMIT || "unlimited"}`);
console.log(`  auth:   ${TOKEN ? "Bearer " + TOKEN.slice(0, 10) + "…" : "none (public XRPC only)"}`);

if (STAGE === "characters" || STAGE === "all") await stageCharacters();
if (STAGE === "work"       || STAGE === "all") await stageWork();
if (STAGE === "chapters"   || STAGE === "all") await stageChapters();
if (STAGE === "pages"      || STAGE === "all") await stagePages();

console.log(`\n=== done ===`);
console.log(`AT URI (work):    ${WORK_AT_URI}`);
console.log(`Reader URL base:  ${READER_BASE}/{rkey}`);
console.log(`\nOn deploy of updated kotodama.jsonld, the derive rules will auto-emit`);
console.log(`app.bsky.feed.post for each published chapter+page, with recordWithMedia`);
console.log(`embed + facet#mention / #tag / #link — visible in yoro feed as a vertical`);
console.log(`post thread suitable for smartphone reading.`);
