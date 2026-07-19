#!/usr/bin/env -S deno run --allow-read --allow-net --allow-env
/**
 * One-shot enrichment pipeline. Loads ghosthacker source files from local FS and
 * POSTs to lg-mangaka enrichment / import graphs (which write to vertex_mangaka).
 *
 * Set MANGAKA_BASE=http://127.0.0.1:18001/xrpc/ (port-forward) when running locally.
 * Production: MANGAKA_BASE=https://mangaka.etzhayyim.com/xrpc/ (goes through bpmn-dispatcher).
 */

const JUMP_DIR = "/Users/junkawasaki/github/ghosthacker/260123-jump/resources";
const MANGAKA_BASE = Deno.env.get("MANGAKA_BASE") || "https://mangaka.etzhayyim.com/xrpc/";

async function xrpc(method: string, body: Record<string, unknown>): Promise<any> {
  const resp = await fetch(MANGAKA_BASE + method, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return await resp.json();
}

function readJsonSafe(path: string): any | null {
  try { return JSON.parse(Deno.readTextFileSync(path)); } catch { return null; }
}

function rkeyForChar(charFolderName: string): string { return `gh-char-${charFolderName}`; }
function rkeyForOrg(orgFolderName: string): string { return `gh-org-${orgFolderName}`; }
function rkeyForEnv(envName: string): string { return `gh-env-${envName}`; }

// === 1. Characters ===
function loadCharacterProfiles(): Record<string, any> {
  const out: Record<string, any> = {};
  try {
    for (const e of Deno.readDirSync(`${JUMP_DIR}/characters`)) {
      if (!e.isDirectory) continue;
      const prof = readJsonSafe(`${JUMP_DIR}/characters/${e.name}/profile.jsonld`);
      if (!prof) continue;
      out[rkeyForChar(e.name)] = prof;
    }
  } catch { /* */ }
  return out;
}

// === 2. Organizations ===
function loadOrgProfiles(): Record<string, any> {
  const out: Record<string, any> = {};
  try {
    for (const e of Deno.readDirSync(`${JUMP_DIR}/organizations`)) {
      if (!e.isDirectory) continue;
      const prof = readJsonSafe(`${JUMP_DIR}/organizations/${e.name}/profile.jsonld`);
      if (!prof) continue;
      out[rkeyForOrg(e.name)] = prof;
    }
  } catch { /* */ }
  return out;
}

// === 3. Environments (both .jsonld files AND subfolders) ===
function loadEnvProfiles(): Record<string, any> {
  const out: Record<string, any> = {};
  try {
    for (const e of Deno.readDirSync(`${JUMP_DIR}/environments`)) {
      if (e.isFile && e.name.endsWith(".jsonld")) {
        const name = e.name.replace(/\.jsonld$/, "");
        const prof = readJsonSafe(`${JUMP_DIR}/environments/${e.name}`);
        if (prof) out[rkeyForEnv(name)] = prof;
      } else if (e.isDirectory) {
        // Try to find a profile.jsonld or {name}.jsonld inside
        const cand1 = `${JUMP_DIR}/environments/${e.name}/profile.jsonld`;
        const cand2 = `${JUMP_DIR}/environments/${e.name}/${e.name}.jsonld`;
        const prof = readJsonSafe(cand1) || readJsonSafe(cand2) || { name: e.name, source: "folder-only" };
        out[rkeyForEnv(e.name)] = prof;
      }
    }
  } catch { /* */ }
  return out;
}

// === 4. Incidents ===
function loadIncidents(): any[] {
  const d = readJsonSafe(`${JUMP_DIR}/incidents.jsonld`);
  if (!d) return [];
  return d["gh:episodes"] || d.episodes || [];
}

// === 5. Chat history ===
function loadChatSessions(): any[] {
  const out: any[] = [];
  try {
    for (const e of Deno.readDirSync(`${JUMP_DIR}/chat_history`)) {
      if (!e.isFile || !e.name.endsWith(".jsonld")) continue;
      const d = readJsonSafe(`${JUMP_DIR}/chat_history/${e.name}`);
      if (!d) continue;
      out.push({
        sessionId: d["@id"] || e.name,
        title: d["dct:title"] || "",
        slug: d["gh:slug"] || "",
        createdAt: d["schema:dateCreated"] || "",
        messages: d["gh:messages"] || [],
      });
    }
  } catch { /* */ }
  return out;
}

// === Main ===
async function main() {
  console.log(`=== Ghost Hacker enrichment pipeline ===`);
  console.log(`MANGAKA_BASE=${MANGAKA_BASE}\n`);

  // 1. Characters
  const charProfiles = loadCharacterProfiles();
  console.log(`[1/5] enrichCharacters — ${Object.keys(charProfiles).length} profiles`);
  const r1 = await xrpc("com.etzhayyim.mangaka.enrichCharacters", { profiles: charProfiles });
  console.log(`     status=${r1.status} counts=${JSON.stringify(r1.counts)} latency=${r1.latencyMs}ms\n`);

  // 2. Organizations
  const orgProfiles = loadOrgProfiles();
  console.log(`[2/5] enrichOrganizations — ${Object.keys(orgProfiles).length} profiles`);
  const r2 = await xrpc("com.etzhayyim.mangaka.enrichOrganizations", { profiles: orgProfiles });
  console.log(`     status=${r2.status} counts=${JSON.stringify(r2.counts)} latency=${r2.latencyMs}ms\n`);

  // 3. Environments (creates new ones too)
  const envProfiles = loadEnvProfiles();
  console.log(`[3/5] enrichEnvironments — ${Object.keys(envProfiles).length} profiles`);
  const r3 = await xrpc("com.etzhayyim.mangaka.enrichEnvironments", { profiles: envProfiles });
  console.log(`     status=${r3.status} counts=${JSON.stringify(r3.counts)} latency=${r3.latencyMs}ms\n`);

  // 4. Incidents
  const incidents = loadIncidents();
  console.log(`[4/5] deriveChapterIncidents — ${incidents.length} incident groups`);
  const r4 = await xrpc("com.etzhayyim.mangaka.deriveChapterIncidents", { incidents, workRkey: "gh-work-ghost-hacker" });
  console.log(`     status=${r4.status} counts=${JSON.stringify(r4.counts)} latency=${r4.latencyMs}ms\n`);

  // 5. Chat history
  const sessions = loadChatSessions();
  console.log(`[5/5] importChatHistory — ${sessions.length} sessions`);
  const r5 = await xrpc("com.etzhayyim.mangaka.importChatHistory", { sessions, workRkey: "gh-work-ghost-hacker" });
  console.log(`     status=${r5.status} counts=${JSON.stringify(r5.counts)} latency=${r5.latencyMs}ms\n`);

  console.log("=== Done ===");
}

main().catch((err) => { console.error(err); Deno.exit(1); });
