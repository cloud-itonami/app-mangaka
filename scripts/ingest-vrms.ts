#!/usr/bin/env -S deno run --allow-read --allow-net --allow-env
/**
 * Ingest VRM files into mangaka.etzhayyim.com.
 *
 * P13 of ADR-2605141200 — VRM character ingestion. Walks a directory of
 * character folders (`<NAME>/avatar.vrm` per the ghost-hacker layout),
 * base64-encodes each, and POSTs to
 *   POST {POD_BASE}/xrpc/com.etzhayyim.mangaka.tools.attachCharacterVrm
 *
 * The tool validates the glTF magic, uploads to B2 with content-addressed
 * key `blobs/mangaka/vrm/{sha256hex}`, and patches the character vertex's
 * `props.vrmBlobKey` so `tool_resolve_assets` (Pregel step 2) picks it
 * up next render.
 *
 * Usage:
 *   POD_BASE=https://lg-mangaka-internal MANGAKA_API_KEY=... \
 *     deno run -A scripts/ingest-vrms.ts data/ghosthacker/resources/characters
 *
 *   # Dry run (don't actually POST):
 *   deno run -A scripts/ingest-vrms.ts ./characters --dry-run
 *
 *   # Override character rkey (default: `ch-<lowercase folder name>`):
 *   deno run -A scripts/ingest-vrms.ts ./characters --rkey-prefix ch-gh-
 *
 * Skips folders without an `avatar.vrm`. Skips already-attached characters
 * (the tool returns `status: "unchanged"` and we count those as success).
 *
 * Idempotent — content-addressed dedup means re-running is a no-op when
 * nothing changed.
 */

const POD_BASE = (Deno.env.get("POD_BASE") ?? "https://mangaka.etzhayyim.com").replace(/\/$/, "");
const API_KEY = Deno.env.get("MANGAKA_API_KEY") ?? "";
const ENDPOINT = `${POD_BASE}/xrpc/com.etzhayyim.mangaka.tools.attachCharacterVrm`;

interface CliArgs {
  dir: string;
  dryRun: boolean;
  rkeyPrefix: string;
  vrmFilename: string;
}

function parseArgs(): CliArgs {
  const args = Deno.args;
  let dir = "";
  let dryRun = false;
  let rkeyPrefix = "ch-";
  let vrmFilename = "avatar.vrm";
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "--dry-run") dryRun = true;
    else if (a === "--rkey-prefix") rkeyPrefix = args[++i] ?? rkeyPrefix;
    else if (a === "--vrm-name") vrmFilename = args[++i] ?? vrmFilename;
    else if (!a.startsWith("-")) dir = a;
    else {
      console.error(`unknown flag: ${a}`);
      Deno.exit(2);
    }
  }
  if (!dir) {
    console.error("usage: ingest-vrms.ts <characters-dir> [--dry-run] [--rkey-prefix <prefix>] [--vrm-name <file>]");
    Deno.exit(2);
  }
  return { dir, dryRun, rkeyPrefix, vrmFilename };
}

interface AttachResult {
  blobKey?: string;
  vertexId?: string;
  status?: "attached" | "unchanged";
  warning?: string;
  error?: string;
}

async function attach(characterRkey: string, vrmPath: string): Promise<AttachResult> {
  const bytes = await Deno.readFile(vrmPath);
  // base64 encode in chunks (TextDecoder approach) — Deno's btoa needs binary
  // string input, which would push 5-50 MB to a single string. Use the
  // chunked encoder from std/encoding/base64.
  const { encodeBase64 } = await import("https://deno.land/std@0.224.0/encoding/base64.ts");
  const b64 = encodeBase64(bytes);
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (API_KEY) headers["x-api-key"] = API_KEY;
  const r = await fetch(ENDPOINT, {
    method: "POST",
    headers,
    body: JSON.stringify({ characterRkey, vrmContentB64: b64 }),
  });
  if (!r.ok) {
    return { error: `HTTP ${r.status}: ${(await r.text()).slice(0, 240)}` };
  }
  return (await r.json()) as AttachResult;
}

async function main() {
  const { dir, dryRun, rkeyPrefix, vrmFilename } = parseArgs();
  console.log(`[ingest-vrms] scanning ${dir}`);
  console.log(`[ingest-vrms] endpoint ${ENDPOINT}`);
  console.log(`[ingest-vrms] rkey  ${rkeyPrefix}<folder>`);
  console.log(`[ingest-vrms] vrm   ${vrmFilename} per folder`);
  if (dryRun) console.log("[ingest-vrms] --dry-run (no POST)");
  console.log("");

  const results: { name: string; rkey: string; result: AttachResult | null }[] = [];
  for await (const entry of Deno.readDir(dir)) {
    if (!entry.isDirectory) continue;
    const vrmPath = `${dir}/${entry.name}/${vrmFilename}`;
    try {
      await Deno.stat(vrmPath);
    } catch {
      console.log(`  skip ${entry.name}: no ${vrmFilename}`);
      continue;
    }
    const rkey = rkeyPrefix + entry.name.toLowerCase();
    if (dryRun) {
      const size = (await Deno.stat(vrmPath)).size;
      console.log(`  [dry] ${entry.name} → ${rkey}  (${(size / 1024 / 1024).toFixed(1)} MB)`);
      results.push({ name: entry.name, rkey, result: null });
      continue;
    }
    process.stdout?.write?.(`  ${entry.name} → ${rkey} ... `);
    const r = await attach(rkey, vrmPath).catch((e) => ({ error: String(e) }));
    results.push({ name: entry.name, rkey, result: r });
    if (r.error) console.log(`✗ ${r.error}`);
    else if (r.warning) console.log(`⚠ ${r.warning}  blobKey=${r.blobKey}`);
    else console.log(`✓ ${r.status}  blobKey=${r.blobKey}`);
  }

  console.log("");
  const ok = results.filter((x) => x.result && !x.result.error && !x.result.warning).length;
  const warn = results.filter((x) => x.result?.warning).length;
  const err = results.filter((x) => x.result?.error).length;
  const skipped = results.filter((x) => x.result === null).length;
  console.log(`summary: ${ok} ok · ${warn} warn · ${err} err · ${skipped} dry/skip`);
  if (err > 0) Deno.exit(1);
}

main();
