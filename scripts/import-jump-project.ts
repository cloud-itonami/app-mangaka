#!/usr/bin/env -S deno run --allow-read --allow-net
/**
 * Create a Ghost Hacker project record that links all episode documents.
 * Saves to R2 via mangaka.etzhayyim.com XRPC + direct project save.
 * AT URI: mangaka.etzhayyim.com/at/mng4k4x1.etzhayyim.com/com.etzhayyim.mangaka.project/gh-260123-jump
 */

const JUMP_DIR = "/Users/junkawasaki/github/ghosthacker/260123-jump/resources";
const MANGAKA_BASE = "https://mangaka.etzhayyim.com/xrpc/";

async function xrpc(method: string, body: Record<string, unknown>) {
  const resp = await fetch(MANGAKA_BASE + method, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return await resp.json();
}

const PROJECT_ID = "gh-260123-jump";

// Build document list from all episodes
const episodes = [...Deno.readDirSync(`${JUMP_DIR}/episodes`)]
  .filter(e => e.isDirectory).map(e => e.name).sort();

const documents: Array<{ docId: string; title: string; slug: string; pages: number; images: number; arc: string }> = [];

for (const epSlug of episodes) {
  const epPath = `${JUMP_DIR}/episodes/${epSlug}/episode.jsonld`;
  try { Deno.statSync(epPath); } catch { continue; }
  const ep = JSON.parse(Deno.readTextFileSync(epPath));
  const title = ep["dct:title"] || epSlug;
  const pages = (ep["gh:pages"] || []).length;
  const arc = ep["gh:arc"] || "";

  // Count images
  let images = 0;
  for (const pg of (ep["gh:pages"] || [])) {
    for (const pnl of (pg["gh:panels"] || [])) {
      if (pnl["generatedImageUrl"] || pnl["gh:generatedImageUrl"]) images++;
    }
  }

  documents.push({
    docId: `doc-gh-${epSlug}`,
    title,
    slug: epSlug,
    pages,
    images,
    arc,
  });
}

// Load PROJECT.jsonld for metadata
const projectMeta = JSON.parse(Deno.readTextFileSync(`${JUMP_DIR}/../PROJECT.jsonld`));

const project = {
  projectId: PROJECT_ID,
  name: "Ghost Hacker: 260123-jump",
  description: projectMeta["dct:description"] || "Cybersecurity manga series",
  projectType: "manga-series",
  convoId: "",
  documents,
  createdAt: new Date().toISOString(),
};

console.log(`=== Creating project: ${project.name} ===`);
console.log(`Documents: ${documents.length}`);
for (const d of documents) {
  console.log(`  ${d.docId}: ${d.title} (${d.pages}p, ${d.images} img)`);
}

// Save project to R2 via saveProject (dedicated project R2 path)
const r = await xrpc("com.etzhayyim.mangaka.saveProject", {
  projectId: PROJECT_ID,
  project: JSON.stringify(project),
});
console.log(`\nSave: ${JSON.stringify(r)}`);

// Also update the R2 projects index
const createR = await xrpc("com.etzhayyim.mangaka.createProject", {
  name: project.name,
  description: project.description,
  projectType: "manga-series",
});
console.log(`Project index: ${JSON.stringify(createR)}`);

const url = `https://mangaka.etzhayyim.com/at/mng4k4x1.etzhayyim.com/com.etzhayyim.mangaka.project/${PROJECT_ID}`;
console.log(`\n=== Done ===`);
console.log(`AT URI: at://mng4k4x1.etzhayyim.com/com.etzhayyim.mangaka.project/${PROJECT_ID}`);
console.log(`URL:    ${url}`);
