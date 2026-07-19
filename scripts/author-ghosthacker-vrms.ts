#!/usr/bin/env -S deno run --allow-read --allow-write --allow-net --allow-env
/**
 * Batch driver for the `composeCharacterVrm` Pregel — authors a VRM
 * for every ghost-hacker character folder under
 *   60-apps/etzhayyim-project-mangaka/data/ghosthacker/resources/characters/
 * and POSTs
 *   POST {POD_BASE}/xrpc/com.etzhayyim.mangaka.composeCharacterVrm
 *        { characterRkey }
 * one row at a time. Sequential by design — running in parallel would
 * saturate the VKE render pool (each character is ~3–5 GPU min).
 *
 * Resume model:
 *   A cursor file at `--cursor` (default `./author-ghosthacker-vrms.cursor.json`)
 *   accumulates outcomes per character:
 *     {
 *       "Chise":  { "status": "authored",  "blobKey": "blobs/mangaka/vrm/...", "vertexId": "at://...", "at": "..." },
 *       "Kota":   { "status": "error",     "error":   "HTTP 503: …",                                    "at": "..." },
 *       ...
 *     }
 *   Re-running the script skips characters whose latest status is
 *   "authored" (or "unchanged") unless `--rerun-all` is passed.
 *   Errors are retried by default; pass `--skip-failed` to keep the
 *   prior `status:"error"` and move on.
 *
 * Flags:
 *   --pod-base <url>      Override POD_BASE env (default https://mangaka.etzhayyim.com)
 *   --characters-dir <p>  Override the character roster directory
 *   --cursor <path>       Cursor file (default ./author-ghosthacker-vrms.cursor.json)
 *   --rkey-prefix <s>     Character rkey prefix (default "ch-")
 *   --only <name>         Author a single character (still updates cursor)
 *   --dry-run             Print the POSTs that would happen, no fetch
 *   --rerun-all           Ignore prior "authored"/"unchanged" status in cursor
 *   --skip-failed         Keep prior "error" rows (default: retry errors)
 *   --limit <n>           Stop after authoring N characters this run
 *
 * Idempotent — the underlying Pregel ends with `attachCharacterVrm`,
 * which is content-addressed (sha256 of the VRM bytes). Re-runs over
 * already-authored characters are no-ops on the storage layer; this
 * driver short-circuits before the POST to save the GPU minutes.
 */

const DEFAULT_POD_BASE = "https://mangaka.etzhayyim.com";
const DEFAULT_CHARACTERS_DIR =
  "60-apps/etzhayyim-project-mangaka/data/ghosthacker/resources/characters";
const DEFAULT_CURSOR = "author-ghosthacker-vrms.cursor.json";
const NSID = "com.etzhayyim.mangaka.composeCharacterVrm";

interface CliArgs {
  podBase: string;
  charactersDir: string;
  cursorPath: string;
  rkeyPrefix: string;
  only: string | null;
  dryRun: boolean;
  rerunAll: boolean;
  skipFailed: boolean;
  limit: number | null;
}

type Status =
  | "authored"   // Pregel ended with state.status=authored
  | "unchanged"  // attach_vrm returned status=unchanged (idempotent)
  | "skipped"    // skipped this run (already authored, or pre-filter)
  | "error"      // pod returned non-2xx or Pregel ended with error
  | "dry";

interface CursorEntry {
  status: Status;
  blobKey?: string;
  vertexId?: string;
  rigSource?: string;     // "rigify" | "rignet"
  iterations?: number;
  error?: string;
  at: string;             // ISO 8601 of last attempt
}

type Cursor = Record<string, CursorEntry>;

interface ComposeResult {
  status?: "authored" | "skipped" | "error";
  blobKey?: string;
  vertexId?: string;
  rigSource?: string;
  iterations?: number;
  error?: string;
}

function parseArgs(): CliArgs {
  const args = Deno.args;
  let podBase = Deno.env.get("POD_BASE") ?? DEFAULT_POD_BASE;
  let charactersDir = DEFAULT_CHARACTERS_DIR;
  let cursorPath = DEFAULT_CURSOR;
  let rkeyPrefix = "ch-";
  let only: string | null = null;
  let dryRun = false;
  let rerunAll = false;
  let skipFailed = false;
  let limit: number | null = null;

  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    switch (a) {
      case "--pod-base":         podBase       = args[++i] ?? podBase; break;
      case "--characters-dir":   charactersDir = args[++i] ?? charactersDir; break;
      case "--cursor":           cursorPath    = args[++i] ?? cursorPath; break;
      case "--rkey-prefix":      rkeyPrefix    = args[++i] ?? rkeyPrefix; break;
      case "--only":             only          = args[++i] ?? null; break;
      case "--dry-run":          dryRun        = true; break;
      case "--rerun-all":        rerunAll      = true; break;
      case "--skip-failed":      skipFailed    = true; break;
      case "--limit":            limit         = Number(args[++i] ?? "") || null; break;
      case "-h": case "--help":
        printHelp();
        Deno.exit(0);
        break;
      default:
        if (a.startsWith("-")) {
          console.error(`unknown flag: ${a}`);
          Deno.exit(2);
        }
    }
  }
  podBase = podBase.replace(/\/$/, "");
  return { podBase, charactersDir, cursorPath, rkeyPrefix, only, dryRun, rerunAll, skipFailed, limit };
}

function printHelp() {
  console.log(`author-ghosthacker-vrms.ts — batch driver for compose_character_vrm

Usage:
  POD_BASE=https://mangaka.etzhayyim.com MANGAKA_API_KEY=...
  deno run -A scripts/author-ghosthacker-vrms.ts [flags]

Flags:
  --pod-base <url>      Override POD_BASE env (default ${DEFAULT_POD_BASE})
  --characters-dir <p>  Roster dir (default ${DEFAULT_CHARACTERS_DIR})
  --cursor <path>       Cursor file (default ${DEFAULT_CURSOR})
  --rkey-prefix <s>     Character rkey prefix (default "ch-")
  --only <name>         Author a single character folder
  --dry-run             Print POSTs without firing them
  --rerun-all           Ignore prior authored/unchanged in cursor
  --skip-failed         Keep prior error rows (default: retry)
  --limit <n>           Stop after authoring N characters this run
`);
}

async function loadCursor(path: string): Promise<Cursor> {
  try {
    const txt = await Deno.readTextFile(path);
    return JSON.parse(txt) as Cursor;
  } catch {
    return {};
  }
}

async function saveCursor(path: string, cursor: Cursor): Promise<void> {
  await Deno.writeTextFile(path, JSON.stringify(cursor, null, 2) + "\n");
}

async function listCharacters(dir: string): Promise<string[]> {
  const names: string[] = [];
  for await (const entry of Deno.readDir(dir)) {
    if (!entry.isDirectory) continue;
    if (entry.name.startsWith(".")) continue;
    names.push(entry.name);
  }
  names.sort();
  return names;
}

function shouldSkip(prior: CursorEntry | undefined, rerunAll: boolean, skipFailed: boolean): boolean {
  if (!prior) return false;
  if (rerunAll) return false;
  if (prior.status === "authored" || prior.status === "unchanged") return true;
  if (prior.status === "error" && skipFailed) return true;
  return false;
}

async function compose(podBase: string, characterRkey: string): Promise<ComposeResult> {
  const apiKey = Deno.env.get("MANGAKA_API_KEY") ?? "";
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (apiKey) headers["x-api-key"] = apiKey;
  const endpoint = `${podBase}/xrpc/${NSID}`;
  const r = await fetch(endpoint, {
    method: "POST",
    headers,
    body: JSON.stringify({ characterRkey }),
  });
  if (!r.ok) {
    return { status: "error", error: `HTTP ${r.status}: ${(await r.text()).slice(0, 240)}` };
  }
  return (await r.json()) as ComposeResult;
}

async function main() {
  const cfg = parseArgs();
  console.log(`[author-ghosthacker-vrms] roster   ${cfg.charactersDir}`);
  console.log(`[author-ghosthacker-vrms] endpoint ${cfg.podBase}/xrpc/${NSID}`);
  console.log(`[author-ghosthacker-vrms] cursor   ${cfg.cursorPath}`);
  console.log(`[author-ghosthacker-vrms] rkey     ${cfg.rkeyPrefix}<folder>`);
  if (cfg.dryRun) console.log("[author-ghosthacker-vrms] --dry-run (no POST)");
  if (cfg.rerunAll) console.log("[author-ghosthacker-vrms] --rerun-all (ignore cursor authored/unchanged)");
  if (cfg.skipFailed) console.log("[author-ghosthacker-vrms] --skip-failed (keep prior errors)");
  if (cfg.only) console.log(`[author-ghosthacker-vrms] --only ${cfg.only}`);
  if (cfg.limit !== null) console.log(`[author-ghosthacker-vrms] --limit ${cfg.limit}`);
  console.log("");

  const cursor = await loadCursor(cfg.cursorPath);
  let allNames = await listCharacters(cfg.charactersDir);
  if (cfg.only) {
    if (!allNames.includes(cfg.only)) {
      console.error(`[author-ghosthacker-vrms] --only ${cfg.only} not found under ${cfg.charactersDir}`);
      Deno.exit(2);
    }
    allNames = [cfg.only];
  }

  let authored = 0;
  const tally = { authored: 0, unchanged: 0, skipped: 0, error: 0, dry: 0 };

  for (const name of allNames) {
    const rkey = cfg.rkeyPrefix + name.toLowerCase();
    const prior = cursor[name];
    if (shouldSkip(prior, cfg.rerunAll, cfg.skipFailed)) {
      console.log(`  skip ${name}  (prior=${prior?.status})`);
      tally.skipped++;
      continue;
    }
    if (cfg.dryRun) {
      console.log(`  [dry] ${name} → POST { characterRkey: "${rkey}" }`);
      cursor[name] = { status: "dry", at: new Date().toISOString() };
      tally.dry++;
      continue;
    }

    const tStart = Date.now();
    process.stdout?.write?.(`  ${name} → ${rkey} ... `);
    const r = await compose(cfg.podBase, rkey).catch((e) => ({
      status: "error" as const,
      error: String(e),
    } satisfies ComposeResult));
    const dt = ((Date.now() - tStart) / 1000).toFixed(1);

    const entry: CursorEntry = {
      status:
        r.status === "authored" ? "authored" :
        r.status === "skipped"  ? "unchanged" :
        "error",
      blobKey:    r.blobKey,
      vertexId:   r.vertexId,
      rigSource:  r.rigSource,
      iterations: r.iterations,
      error:      r.error,
      at: new Date().toISOString(),
    };
    cursor[name] = entry;
    await saveCursor(cfg.cursorPath, cursor); // persist after every row — resume on Ctrl-C

    if (entry.status === "error") {
      console.log(`✗ ${dt}s ${entry.error}`);
      tally.error++;
    } else if (entry.status === "unchanged") {
      console.log(`= ${dt}s blobKey=${entry.blobKey}`);
      tally.unchanged++;
    } else {
      console.log(`✓ ${dt}s blobKey=${entry.blobKey}  rig=${entry.rigSource ?? "?"}  iter=${entry.iterations ?? "?"}`);
      tally.authored++;
      authored++;
      if (cfg.limit !== null && authored >= cfg.limit) {
        console.log(`\n[author-ghosthacker-vrms] --limit ${cfg.limit} reached, stopping`);
        break;
      }
    }
  }

  // Persist dry-run + final state. Live runs already saved after every
  // row (so Ctrl-C resumes); this final save is the only persistence
  // hook for dry-runs.
  await saveCursor(cfg.cursorPath, cursor);

  console.log("");
  console.log(`summary: ${tally.authored} authored · ${tally.unchanged} unchanged · ${tally.skipped} skipped · ${tally.error} error · ${tally.dry} dry`);
  if (tally.error > 0) Deno.exit(1);
}

main();
