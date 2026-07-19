#!/usr/bin/env -S deno run --allow-read --allow-write
/**
 * Migrate arc0-1-origin/episode.jsonld to v2 (260419-GH-jump.md / story-outline.jsonld).
 *
 * Insight: v2 LLM-diff inserts placed panels at v2 page numbers (panel @id "p<N>-..." matches v2 page N).
 * So current page numbers already align with v2 — no renumbering needed.
 *
 * Operations:
 *   1. Rename gh:pageTitle on every page to match v2
 *   2. Set gh:act on every page from v2 (act IDs derived from v2 actStructure)
 *   3. Replace gh:actStructure with v2 (titles + page ranges + countermeasures)
 *   4. For pages where v1 panel content doesn't match new v2 title:
 *      - Move v1 panels (no gh:inserted flag) to gh:deprecatedPanels (preserves image gen history)
 *      - If page already has v2-inserted panels, keep them
 *      - Append placeholder panels generated from v2 outline gh:script entries
 *   5. Add gh:v2Migration metadata
 *
 * Page-level disposition:
 *   - identity: title rename only, panels match v2 (e.g., countermeasures p22-29, ghost battle p30-40)
 *   - keep-and-augment: v2 inserts exist + v1 panels OK to keep mixed
 *   - replace: panels don't match new title, archive v1 + generate placeholders from v2 outline
 */
const EP = "/Users/junkawasaki/github/ghosthacker/260123-jump/resources/episodes/arc0-1-origin/episode.jsonld";
const OUTLINE = "/Users/junkawasaki/github/ghosthacker/260123-jump/resources/episodes/arc0-1-origin/story-outline.jsonld";

const episode = JSON.parse(await Deno.readTextFile(EP));
const outline = JSON.parse(await Deno.readTextFile(OUTLINE));

const v2Pages: Map<number, any> = new Map();
for (const p of outline["gh:pages"]) v2Pages.set(p["gh:pageNumber"], p);

// Disposition per page (current page number === v2 page number).
// "identity": v1 panels mostly fine — just rename title + act
// "augment":  v1 + v2-inserted panels mixed — rename title, mark v1 panels deprecated where v2 inserts cover them
// "replace":  v1 panels don't match v2 title — move all v1 panels to deprecatedPanels, leave only v2 inserts + placeholder panels from v2 outline
type Disposition = "identity" | "augment" | "replace";
const dispositionByPage: Record<number, Disposition> = {
  0:  "identity", // pretitle (matches v2 p0)
  1:  "replace",  // Tokyo skyline title spread (1 panel) → 昼休み、3日前の教室 (need new panels)
  2:  "augment",  // タイトルスプレッド右 → 誘惑の種 (2 v2 inserts already match content)
  3:  "replace",  // 登校 → 放課後、下校路 (current panels are school-gate, not walking-home)
  4:  "replace",  // 教室続き → Yutoの部屋、その夜 (current panels are classroom break)
  5:  "replace",  // 夜ベッド・偽サイト → 誘惑に負ける (current panels show "discover", v2 wants "decide")
  6:  "augment",  // 葛藤 → Renの部屋・捜査壁 (4 v2 inserts cover v2 content; v1 葛藤 panels archive)
  7:  "augment",  // カード入力 → Hacker Nues、動く (4 v2 inserts cover; v1 card panels archive)
  8:  "replace",  // 購入完了 → Yuto、3日の待機 (no v2 inserts; v1 purchase panels archive)
  9:  "replace",  // 翌日学校 → 問い合わせ
  10: "replace",  // 3日後届かない → SMSの罠
  11: "augment",  // 親切な出品者 → 乗っ取りの一瞬 (2 v2 inserts cover; v1 panels archive)
  12: "replace",  // 番号を教える → リンクの出所
  13: "replace",  // 学校・詰め寄られる → 晒された本音
  14: "replace",  // 乗っ取り発覚 → 階段、1人
  15: "augment",  // 悪口公開・友情崩壊 → 母父メッセ (2 v2 inserts cover; v1 abuse panels archive)
  16: "replace",  // カードが止まる → 夜、プレタイトルの場面に帰る
  17: "replace",  // 不正請求発覚 → 絶望の濃度
  18: "augment",  // サイト消失 → 翌朝、教室の隅 (1 v2 insert covers; v1 panels archive)
  19: "replace",  // 完全ロックアウト → Nei、Yutoに声をかける
  20: "augment",  // Neiの声かけ → NeiからRenへ (2 v2 inserts cover; v1 panels archive)
  21: "replace",  // Ren/Nei動き出す → Ren、動く (single HHKB tap panel)
  22: "identity", // countermeasures (1/2)
  23: "identity",
  24: "identity",
  25: "identity",
  26: "identity",
  27: "identity",
  28: "identity",
  29: "identity",
  30: "identity", // 翌朝、教室、罵倒
  31: "augment",  // 幻肢痛 (1 v2 insert)
  32: "augment",  // SIP Project Map (2 v2 inserts)
  33: "augment",  // 情報場の可視化 (2 v2 inserts)
  34: "identity", // ランダウアー
  35: "augment",  // ゴーストハック (2 v2 inserts)
  36: "identity", // NULL AXE
  37: "identity",
  38: "identity",
  39: "identity",
  40: "identity",
  41: "augment",  // 和解／nue／バンコク (3 v2 inserts cover bangkok/nue; v1 reconciliation panels keep)
  42: "augment",  // 3段の和解 (2 v2 inserts)
  43: "augment",  // 放課後、Renの部屋／気づき (4 v2 inserts)
  44: "augment",  // 決意 (2 v2 inserts cover; v1 spread Tokyo panels archive)
  45: "augment",  // エピローグ／事務所、始める (2 v2 inserts cover; v1 spread panels archive)
};

// Build act ID lookup from v2
const v2Acts = outline["gh:actStructure"];
function actIdForPage(pageNum: number): string {
  for (const a of v2Acts) {
    const [s, e] = a["gh:pageRange"];
    if (pageNum >= s && pageNum <= e) return a["@id"];
  }
  return "";
}

// Build placeholder panel from v2 outline gh:script entry
function placeholderPanelsFromV2(v2Page: any, pageNum: number): any[] {
  const script = v2Page["gh:script"] || [];
  const setting = v2Page["gh:setting"] ?? "";
  const visualNote = v2Page["gh:visualNote"] ?? "";
  // Group script entries into rough panels: each narration/dialogue block becomes a panel
  // For simplicity, generate 1 panel per entry (production will refine)
  const panels: any[] = [];
  let panelIdx = 1;
  for (const entry of script) {
    const type = entry["gh:type"];
    const speaker = entry["gh:speaker"];
    const text = entry["gh:text"];
    const visual = type === "narration" ? text : (type === "dialogue" || type === "monologue") ? `${speaker}: ${text}` : `${type}: ${text}`;
    panels.push({
      "@id": `panel:p${pageNum}n${panelIdx}-v2outline`,
      "characters": speaker ? [`character:${speaker}`] : [],
      "dialogue": (type === "dialogue" || type === "monologue") ? [{ speaker, text }] : [],
      "environment": "",
      "panel": panelIdx,
      "shot": "TBD",
      "visual": visual,
      "gh:inserted": true,
      "gh:insertedRevision": "story-outline-v2-comprehensive-rebuild",
      "gh:insertedSource": "story-outline.jsonld",
      "gh:scriptType": type,
      "gh:v2Setting": setting || undefined,
      "gh:v2VisualNote": visualNote || undefined,
      "gh:needsImageGeneration": true,
    });
    panelIdx++;
  }
  return panels;
}

// Iterate pages, apply disposition
const newPages: any[] = [];
let stats = { identity: 0, augment: 0, replace: 0, archived: 0, placeholders: 0 };

for (const cur of episode["gh:pages"]) {
  const pageNum = cur["gh:pageNumber"];
  const v2 = v2Pages.get(pageNum);
  if (!v2) {
    console.error(`No v2 page for ${pageNum}`);
    Deno.exit(1);
  }
  const disp = dispositionByPage[pageNum] ?? "identity";
  const panels: any[] = cur["gh:panels"] ?? [];
  const v2Panels = panels.filter((p) => p["gh:inserted"]);
  const v1Panels = panels.filter((p) => !p["gh:inserted"]);

  let newPanels: any[] = [];
  let deprecatedPanels: any[] = [];

  if (disp === "identity") {
    // v1 panels match v2 — keep all
    newPanels = panels;
  } else if (disp === "augment") {
    // v2 inserts already cover v2 content; archive v1 panels
    newPanels = v2Panels;
    deprecatedPanels = v1Panels;
  } else if (disp === "replace") {
    // v1 panels don't match new title; archive all (or keep v2 inserts if any) and add v2-outline placeholders
    newPanels = [...v2Panels, ...placeholderPanelsFromV2(v2, pageNum)];
    deprecatedPanels = v1Panels;
  }

  stats[disp]++;
  stats.archived += deprecatedPanels.length;
  if (disp === "replace") {
    stats.placeholders += newPanels.length - v2Panels.length;
  }

  // Renumber panel index sequentially
  newPanels.forEach((p, i) => {
    p["panel"] = i + 1;
    p["gh:panelIndex"] = i + 1;
  });

  // Build new page object
  const newPage: any = {
    ...cur,
    "gh:pageNumber": pageNum,
    "gh:pageTitle": v2["gh:pageTitle"],
    "gh:act": actIdForPage(pageNum),
    "gh:panels": newPanels,
    "gh:panelCount": newPanels.length,
    "gh:v2Migration": {
      "gh:phase": "phase2-comprehensive-rebuild",
      "gh:disposition": disp,
      "gh:migratedAt": new Date().toISOString(),
      "gh:v2Pov": v2["gh:pov"] ?? null,
      "gh:v2Setting": v2["gh:setting"] ?? null,
      "gh:v2VisualNote": v2["gh:visualNote"] ?? null,
      "gh:archivedPanelCount": deprecatedPanels.length,
      "gh:placeholderPanelCount": disp === "replace" ? newPanels.length - v2Panels.length : 0,
    },
  };

  if (deprecatedPanels.length > 0) {
    newPage["gh:deprecatedPanels"] = deprecatedPanels.map((p) => ({
      ...p,
      "gh:deprecatedAt": new Date().toISOString(),
      "gh:deprecatedReason": disp === "augment" ? "v1-superseded-by-v2-inserts" : "v1-page-content-replaced-by-v2",
    }));
  }

  // Drop legacy spread metadata for pages that aren't title-spread anymore
  if (pageNum !== 0 && pageNum !== 1) {
    delete newPage["gh:isSpread"];
    delete newPage["gh:spreadWith"];
  }

  // Special: page 1 was Title Spread Left in v1; now becomes "昼休み、3日前の教室" — drop spread metadata
  if (pageNum === 1) {
    delete newPage["gh:isSpread"];
    delete newPage["gh:spreadWith"];
    delete newPage["gh:pageLayout"];
  }

  newPages.push(newPage);
}

episode["gh:pages"] = newPages;
episode["gh:totalPages"] = 45;

// Replace gh:actStructure with v2
episode["gh:actStructure"] = v2Acts.map((a: any) => {
  const out: any = {
    "@id": a["@id"],
    "gh:actNumber": a["gh:actNumber"],
    "gh:actTitle": a["gh:actTitle"],
    "gh:actTitleEn": a["gh:actTitleEn"],
    "gh:emotionalArc": a["gh:emotionalArc"],
    "gh:keyBeat": a["gh:keyBeat"],
    "gh:narrativePurpose": a["gh:narrativePurpose"],
    "gh:pageRange": a["gh:pageRange"],
  };
  if (a["gh:pageCount"] !== undefined) out["gh:pageCount"] = a["gh:pageCount"];
  else if (a["gh:pageRange"]) {
    const [s, e] = a["gh:pageRange"];
    out["gh:pageCount"] = e - s + 1;
  }
  if (a["gh:countermeasures"]) out["gh:countermeasures"] = a["gh:countermeasures"];
  return out;
});

// Episode metadata
episode["dct:title"] = "Arc 0-1: 「パスワードは覚えるな」 / Ghost Hacker #00";
if (outline["dct:description"]) episode["dct:description"] = outline["dct:description"];

episode["gh:v2Migration"] = {
  "gh:phase": "phase2-comprehensive-rebuild",
  "gh:migratedAt": new Date().toISOString(),
  "gh:reference": "260419-GH-jump.md / story-outline.jsonld v2",
  "gh:operations": [
    "renamed gh:pageTitle on every page from v2 outline",
    "rebuilt gh:act on every page from v2 actStructure page ranges",
    "replaced gh:actStructure with v2 (8 acts, p0-p45)",
    `archived ${stats.archived} v1 panels into gh:deprecatedPanels (preserves image gen history for reuse)`,
    `inserted ${stats.placeholders} placeholder panels from v2 outline gh:script (gh:needsImageGeneration: true)`,
  ],
  "gh:dispositionStats": stats,
  "gh:phase3Pending": [
    "regenerate images for placeholder panels (gh:needsImageGeneration: true)",
    "fine-tune panel granularity (current placeholder = 1 script entry / panel; production typically merges to ~5-10 panels/page)",
    "review augment-disposition pages for any v1 panels that should be promoted from gh:deprecatedPanels (not all v1 panels are obsolete)",
  ],
};

await Deno.writeTextFile(EP, JSON.stringify(episode, null, 2) + "\n");

console.log("=== Phase 2 migration complete ===");
console.log(`Total pages: ${newPages.length} (p0-p${newPages.length - 1})`);
console.log(`Acts: ${episode["gh:actStructure"].length}`);
console.log("\nDisposition stats:");
console.log(`  identity (no panel changes):       ${stats.identity}`);
console.log(`  augment  (archived v1, kept v2):   ${stats.augment}`);
console.log(`  replace  (archived v1, +v2 ph):    ${stats.replace}`);
console.log(`  v1 panels archived to deprecated:  ${stats.archived}`);
console.log(`  v2 placeholder panels inserted:    ${stats.placeholders}`);
