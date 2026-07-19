#!/usr/bin/env -S deno run --allow-read --allow-write
/**
 * Phase 3.2 — Panel granularity adjustment.
 *
 * Phase 2 inserted 1 panel per v2 script entry (too granular).
 * This pass merges consecutive entries into logical panels (target 4-8 panels/page).
 *
 * Grouping rules:
 *   - Hard boundary (force new panel): narration AFTER dialogue, scene-change, section, insert,
 *     screen, sfx, telop, ad, post, dm, message, evidence-list, alert, panel, overlay, outro, memo, email, sms
 *   - Same-speaker dialogue: merge up to 3 consecutive entries by same speaker
 *   - Speaker change: allow 1 swap per panel (back-and-forth Q&A pattern)
 *   - After 4 entries in a panel: force new panel (cap)
 *
 * Only touches panels with `gh:insertedRevision: "story-outline-v2-comprehensive-rebuild"`.
 * Panels with image history (other gh:inserted revisions or v2-llm-diff) are preserved untouched.
 */
const EP = "/Users/junkawasaki/github/ghosthacker/260123-jump/resources/episodes/arc0-1-origin/episode.jsonld";
const OUTLINE = "/Users/junkawasaki/github/ghosthacker/260123-jump/resources/episodes/arc0-1-origin/story-outline.jsonld";

const episode = JSON.parse(await Deno.readTextFile(EP));
const outline = JSON.parse(await Deno.readTextFile(OUTLINE));

const v2Pages: Map<number, any> = new Map();
for (const p of outline["gh:pages"]) v2Pages.set(p["gh:pageNumber"], p);

const HARD_BREAK_TYPES = new Set([
  "scene-change", "section", "insert", "screen", "sfx", "telop", "ad",
  "post", "dm", "message", "evidence-list", "alert", "panel", "overlay",
  "outro", "memo", "email", "sms", "evidence-entry"
]);

type ScriptEntry = { "gh:type": string; "gh:speaker"?: string; "gh:text"?: string; "gh:emotion"?: string; "gh:items"?: string[]; "gh:title"?: string };

type PanelGroup = {
  entries: ScriptEntry[];
  primarySpeakers: string[];
  primaryType: string;
  visualPrimary: string;
};

function groupScriptIntoPanels(script: ScriptEntry[]): PanelGroup[] {
  const groups: PanelGroup[] = [];
  let cur: PanelGroup | null = null;
  let speakersSeen: Set<string> = new Set();
  let entriesInPanel = 0;

  const flush = () => {
    if (cur && cur.entries.length > 0) groups.push(cur);
    cur = null;
    speakersSeen = new Set();
    entriesInPanel = 0;
  };

  for (let i = 0; i < script.length; i++) {
    const e = script[i];
    const t = e["gh:type"];
    const sp = e["gh:speaker"] ?? "";

    const prevType = cur?.primaryType ?? "";
    const isHardBreak = HARD_BREAK_TYPES.has(t);
    const isNarrationAfterDialogue = t === "narration" && (prevType === "dialogue" || prevType === "monologue");
    const speakerSwitchOverflow = (t === "dialogue" || t === "monologue") && sp && speakersSeen.size >= 2 && !speakersSeen.has(sp);
    const sizeOverflow = entriesInPanel >= 4;

    if (!cur || isHardBreak || isNarrationAfterDialogue || speakerSwitchOverflow || sizeOverflow) {
      flush();
      cur = { entries: [], primarySpeakers: [], primaryType: t, visualPrimary: "" };
    }

    cur!.entries.push(e);
    if (sp) speakersSeen.add(sp);
    if (sp && !cur!.primarySpeakers.includes(sp)) cur!.primarySpeakers.push(sp);
    if (!cur!.visualPrimary) {
      // first narration / dialogue text becomes visual descriptor
      cur!.visualPrimary = e["gh:text"] ?? "";
    }
    entriesInPanel++;

    // Hard-break types complete the panel after themselves (single-entry panel)
    if (isHardBreak) flush();
  }
  flush();
  return groups;
}

function buildPanelFromGroup(group: PanelGroup, pageNum: number, panelIdx: number, v2Page: any): any {
  // Visual = primary narration text, or composite of dialogue speakers + key text
  const narrationEntries = group.entries.filter((e) => e["gh:type"] === "narration");
  const dialogueEntries = group.entries.filter((e) => e["gh:type"] === "dialogue" || e["gh:type"] === "monologue");
  const specialEntries = group.entries.filter((e) => HARD_BREAK_TYPES.has(e["gh:type"]));

  let visual: string;
  if (narrationEntries.length > 0) {
    visual = narrationEntries.map((e) => e["gh:text"]).join(" / ");
  } else if (dialogueEntries.length > 0) {
    visual = dialogueEntries.map((e) => `${e["gh:speaker"]}: 「${e["gh:text"]}」`).slice(0, 2).join(" / ");
  } else if (specialEntries.length > 0) {
    const e = specialEntries[0];
    visual = `[${e["gh:type"]}] ${e["gh:text"] ?? (e["gh:items"]?.join(", ") ?? "")}`;
  } else {
    visual = group.visualPrimary;
  }

  // Pick a reasonable shot type
  const types = group.entries.map((e) => e["gh:type"]);
  let shot = "Medium Shot";
  if (types.includes("sfx") || types.includes("insert") || types.includes("screen")) shot = "Insert";
  else if (group.primarySpeakers.length === 1 && narrationEntries.length === 0) shot = "Close Up";
  else if (narrationEntries.length > 0 && group.entries.length === 1) shot = "Wide Shot";

  // Dialogues array
  const dialogues = dialogueEntries.map((e) => ({
    speaker: e["gh:speaker"],
    text: e["gh:text"],
    ...(e["gh:emotion"] ? { emotion: e["gh:emotion"] } : {}),
  }));

  // Characters list
  const characters = [...new Set(dialogueEntries.map((e) => e["gh:speaker"]).filter(Boolean))].map((s) => `character:${s}`);

  // Construct panel
  const panel: any = {
    "@id": `panel:p${pageNum}n${panelIdx}-v2`,
    "characters": characters,
    "dialogue": dialogues,
    "environment": "",
    "panel": panelIdx,
    "shot": shot,
    "visual": visual,
    "gh:inserted": true,
    "gh:insertedRevision": "story-outline-v2-granularity-merged",
    "gh:insertedSource": "story-outline.jsonld",
    "gh:scriptEntries": group.entries,
    "gh:scriptEntryCount": group.entries.length,
    "gh:panelIndex": panelIdx,
    "gh:needsImageGeneration": true,
  };

  if (v2Page["gh:setting"]) panel["gh:v2Setting"] = v2Page["gh:setting"];
  if (v2Page["gh:visualNote"]) panel["gh:v2VisualNote"] = v2Page["gh:visualNote"];

  // Special entries (insert/screen/sfx) — capture their text in dedicated fields
  for (const e of specialEntries) {
    const t = e["gh:type"];
    const txt = e["gh:text"] ?? "";
    if (t === "screen") panel["gh:screenText"] = txt;
    else if (t === "insert") panel["gh:insertText"] = txt;
    else if (t === "sfx") panel["gh:sfx"] = txt;
    else if (t === "telop") panel["gh:telop"] = txt;
    else if (t === "ad") panel["gh:adText"] = txt;
    else if (t === "post") panel["gh:postText"] = `${e["gh:speaker"]}: ${txt}`;
    else if (t === "dm") panel["gh:dmText"] = `${e["gh:speaker"]}: ${txt}`;
    else if (t === "message") panel["gh:messageText"] = `${e["gh:speaker"]}: ${txt}`;
    else if (t === "email") panel["gh:emailText"] = `${e["gh:speaker"]}: ${txt}`;
    else if (t === "sms") panel["gh:smsText"] = `${e["gh:speaker"]}: ${txt}`;
    else if (t === "alert") panel["gh:alertText"] = txt;
    else if (t === "scene-change") panel["gh:sceneChange"] = txt;
    else if (t === "section") panel["gh:section"] = e["gh:title"] ?? txt;
    else if (t === "memo") panel["gh:memoText"] = txt;
    else if (t === "outro") panel["gh:outroText"] = txt;
    else if (t === "evidence-list") panel["gh:evidenceItems"] = e["gh:items"] ?? [];
    else if (t === "evidence-entry") panel["gh:evidenceEntry"] = txt;
    else if (t === "panel") panel["gh:panelDescription"] = txt;
    else if (t === "overlay") panel["gh:overlayText"] = txt;
  }

  return panel;
}

let totalBefore = 0;
let totalAfter = 0;
let pagesProcessed = 0;

for (const page of episode["gh:pages"]) {
  const pageNum = page["gh:pageNumber"];
  const panels = page["gh:panels"] ?? [];

  // Identify Phase 2 placeholder panels (insertedRevision === comprehensive-rebuild)
  const ph2Placeholders = panels.filter((p: any) => p["gh:insertedRevision"] === "story-outline-v2-comprehensive-rebuild");
  if (ph2Placeholders.length === 0) continue;

  const otherPanels = panels.filter((p: any) => p["gh:insertedRevision"] !== "story-outline-v2-comprehensive-rebuild");

  totalBefore += panels.length;

  // Pull script from v2 outline (the placeholder panel array exactly mirrors gh:script)
  const v2Page = v2Pages.get(pageNum);
  if (!v2Page) continue;
  const script: ScriptEntry[] = v2Page["gh:script"] ?? [];

  // Group script into panels
  const groups = groupScriptIntoPanels(script);

  // Build merged panels (panel index after otherPanels)
  let panelIdx = otherPanels.length + 1;
  const mergedPanels = groups.map((g) => buildPanelFromGroup(g, pageNum, panelIdx++, v2Page));

  // Reorder: keep otherPanels first (they're v2-llm-diff-inserted, presumably ordered earlier in scene),
  // then merged panels. Production may reorder per page layout.
  const newPanels = [...otherPanels, ...mergedPanels];
  newPanels.forEach((p: any, i: number) => {
    p["panel"] = i + 1;
    p["gh:panelIndex"] = i + 1;
  });

  page["gh:panels"] = newPanels;
  page["gh:panelCount"] = newPanels.length;
  totalAfter += newPanels.length;
  pagesProcessed++;

  // Update migration metadata
  page["gh:v2Migration"]["gh:phase"] = "phase3.2-granularity-merged";
  page["gh:v2Migration"]["gh:granularityMerged"] = {
    "gh:before": ph2Placeholders.length,
    "gh:after": mergedPanels.length,
    "gh:mergeRatio": Number((ph2Placeholders.length / Math.max(1, mergedPanels.length)).toFixed(2)),
  };
}

episode["gh:v2Migration"]["gh:phase"] = "phase3.2-granularity-merged";
episode["gh:v2Migration"]["gh:phase3.2GranularityCompleted"] = {
  "gh:pagesProcessed": pagesProcessed,
  "gh:panelsBefore": totalBefore,
  "gh:panelsAfter": totalAfter,
  "gh:reductionRatio": Number((1 - totalAfter / Math.max(1, totalBefore)).toFixed(2)),
  "gh:completedAt": new Date().toISOString(),
};

await Deno.writeTextFile(EP, JSON.stringify(episode, null, 2) + "\n");

console.log("=== Phase 3.2 granularity merge complete ===");
console.log(`Pages processed:       ${pagesProcessed}`);
console.log(`Panels before:         ${totalBefore}`);
console.log(`Panels after:          ${totalAfter}`);
console.log(`Reduction:             ${Math.round((1 - totalAfter / Math.max(1, totalBefore)) * 100)}%`);
console.log("\nPer-page panel counts after merge:");
for (const p of episode["gh:pages"]) {
  if (p["gh:v2Migration"]?.["gh:granularityMerged"]) {
    const m = p["gh:v2Migration"]["gh:granularityMerged"];
    console.log(`  p${p["gh:pageNumber"]}: ${m["gh:before"]} → ${m["gh:after"]} (${p["gh:pageTitle"]})`);
  }
}
