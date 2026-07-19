#!/usr/bin/env -S deno run --allow-read --allow-net --allow-run --allow-write --allow-env
/**
 * Import 1 episode page (arc0-1-origin, page 0) into mangaka.etzhayyim.com as a Genko document.
 * Uploads images to PDS blob storage, references by URL in the document.
 */

const JUMP_DIR = "/Users/junkawasaki/github/ghosthacker/260123-jump/resources";
const IMG_DIR = "/Users/junkawasaki/github/ghosthacker/260123-jump/resources/images";
const PDS_BASE = "https://atproto.etzhayyim.com/xrpc/";
const MANGAKA_BASE = "https://mangaka.etzhayyim.com/xrpc/";

async function xrpc(method: string, body: Record<string, unknown>) {
  const resp = await fetch(MANGAKA_BASE + method, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return await resp.json();
}

/** Upload image to PDS blob storage. Returns blob URL for getBlob. */
async function uploadBlob(filePath: string): Promise<string> {
  const data = await Deno.readFile(filePath);
  console.log(`  Uploading ${filePath.split("/").pop()} (${(data.length / 1024).toFixed(0)}KB)...`);

  const resp = await fetch(PDS_BASE + "com.atproto.repo.uploadBlob", {
    method: "POST",
    headers: { "content-type": "image/jpeg" },
    body: data,
  });
  const result = await resp.json();
  if (result.blob?.ref?.$link) {
    const blobKey = result.blob.ref.$link; // e.g. "blobs/anonymous/sha256hex"
    // Strip "blobs/" prefix for getBlob cid parameter
    const cid = blobKey.replace(/^blobs\//, "");
    const url = `${PDS_BASE}com.atproto.sync.getBlob?cid=${encodeURIComponent(cid)}`;
    console.log(`    → uploaded: ${url.slice(0, 80)}...`);
    return url;
  }
  console.warn(`    → upload failed:`, result);
  return "";
}

/** Resolve image path from JSON-LD relative path. */
function resolveImagePath(relPath: string): string {
  return IMG_DIR + relPath.replace("/images", "");
}

// --- Load episode data ---
const ep = JSON.parse(Deno.readTextFileSync(`${JUMP_DIR}/episodes/arc0-1-origin/episode.jsonld`));
const title = ep["dct:title"] || "Arc 0-1";
const pages = ep["gh:pages"] || [];

console.log(`=== Importing: ${title} ===`);
console.log(`Total pages: ${pages.length}`);

// B4 manga page dimensions
const PAGE_W = 2480;
const PAGE_H = 3508;
const MARGIN = 120;
const INNER_W = PAGE_W - MARGIN * 2;
const INNER_H = PAGE_H - MARGIN * 2;

let nidCounter = 1;
function nid() { return "n" + (nidCounter++); }
function pid() { return "p" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }

const docId = "doc-gh-arc01-" + Date.now().toString(36);
const docPages: any[] = [];

// Process page 0 (pretitle) — 4 panels with generated images
const pageIdx = 0;
const pg = pages[pageIdx];
const panels = pg["gh:panels"] || [];
const pageNum = pg["gh:pageNumber"] ?? 0;
const pageTitle = pg["gh:pageTitle"] || `Page ${pageNum}`;

console.log(`\nProcessing page ${pageNum}: ${pageTitle} (${panels.length} panels)`);

const nodes: any[] = [];

// Layout: 4-panel manga grid
const gap = 20;
const layouts = [
  { x1: MARGIN, y1: MARGIN, x2: PAGE_W - MARGIN, y2: MARGIN + INNER_H * 0.28 },
  { x1: MARGIN, y1: MARGIN + INNER_H * 0.28 + gap, x2: PAGE_W - MARGIN, y2: MARGIN + INNER_H * 0.62 },
  { x1: MARGIN, y1: MARGIN + INNER_H * 0.62 + gap, x2: MARGIN + INNER_W * 0.48, y2: PAGE_H - MARGIN },
  { x1: MARGIN + INNER_W * 0.52, y1: MARGIN + INNER_H * 0.62 + gap, x2: PAGE_W - MARGIN, y2: PAGE_H - MARGIN },
];

for (let i = 0; i < panels.length; i++) {
  const pnl = panels[i];
  const layout = layouts[i] || layouts[layouts.length - 1];
  const panelNid = nid();

  // Panel overlay
  nodes.push({
    id: panelNid,
    type: "panel",
    visible: true,
    data: {
      type: "panel", _nid: panelNid, _visible: true,
      panelName: String(i + 1),
      x1: layout.x1, y1: layout.y1, x2: layout.x2, y2: layout.y2,
    },
  });

  // Upload image and create ai-image node with URL
  const imgUrl = pnl["generatedImageUrl"] || pnl["gh:generatedImageUrl"] || "";
  if (imgUrl) {
    const imgPath = resolveImagePath(imgUrl);
    const blobUrl = await uploadBlob(imgPath);
    if (blobUrl) {
      const imgNid = nid();
      nodes.push({
        id: imgNid,
        type: "ai-image",
        visible: true,
        data: {
          type: "ai-image", _nid: imgNid, _visible: true, _parent: panelNid,
          _agent: "genga",
          _genImageUrl: blobUrl,
          _genPrompt: pnl["gh:imagePrompt"] || "",
          _artistName: "Anime Genga Artist",
          x1: layout.x1, y1: layout.y1, x2: layout.x2, y2: layout.y2,
          createdAt: new Date().toISOString(),
        },
      });
    }
  }

  // Dialogue as text nodes
  const dialogues: any[] = pnl["dialogue"] || [];
  for (let di = 0; di < dialogues.length; di++) {
    const dlg = dialogues[di];
    const speaker = dlg["speaker"] || "";
    const text = dlg["text"] || "";
    if (!text) continue;
    const textNid = nid();
    const textStr = speaker && speaker !== "Narration" && speaker !== "SE"
      ? `${speaker}「${text}」` : text;
    nodes.push({
      id: textNid,
      type: "text",
      visible: true,
      data: {
        type: "text", _nid: textNid, _visible: true, _parent: panelNid,
        text: textStr, x: layout.x1 + 20, y: layout.y1 + 30 + di * 60,
        fontSize: 24, color: "#000",
      },
    });
  }
}

docPages.push({
  id: pid(),
  name: pageTitle,
  youshi: { id: nid(), type: "b4manga", visible: true },
  nodes,
});

const doc: any = {
  name: `${title} — Page ${pageNum}`,
  docId,
  convoId: "",
  pages: docPages,
  activePageIdx: 0,
};

console.log(`\nDocument: ${doc.name}`);
console.log(`  Pages: ${doc.pages.length}, Nodes: ${doc.pages[0].nodes.length}`);
const docJson = JSON.stringify(doc);
console.log(`  Size: ${(docJson.length / 1024).toFixed(0)}KB`);

// Find project
console.log("\n=== Finding project ===");
const projects = await xrpc("com.etzhayyim.mangaka.listProjects", { limit: 10 });
let convoId = "";
if (projects.items) {
  const ghProject = projects.items.find((p: any) =>
    (p.name || p.display_name || "").includes("Ghost Hacker")
  );
  if (ghProject) {
    convoId = ghProject.rkey || ghProject.convo_id || "";
    console.log(`  Found: ${ghProject.name || ghProject.display_name}, convoId=${convoId}`);
  }
}
if (!convoId) {
  const newP = await xrpc("com.etzhayyim.mangaka.createProject", {
    name: "Ghost Hacker: 260123-jump",
    description: "Cybersecurity manga — arc0-1 import",
    projectType: "manga-series",
  });
  convoId = newP.convoId || "";
  console.log(`  Created project convoId=${convoId}`);
}

doc.convoId = convoId;

// Save document
console.log("\n=== Saving document ===");
const saveResult = await xrpc("com.etzhayyim.mangaka.saveDocument", {
  docId,
  name: doc.name,
  document: JSON.stringify(doc),
  convoId,
});
console.log("Result:", JSON.stringify(saveResult));
console.log(`\n=== Done! Refresh mangaka.etzhayyim.com → select Ghost Hacker project ===`);
