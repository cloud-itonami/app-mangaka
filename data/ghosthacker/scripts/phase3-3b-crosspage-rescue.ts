#!/usr/bin/env -S deno run --allow-read --allow-write
/**
 * Phase 3.3b — Cross-page rescue.
 *
 * Some v1 panels were archived on the wrong v2 page because v1 page numbers
 * shifted relative to v2 (e.g., v1 p11 SMS interaction = v2 p10 SMS の罠).
 * Move those v1 panels from source page deprecated → target page active.
 *
 * On the target page, rescued v1 panels REPLACE matching v2-comprehensive-rebuild
 * placeholders (since v1 has full image generation history; placeholders are text-only).
 * Where no clean replacement, append after existing active panels.
 */
const EP = "/Users/junkawasaki/github/ghosthacker/260123-jump/resources/episodes/arc0-1-origin/episode.jsonld";
const episode = JSON.parse(await Deno.readTextFile(EP));

// Cross-page move map: { fromPage, toPage, panelIds[], rationale }
type Move = { from: number; to: number; panelIds: string[] | "all"; rationale: string };
const moves: Move[] = [
  {
    from: 7, to: 5, panelIds: "all",
    rationale: "v1 p7 (カード入力・決済) panels match v2 p5 (誘惑に負ける) — Yuto's card input is the climax of '誘惑に負ける'",
  },
  {
    from: 11, to: 10, panelIds: "all",
    rationale: "v1 p11 (親切な出品者) panels match v2 p10 (SMSの罠) — fake seller DM + SMS code 847293 is core SMS trap scene",
  },
  {
    from: 15, to: 13, panelIds: "all",
    rationale: "v1 p15 (悪口公開・友情崩壊) panels match v2 p13 (翌朝、教室 — 晒された本音) — Mei/Saki abuse exposure scene",
  },
  {
    from: 18, to: 17, panelIds: "all",
    rationale: "v1 p18 (サイト消失・404) panels match v2 p17 (絶望の濃度) — SNS乗っ取り後 darkness phase",
  },
  {
    from: 20, to: 19, panelIds: "all",
    rationale: "v1 p20 (Neiの声かけ) panels match v2 p19 (Nei、Yutoに声をかける) — same scene, off-by-one v1 numbering",
  },
  // p44 → p45 Tokyo panels: skip auto-move; v2 p45 has different ending (laptop typing).
  // The Tokyo overview is more of a chapter-end establishing, doesn't fit v2 p45 indoor scene.
];

const pages: Map<number, any> = new Map();
for (const p of episode["gh:pages"]) pages.set(p["gh:pageNumber"], p);

let stats = { moved: 0, replacedPlaceholders: 0, appended: 0 };

for (const move of moves) {
  const src = pages.get(move.from);
  const dst = pages.get(move.to);
  if (!src || !dst) continue;
  const deprecated = src["gh:deprecatedPanels"] ?? [];
  if (deprecated.length === 0) continue;

  const toMove = move.panelIds === "all"
    ? deprecated
    : deprecated.filter((p: any) => (move.panelIds as string[]).includes(p["@id"]));
  if (toMove.length === 0) continue;

  // Mark each as cross-page rescued
  const rescued = toMove.map((p: any) => {
    const r = { ...p };
    delete r["gh:deprecatedAt"];
    delete r["gh:deprecatedReason"];
    r["gh:rescued"] = true;
    r["gh:rescuedAt"] = new Date().toISOString();
    r["gh:rescuedRevision"] = "phase3.3b-crosspage-rescue";
    r["gh:rescueRationale"] = move.rationale;
    r["gh:originalPage"] = move.from;
    return r;
  });

  // On target page: replace placeholders (insertedRevision === "story-outline-v2-granularity-merged")
  // with rescued v1 panels (one-for-one until rescued runs out, then append remaining)
  const dstActive: any[] = dst["gh:panels"] ?? [];
  const placeholders = dstActive.filter((p: any) => p["gh:insertedRevision"] === "story-outline-v2-granularity-merged");
  const others = dstActive.filter((p: any) => p["gh:insertedRevision"] !== "story-outline-v2-granularity-merged");

  // Rescued panels go in place of placeholders (since they have images)
  // If rescued > placeholders, append extras
  const toReplace = Math.min(rescued.length, placeholders.length);
  const finalActive = [...others, ...rescued.slice(0, toReplace), ...placeholders.slice(toReplace), ...rescued.slice(toReplace)];
  finalActive.forEach((p: any, i: number) => {
    p["panel"] = i + 1;
    p["gh:panelIndex"] = i + 1;
  });

  dst["gh:panels"] = finalActive;
  dst["gh:panelCount"] = finalActive.length;

  // Record on target page
  if (!dst["gh:v2Migration"]["gh:crossPageRescue"]) dst["gh:v2Migration"]["gh:crossPageRescue"] = [];
  dst["gh:v2Migration"]["gh:crossPageRescue"].push({
    "gh:fromPage": move.from,
    "gh:movedCount": rescued.length,
    "gh:replacedPlaceholders": toReplace,
    "gh:appended": rescued.length - toReplace,
    "gh:rationale": move.rationale,
  });

  // Remove from source deprecated
  const remainingDep = move.panelIds === "all"
    ? []
    : deprecated.filter((p: any) => !(move.panelIds as string[]).includes(p["@id"]));
  if (remainingDep.length > 0) src["gh:deprecatedPanels"] = remainingDep;
  else delete src["gh:deprecatedPanels"];

  if (!src["gh:v2Migration"]["gh:crossPageRescueOut"]) src["gh:v2Migration"]["gh:crossPageRescueOut"] = [];
  src["gh:v2Migration"]["gh:crossPageRescueOut"].push({
    "gh:toPage": move.to,
    "gh:movedCount": rescued.length,
    "gh:rationale": move.rationale,
  });

  stats.moved += rescued.length;
  stats.replacedPlaceholders += toReplace;
  stats.appended += rescued.length - toReplace;
}

episode["gh:v2Migration"]["gh:phase"] = "phase3.3b-crosspage-rescue-applied";
episode["gh:v2Migration"]["gh:phase3.3bCrosspageRescue"] = {
  "gh:totalPanelsMoved": stats.moved,
  "gh:placeholdersReplaced": stats.replacedPlaceholders,
  "gh:appendedAfterPlaceholders": stats.appended,
  "gh:moves": moves,
  "gh:completedAt": new Date().toISOString(),
};

await Deno.writeTextFile(EP, JSON.stringify(episode, null, 2) + "\n");

console.log("=== Phase 3.3b cross-page rescue complete ===");
console.log(`Total panels moved:           ${stats.moved}`);
console.log(`Placeholders replaced:        ${stats.replacedPlaceholders}`);
console.log(`Appended (no placeholder):    ${stats.appended}`);
for (const m of moves) console.log(`  p${m.from} → p${m.to}`);
