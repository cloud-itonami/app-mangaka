#!/usr/bin/env -S deno run --allow-read --allow-net
/**
 * Import 260123-jump (Ghost Hacker) project data into mangaka.etzhayyim.com graph.
 * Usage: deno run --allow-read --allow-net import-jump.ts [--dry-run]
 */

const JUMP_DIR = "/Users/junkawasaki/github/ghosthacker/260123-jump/resources";
const XRPC_BASE = "https://mangaka.etzhayyim.com/xrpc/";
const DRY_RUN = Deno.args.includes("--dry-run");

let created = 0;
let errors = 0;

async function xrpc(method: string, body: Record<string, unknown>) {
  if (DRY_RUN) {
    console.log(`[DRY] ${method}`, JSON.stringify(body).slice(0, 120));
    created++;
    return { ok: true };
  }
  const resp = await fetch(XRPC_BASE + method, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const r = await resp.json();
  if (r.error) {
    console.error(`  ERROR ${method}: ${r.error}`);
    errors++;
  } else {
    created++;
  }
  return r;
}

function readJsonld(path: string): any {
  try {
    return JSON.parse(Deno.readTextFileSync(path));
  } catch {
    return null;
  }
}

// --- 1. Create top-level project ---
console.log("=== Creating Ghost Hacker project ===");
const project = readJsonld(`${JUMP_DIR}/../PROJECT.jsonld`);
const topProject = await xrpc("com.etzhayyim.mangaka.createProject", {
  name: "Ghost Hacker: 260123-jump",
  description: project?.["dct:description"] || "Cybersecurity manga series",
  projectType: "manga-series",
});
const topProjectId = topProject.convoId || "gh-jump";

// --- 2. Import characters (51) ---
console.log("\n=== Importing characters ===");
const charDirs = [...Deno.readDirSync(`${JUMP_DIR}/characters`)].filter(e => e.isDirectory).map(e => e.name);
for (const slug of charDirs) {
  const profile = readJsonld(`${JUMP_DIR}/characters/${slug}/profile.jsonld`);
  if (!profile) continue;
  console.log(`  character: ${slug} — ${profile["schema:name"] || slug}`);
  await xrpc("com.etzhayyim.mangaka.createCharacter", {
    id: `chr-${slug.toLowerCase()}`,
    workId: topProjectId,
    name: profile["schema:name"] || slug,
    slug: profile["gh:slug"] || slug,
    role: profile["schema:role"] || "",
    age: profile["schema:age"],
    appearance: profile["gh:appearance"],
    background: profile["gh:background"],
    hiddenBackground: profile["gh:hiddenBackground"],
    personality: profile["gh:personality"],
    skills: profile["gh:skills"],
    relationships: profile["gh:relationships"],
    onlinePresence: profile["gh:onlinePresence"],
    arc: profile["gh:arc"] || "",
  });
}

// --- 3. Import organizations (10) ---
console.log("\n=== Importing organizations ===");
const orgDirs = [...Deno.readDirSync(`${JUMP_DIR}/organizations`)].filter(e => e.isDirectory).map(e => e.name);
for (const slug of orgDirs) {
  const profile = readJsonld(`${JUMP_DIR}/organizations/${slug}/profile.jsonld`);
  if (!profile) continue;
  const name = profile["schema:name"] || profile["dct:title"] || slug;
  console.log(`  org: ${slug} — ${name}`);
  await xrpc("com.etzhayyim.mangaka.createOrganization", {
    id: `org-${slug.toLowerCase()}`,
    workId: topProjectId,
    name,
    slug,
    businessType: profile["gh:businessType"] || "",
    location: profile["schema:location"],
    employeeCount: profile["schema:employeeCount"],
    foundingDate: profile["schema:foundingDate"],
    organizationChart: profile["gh:organizationChart"],
    incidentReport: profile["gh:incidentReport"],
    atmosphere: profile["gh:atmosphere"] || "",
    keyCharacters: profile["gh:keyCharacters"],
  });
}

// --- 4. Import environments (32) ---
console.log("\n=== Importing environments ===");
const envDirs = [...Deno.readDirSync(`${JUMP_DIR}/environments`)].filter(e => e.isDirectory).map(e => e.name);
for (const slug of envDirs) {
  const profile = readJsonld(`${JUMP_DIR}/environments/${slug}/profile.jsonld`);
  if (!profile) continue;
  const name = profile["dct:title"] || profile["schema:name"] || slug;
  console.log(`  env: ${slug} — ${name}`);
  await xrpc("com.etzhayyim.mangaka.createEnvironment", {
    id: `env-${slug}`,
    workId: topProjectId,
    name,
    slug,
    description: profile["gh:basePrompt"] || profile["dct:description"] || "",
    basePrompt: profile["gh:basePrompt"] || "",
  });
}

// --- 5. Import episodes as works + sub-projects, acts as chapters, pages, panels ---
console.log("\n=== Importing episodes ===");
const episodeDirs = [...Deno.readDirSync(`${JUMP_DIR}/episodes`)].filter(e => e.isDirectory).map(e => e.name).sort();
for (const epSlug of episodeDirs) {
  const ep = readJsonld(`${JUMP_DIR}/episodes/${epSlug}/episode.jsonld`);
  if (!ep) continue;

  const title = ep["dct:title"] || epSlug;
  const episodeId = ep["gh:episodeId"] || `episode:${epSlug}`;
  console.log(`  episode: ${epSlug} — ${title}`);

  // Create work (episode-level)
  const workId = `work-${epSlug}`;
  await xrpc("com.etzhayyim.mangaka.createWork", {
    id: workId,
    title,
    arc: ep["gh:arc"] || "",
    setting: ep["gh:setting"] || "",
    timeframe: ep["gh:timeframe"] || "",
    mainCharacter: ep["gh:mainCharacter"] || "",
    supportingCharacters: ep["gh:supportingCharacters"],
    incidentDescription: ep["gh:incidentDescription"] || "",
    industry: ep["gh:industry"] || "",
    nistFocus: ep["gh:nistFocus"],
    realCase: ep["gh:realCase"] || "",
    totalPages: ep["gh:totalPages"],
    organizationId: ep["gh:organization"] || "",
    status: "active",
  });

  // Create sub-project for this episode
  const epProject = await xrpc("com.etzhayyim.mangaka.createProject", {
    name: title,
    description: ep["gh:incidentDescription"] || title,
    parentProjectId: topProjectId,
    projectType: "episode",
    workId,
  });
  const epProjectId = epProject.convoId || workId;

  // Create acts as chapters
  const acts: any[] = ep["gh:actStructure"] || [];

  // If no actStructure, create a default chapter covering all pages
  const defaultChapterId = `${workId}-act-all`;
  if (acts.length === 0) {
    const totalPages = (ep["gh:pages"] || []).length;
    console.log(`    (no actStructure — creating default chapter for ${totalPages} pages)`);
    await xrpc("com.etzhayyim.mangaka.addChapter", {
      id: defaultChapterId,
      workId,
      chapterNum: 1,
      title: title,
      actNumber: 1,
      actTitle: title,
      narrativePurpose: ep["gh:incidentDescription"] || ep["gh:theme"] || "",
      pageRange: totalPages > 0 ? [1, totalPages] : undefined,
    });
  }

  for (const act of acts) {
    const actId = act["@id"] || `act-${act["gh:actNumber"] || 0}`;
    console.log(`    act ${act["gh:actNumber"]}: ${act["gh:actTitle"] || ""}`);
    await xrpc("com.etzhayyim.mangaka.addChapter", {
      id: `${workId}-${actId}`,
      workId,
      chapterNum: act["gh:actNumber"] ?? 0,
      title: act["gh:actTitle"] || "",
      actNumber: act["gh:actNumber"],
      actTitle: act["gh:actTitle"] || "",
      emotionalArc: act["gh:emotionalArc"] || "",
      keyBeat: act["gh:keyBeat"] || "",
      narrativePurpose: act["gh:narrativePurpose"] || "",
      pageRange: act["gh:pageRange"],
    });
  }

  // Create pages + panels
  const pages: any[] = ep["gh:pages"] || [];
  for (const pg of pages) {
    const pageNum = pg["gh:pageNumber"] ?? 0;
    const pageId = `${workId}-pg${pageNum}`;
    const actRef = pg["gh:act"] || "";

    // Use actRef-based chapterId if available, otherwise default chapter
    const chapterId = actRef ? `${workId}-${actRef}` : (acts.length > 0 ? workId : defaultChapterId);

    await xrpc("com.etzhayyim.mangaka.addPage", {
      id: pageId,
      chapterId,
      pageNum,
      pageTitle: pg["gh:pageTitle"] || "",
      pageNote: pg["gh:pageNote"] || "",
      actId: actRef,
      panelCount: pg["gh:panelCount"] || (pg["gh:panels"] || []).length,
      pageLayout: pg["gh:pageLayout"],
    });

    // Panels
    const panels: any[] = pg["gh:panels"] || [];
    for (let pi = 0; pi < panels.length; pi++) {
      const pnl = panels[pi];
      const panelId = pnl["@id"] || `${pageId}-pnl${pi}`;
      const shot = pnl["gh:shotProperties"] || {};

      await xrpc("com.etzhayyim.mangaka.createPanel", {
        id: panelId,
        pageId,
        order: pnl["panel"] ?? pi,
        characterIds: pnl["characters"],
        dialogue: pnl["dialogue"],
        environmentId: pnl["environment"] || "",
        imagePrompt: pnl["gh:imagePrompt"] || "",
        shotAngle: shot["gh:angle"] || shot["angle"] || "",
        shotAperture: shot["gh:aperture"] || shot["aperture"] || "",
        shotDistance: shot["gh:distance"] || shot["distance"] || pnl["shot"] || "",
        shotLens: shot["gh:lens"] || shot["lens"] || "",
        shotLighting: shot["gh:lighting"] || shot["lighting"] || "",
        generatedImageUrl: pnl["generatedImageUrl"] || pnl["gh:generatedImageUrl"] || "",
      });

      // Generated image history
      const genImages: any[] = pnl["gh:generatedImages"] || [];
      for (let gi = 0; gi < genImages.length; gi++) {
        const img = genImages[gi];
        await xrpc("com.etzhayyim.mangaka.recordGeneratedImage", {
          id: `${panelId}-v${gi + 1}`,
          panelId,
          workId,
          imageUrl: img["gh:imageUrl"] || "",
          imagePrompt: img["gh:imagePrompt"] || "",
          model: img["gh:model"] || "",
          generatedAt: img["gh:generatedAt"] ? new Date(img["gh:generatedAt"] * 1000).toISOString() : undefined,
          version: gi + 1,
        });
      }
    }
  }
}

console.log(`\n=== Done ===`);
console.log(`Created: ${created}, Errors: ${errors}`);
if (DRY_RUN) console.log("(DRY RUN — no data written)");
