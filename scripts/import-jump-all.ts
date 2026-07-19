#!/usr/bin/env -S deno run --allow-read --allow-net --allow-run --allow-write --allow-env
/**
 * Import ALL episodes from 260123-jump into mangaka.etzhayyim.com as Genko documents.
 * Each episode = 1 document (multi-page). Images uploaded to PDS blob.
 * AT URI: mangaka.etzhayyim.com/at/mng4k4x1.etzhayyim.com/com.etzhayyim.mangaka.document/{docId}
 */

const JUMP_DIR = "/Users/junkawasaki/github/ghosthacker/260123-jump/resources";
const IMG_DIR = "/Users/junkawasaki/github/ghosthacker/260123-jump/resources/images";
const PDS_BASE = "https://atproto.etzhayyim.com/xrpc/";
const MANGAKA_BASE = Deno.env.get("MANGAKA_BASE") || "https://mangaka.etzhayyim.com/xrpc/";

let created = 0, errors = 0, imagesUploaded = 0;

async function xrpc(method: string, body: Record<string, unknown>) {
  const resp = await fetch(MANGAKA_BASE + method, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return await resp.json();
}

// Image blob URL cache (same file → same blob URL, skip re-upload)
const blobCache = new Map<string, string>();
// CID cache so importGhosthacker can pass the bare CID (not URL) to vertex_mangaka.cid
const cidCache = new Map<string, string>();

/** Load shared entity catalog from disk (characters/ environments/ organizations/). */
function loadEntityCatalogs(): { characters: Record<string, any>; environments: Record<string, any>; organizations: Record<string, any> } {
  const characters: Record<string, any> = {};
  const environments: Record<string, any> = {};
  const organizations: Record<string, any> = {};
  // Characters: subfolders. Some have a .jsonld file inside; fallback to name from folder.
  try {
    for (const e of Deno.readDirSync(`${JUMP_DIR}/characters`)) {
      if (!e.isDirectory) continue;
      const charId = e.name;
      let data: any = { name: charId };
      const candidate = `${JUMP_DIR}/characters/${charId}/character.jsonld`;
      try { data = { ...data, ...JSON.parse(Deno.readTextFileSync(candidate)) }; } catch { /* no metadata */ }
      characters[`character:${charId}`] = data;
    }
  } catch { /* dir missing */ }
  // Environments: .jsonld files
  try {
    for (const e of Deno.readDirSync(`${JUMP_DIR}/environments`)) {
      if (!e.isFile || !e.name.endsWith(".jsonld")) continue;
      const envId = e.name.replace(/\.jsonld$/, "");
      let data: any = { name: envId };
      try { data = { ...data, ...JSON.parse(Deno.readTextFileSync(`${JUMP_DIR}/environments/${e.name}`)) }; } catch { /* */ }
      environments[`env:${envId}`] = data;
    }
  } catch { /* */ }
  // Organizations: subfolders
  try {
    for (const e of Deno.readDirSync(`${JUMP_DIR}/organizations`)) {
      if (!e.isDirectory) continue;
      const orgId = e.name;
      let data: any = { name: orgId };
      const candidate = `${JUMP_DIR}/organizations/${orgId}/organization.jsonld`;
      try { data = { ...data, ...JSON.parse(Deno.readTextFileSync(candidate)) }; } catch { /* */ }
      organizations[`org:${orgId}`] = data;
    }
  } catch { /* */ }
  return { characters, environments, organizations };
}

async function uploadBlob(filePath: string): Promise<string> {
  if (blobCache.has(filePath)) return blobCache.get(filePath)!;
  try {
    const data = await Deno.readFile(filePath);
    const resp = await fetch(PDS_BASE + "com.atproto.repo.uploadBlob", {
      method: "POST",
      headers: { "content-type": "image/jpeg" },
      body: data,
    });
    const result = await resp.json();
    if (result.blob?.ref?.$link) {
      const cid = result.blob.ref.$link.replace(/^blobs\//, "");
      // sync.getBlob requires both did + cid. Anonymous upload owner = "anonymous".
      const url = `${PDS_BASE}com.atproto.sync.getBlob?did=anonymous&cid=${encodeURIComponent(cid)}`;
      blobCache.set(filePath, url);
      cidCache.set(filePath, cid);
      imagesUploaded++;
      return url;
    }
  } catch (e) { console.warn(`    upload error: ${e}`); }
  return "";
}

function resolveImagePath(relPath: string): string {
  return IMG_DIR + relPath.replace("/images", "");
}

// Genko mm-unit coordinate system (kami-engine-sdk genko-embed.ts patch, 2026-05-13).
// Nodes set _unit:'mm' and the renderer multiplies by current sc at draw time, so
// coords stay paper-relative regardless of canvas size. Values match YOUSHI.b4manga
// in genko-embed.ts (B4 = 257×364mm; inner safe frame = 53.5-203.5mm × 72-292mm).
const INNER_L = 53.5;    // mm — inner safe frame left
const INNER_T = 72;      // mm — inner safe frame top
const INNER_R = 203.5;   // mm — inner safe frame right
const INNER_B = 292;     // mm — inner safe frame bottom
const INNER_W = INNER_R - INNER_L;   // 150 mm
const INNER_H = INNER_B - INNER_T;   // 220 mm

let nidC = 1;
function nid() { return "n" + (nidC++); }
function pid() { return "p" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }

/** Generate panel layout for N panels in mm, inside the genkouyoushi inner safe frame. */
function layoutPanels(count: number): Array<{ x1: number; y1: number; x2: number; y2: number }> {
  const gap = 2;  // mm
  if (count === 1) return [{ x1: INNER_L, y1: INNER_T, x2: INNER_R, y2: INNER_B }];
  if (count === 2) return [
    { x1: INNER_L, y1: INNER_T, x2: INNER_R, y2: INNER_T + INNER_H * 0.48 },
    { x1: INNER_L, y1: INNER_T + INNER_H * 0.52, x2: INNER_R, y2: INNER_B },
  ];
  if (count === 3) return [
    { x1: INNER_L, y1: INNER_T, x2: INNER_R, y2: INNER_T + INNER_H * 0.33 },
    { x1: INNER_L, y1: INNER_T + INNER_H * 0.33 + gap, x2: INNER_R, y2: INNER_T + INNER_H * 0.66 },
    { x1: INNER_L, y1: INNER_T + INNER_H * 0.66 + gap, x2: INNER_R, y2: INNER_B },
  ];
  if (count === 4) return [
    { x1: INNER_L, y1: INNER_T, x2: INNER_R, y2: INNER_T + INNER_H * 0.28 },
    { x1: INNER_L, y1: INNER_T + INNER_H * 0.28 + gap, x2: INNER_R, y2: INNER_T + INNER_H * 0.62 },
    { x1: INNER_L, y1: INNER_T + INNER_H * 0.62 + gap, x2: INNER_L + INNER_W * 0.48, y2: INNER_B },
    { x1: INNER_L + INNER_W * 0.52, y1: INNER_T + INNER_H * 0.62 + gap, x2: INNER_R, y2: INNER_B },
  ];
  if (count === 5) return [
    { x1: INNER_L, y1: INNER_T, x2: INNER_L + INNER_W * 0.48, y2: INNER_T + INNER_H * 0.33 },
    { x1: INNER_L + INNER_W * 0.52, y1: INNER_T, x2: INNER_R, y2: INNER_T + INNER_H * 0.33 },
    { x1: INNER_L, y1: INNER_T + INNER_H * 0.33 + gap, x2: INNER_R, y2: INNER_T + INNER_H * 0.66 },
    { x1: INNER_L, y1: INNER_T + INNER_H * 0.66 + gap, x2: INNER_L + INNER_W * 0.48, y2: INNER_B },
    { x1: INNER_L + INNER_W * 0.52, y1: INNER_T + INNER_H * 0.66 + gap, x2: INNER_R, y2: INNER_B },
  ];
  // 6+ panels: grid layout inside the inner safe frame
  const cols = count <= 6 ? 2 : 3;
  const rows = Math.ceil(count / cols);
  const cellW = (INNER_W - (cols - 1) * gap) / cols;
  const cellH = (INNER_H - (rows - 1) * gap) / rows;
  const out: Array<{ x1: number; y1: number; x2: number; y2: number }> = [];
  for (let i = 0; i < count; i++) {
    const col = i % cols, row = Math.floor(i / cols);
    out.push({
      x1: INNER_L + col * (cellW + gap),
      y1: INNER_T + row * (cellH + gap),
      x2: INNER_L + col * (cellW + gap) + cellW,
      y2: INNER_T + row * (cellH + gap) + cellH,
    });
  }
  return out;
}

/** Build Genko document for one episode. */
async function buildEpisodeDoc(epSlug: string): Promise<{ doc: any; docId: string } | null> {
  const epPath = `${JUMP_DIR}/episodes/${epSlug}/episode.jsonld`;
  try { Deno.statSync(epPath); } catch { return null; }
  const ep = JSON.parse(Deno.readTextFileSync(epPath));
  const title = ep["dct:title"] || epSlug;
  const pages = ep["gh:pages"] || [];
  if (pages.length === 0) return null;

  const docId = `doc-gh-${epSlug}`;
  const docPages: any[] = [];

  // Split source pages with >4 panels into multiple genko pages of max 4 panels
  // each (manga reading is denser, but our fixed paper size doesn't fit dialogue
  // for crammed pages). Page numbering: append /2, /3 suffix to title for splits.
  const MAX_PANELS_PER_PAGE = 4;
  const splitPages: Array<{ panels: any[]; pageNum: number; pageTitle: string }> = [];
  for (const pg of pages) {
    const panels = pg["gh:panels"] || [];
    const pageNum = pg["gh:pageNumber"] ?? 0;
    const pageTitle = pg["gh:pageTitle"] || `Page ${pageNum}`;
    if (panels.length <= MAX_PANELS_PER_PAGE) {
      splitPages.push({ panels, pageNum, pageTitle });
    } else {
      const chunkSize = MAX_PANELS_PER_PAGE;
      const chunks = Math.ceil(panels.length / chunkSize);
      for (let c = 0; c < chunks; c++) {
        splitPages.push({
          panels: panels.slice(c * chunkSize, (c + 1) * chunkSize),
          pageNum: pageNum + c * 0.1, // pseudo-numbering, kept for sort
          pageTitle: chunks > 1 ? `${pageTitle} (${c + 1}/${chunks})` : pageTitle,
        });
      }
    }
  }

  for (const pg of splitPages) {
    const panels = pg.panels || [];
    const pageNum = pg.pageNum;
    const pageTitle = pg.pageTitle;
    const panelCount = panels.length;
    if (panelCount === 0) { docPages.push({ id: pid(), name: pageTitle, youshi: { id: nid(), type: "b4manga", visible: true }, nodes: [] }); continue; }

    const layouts = layoutPanels(panelCount);
    const nodes: any[] = [];

    for (let i = 0; i < panels.length; i++) {
      const pnl = panels[i];
      const layout = layouts[Math.min(i, layouts.length - 1)];
      const panelNid = nid();

      // Panel node — mm-unit coords (Genko canonical shape with _unit:'mm').
      // loadPage at genko-embed.ts:318 unwraps `n.data` into overlays; renderer
      // multiplies x1/y1/x2/y2 by current sc at draw time when _unit==='mm'.
      nodes.push({ id: panelNid, type: "panel", visible: true, data: {
        type: "panel", _nid: panelNid, _visible: true, _unit: "mm",
        panelName: String(i + 1),
        x1: layout.x1, y1: layout.y1, x2: layout.x2, y2: layout.y2,
      }});

      // AI image (upload if exists)
      const imgUrl = pnl["generatedImageUrl"] || pnl["gh:generatedImageUrl"] || "";
      if (imgUrl) {
        const imgPath = resolveImagePath(imgUrl);
        try {
          Deno.statSync(imgPath);
          const blobUrl = await uploadBlob(imgPath);
          if (blobUrl) {
            const imgNid = nid();
            nodes.push({ id: imgNid, type: "ai-image", visible: true, data: {
              type: "ai-image", _nid: imgNid, _visible: true, _unit: "mm",
              _parent: panelNid,
              _agent: "genga", _genImageUrl: blobUrl,
              _genPrompt: pnl["gh:imagePrompt"] || "",
              x1: layout.x1, y1: layout.y1, x2: layout.x2, y2: layout.y2,
              createdAt: new Date().toISOString(),
            }});
          }
        } catch { /* image file doesn't exist, skip */ }
      }

      // Dialogue → fukidashi / SFX / narration nodes, parented to panel.
      // Limit to 3 dialogues per panel to avoid overflow. Auto-size bubble based on
      // text length so it fits the panel without overflowing.
      const dialogues: any[] = (pnl["dialogue"] || []).slice(0, 3);
      const panelW = layout.x2 - layout.x1;
      const panelH = layout.y2 - layout.y1;
      const slotCount = dialogues.length || 1;
      const slotH = Math.min((panelH - 4) / slotCount, 18);
      const bubbleH = slotH - 1;
      for (let di = 0; di < dialogues.length; di++) {
        const dlg = dialogues[di];
        const speaker = (dlg["speaker"] || "").trim();
        const text = (dlg["text"] || "").trim();
        if (!text) continue;
        const nidS = nid();
        // Bubble width based on text length: assume ~3 chars/mm at fontSize~3mm,
        // with a min/max clamp. Stay within 70% of panel width.
        const textLen = text.length + (speaker && speaker !== "Narration" && speaker !== "SE" ? speaker.length + 2 : 0);
        const desiredW = Math.min(textLen * 2.5 + 6, panelW * 0.75);
        const bubbleW = Math.max(20, Math.min(desiredW, panelW - 4));

        if (speaker === "SE") {
          // SFX: stylized text inside the panel, centered horizontally with offset.
          // Position safely within panel bounds.
          const sfxX = layout.x1 + Math.max(2, (panelW - textLen * 4) / 2);
          const sfxY = layout.y1 + 6 + di * 9;
          nodes.push({ id: nidS, type: "text", visible: true, data: {
            type: "text", _nid: nidS, _visible: true, _unit: "mm", _parent: panelNid,
            x: sfxX, y: sfxY,
            text, fontSize: 6, fontFamily: "sfx", fontStyle: "bold", color: "#000000",
            isSfx: true,
          }});
        } else if (speaker === "Narration") {
          // Narration: plain text box clamped to panel area.
          nodes.push({ id: nidS, type: "text", visible: true, data: {
            type: "text", _nid: nidS, _visible: true, _unit: "mm", _parent: panelNid,
            x: layout.x1 + 2, y: layout.y1 + 3 + di * 5,
            text: text.slice(0, 48), fontSize: 2.8, fontFamily: "gothic", color: "#000000",
          }});
        } else {
          // Speech / thought / shout bubble (panel-clamped).
          const isShout = /[!！]{2,}|[!！]+$/.test(text);
          const isThought = /[…]/.test(text) && !isShout;
          const shape = isShout ? "shout" : (isThought ? "thought" : "normal");
          // Position bubble in the upper-right of its slot, clamped to panel.
          const bx1 = Math.max(layout.x1 + 2, layout.x2 - bubbleW - 2);
          const by1 = layout.y1 + 2 + di * slotH;
          const bx2 = Math.min(layout.x2 - 1, bx1 + bubbleW);
          const by2 = Math.min(layout.y2 - 1, by1 + bubbleH);
          // Strip speaker prefix from text — speaker shown as small label above bubble.
          nodes.push({ id: nidS, type: "fukidashi", visible: true, data: {
            type: "fukidashi", _nid: nidS, _visible: true, _unit: "mm", _parent: panelNid,
            shape,
            x1: bx1, y1: by1, x2: bx2, y2: by2,
            text, speaker,
          }});
        }
      }
    }

    docPages.push({ id: pid(), name: pageTitle, youshi: { id: nid(), type: "b4manga", visible: true }, nodes });
  }

  return { doc: { name: title, docId, convoId: "", pages: docPages, activePageIdx: 0 }, docId };
}

/** Build normalized importGhosthacker payload for one episode (uses CID cache populated by saveDocument's uploadBlob calls). */
function buildNormalizedPayload(epSlug: string, catalogs: { characters: Record<string, any>; environments: Record<string, any>; organizations: Record<string, any> }): Record<string, unknown> | null {
  const epPath = `${JUMP_DIR}/episodes/${epSlug}/episode.jsonld`;
  let ep: any;
  try { ep = JSON.parse(Deno.readTextFileSync(epPath)); } catch { return null; }
  const pages = (ep["gh:pages"] || []).map((pg: any) => {
    const pageNumber = pg["gh:pageNumber"] ?? 0;
    const panels = (pg["gh:panels"] || []).map((pnl: any) => {
      const imgUrl: string = pnl["generatedImageUrl"] || pnl["gh:generatedImageUrl"] || "";
      const imgPath = imgUrl ? resolveImagePath(imgUrl) : "";
      const cid = imgPath && cidCache.get(imgPath) || "";
      return {
        panelNumber: pnl["panel"] ?? 0,
        shot: pnl["shot"] || "",
        visual: pnl["visual"] || "",
        characters: pnl["characters"] || [],
        environment: pnl["environment"] || "",
        sdxlPrompt: pnl["gh:sdxlPrompt"] || pnl["gh:imagePrompt"] || "",
        dialogue: pnl["dialogue"] || [],
        generatedImageCid: cid,
      };
    });
    return { pageNumber, pageName: pg["gh:pageTitle"] || `Page ${pageNumber}`, panels };
  });
  return {
    workId: "ghost-hacker",
    workName: "Ghost Hacker",
    episodeId: ep["gh:episodeId"] || `episode:${epSlug}`,
    chapterTitle: ep["dct:title"] || epSlug,
    arc: ep["gh:arc"] || "",
    episodeIndex: ep["gh:episodeIndex"] ?? 0,
    mainCharacter: ep["gh:mainCharacter"] || "",
    tagline: ep["gh:presentationTagline"] || "",
    sinContrast: ep["gh:sinContrast"] || "",
    industry: ep["gh:industry"] || "",
    pages,
    characters: catalogs.characters,
    environments: catalogs.environments,
    organizations: catalogs.organizations,
  };
}

// === Main === (wrapped to satisfy parent package.json `"type": "commonjs"` under Deno 2.x)
async function main() {
  console.log("=== Ghost Hacker: 260123-jump — Full Import ===\n");

  // Load shared entity catalog once
  const catalogs = loadEntityCatalogs();
  console.log(`Catalogs: ${Object.keys(catalogs.characters).length} characters, ${Object.keys(catalogs.environments).length} environments, ${Object.keys(catalogs.organizations).length} organizations\n`);

  // Get/create project
  let convoId = "";
  try {
    const projects = await xrpc("com.etzhayyim.mangaka.listProjects", { limit: 10 });
    const gh = (projects.items || []).find((p: any) => (p.name || "").includes("Ghost Hacker"));
    if (gh) convoId = gh.convoId || "";
  } catch (error) {
    console.warn("[silent-fail] import-jump-all.ts: listProjects failed", error);
  }
  if (!convoId) {
    const np = await xrpc("com.etzhayyim.mangaka.createProject", { name: "Ghost Hacker: 260123-jump", description: "Cybersecurity manga series", projectType: "manga-series" });
    convoId = np.convoId || "";
  }
  console.log(`Project convoId: ${convoId}\n`);

  // Process all episodes
  const episodes = [...Deno.readDirSync(`${JUMP_DIR}/episodes`)].filter(e => e.isDirectory).map(e => e.name).sort();
  const results: { slug: string; title: string; docId: string; pages: number; images: number; url: string }[] = [];
  let normalizedOK = 0, normalizedErr = 0;

  for (const epSlug of episodes) {
    const imgsBefore = imagesUploaded;
    console.log(`--- ${epSlug} ---`);
    const result = await buildEpisodeDoc(epSlug);
    if (!result) { console.log("  (no pages, skipped)"); continue; }

    const { doc, docId } = result;
    doc.convoId = convoId;
    const docJson = JSON.stringify(doc);
    const pageCount = doc.pages.length;
    const imgsThisEp = imagesUploaded - imgsBefore;

    console.log(`  ${doc.name}: ${pageCount} pages, ${imgsThisEp} images, ${(docJson.length / 1024).toFixed(0)}KB`);

    // 1) Save document (for SPA loadDocument compat)
    const r = await xrpc("com.etzhayyim.mangaka.saveDocument", { docId, name: doc.name, document: docJson, convoId });
    if (r.status === "saved") {
      created++;
      const url = `https://mangaka.etzhayyim.com/at/mng4k4x1.etzhayyim.com/com.etzhayyim.mangaka.document/${docId}`;
      results.push({ slug: epSlug, title: doc.name, docId, pages: pageCount, images: imgsThisEp, url });
      console.log(`  → ${url}`);
    } else {
      errors++;
      console.error(`  ERROR saveDocument: ${JSON.stringify(r)}`);
    }

    // 2) Normalize into vertex_mangaka (work/chapter/page/panel/character/environment/organization/generatedImage)
    const payload = buildNormalizedPayload(epSlug, catalogs);
    if (payload) {
      const r2 = await xrpc("com.etzhayyim.mangaka.importGhosthacker", payload);
      if (r2.status === "imported") {
        normalizedOK++;
        const counts = r2.insertedCounts || {};
        console.log(`  norm: pages=${counts.page||0} panels=${counts.panel||0} chars=${counts.character||0} envs=${counts.environment||0} orgs=${counts.organization||0} imgs=${counts.generatedImage||0}`);
      } else {
        normalizedErr++;
        console.error(`  ERROR importGhosthacker: ${JSON.stringify(r2)}`);
      }
    }
  }

  console.log(`\n=== Done ===`);
  console.log(`Episodes (document): ${created} saved, ${errors} errors`);
  console.log(`Episodes (normalized): ${normalizedOK} ok, ${normalizedErr} errors`);
  console.log(`Images uploaded: ${imagesUploaded} (${blobCache.size} unique blobs)`);
  console.log(`\n=== AT URI Index ===`);
  for (const r of results) {
    console.log(`${r.title}`);
    console.log(`  ${r.pages} pages, ${r.images} images`);
    console.log(`  ${r.url}`);
  }
}

main().catch((err) => { console.error(err); Deno.exit(1); });
