#!/usr/bin/env -S deno run --allow-read --allow-write
/**
 * Phase 3.3 — Rescue v1 deprecated panels that still match v2 page content.
 *
 * Phase 2 archived ALL v1 panels on augment-disposition pages.
 * That was over-aggressive: pages where v1 title ~= v2 title (e.g., 幻肢痛 → 幻肢痛)
 * had v1 panels that legitimately depict v2 scenes — they should be restored.
 *
 * Rescue policy (manual decisions based on title+content review):
 *   - Full rescue: v1/v2 titles match → all v1 panels back to active
 *   - Partial rescue: v1/v2 titles partially overlap → cherry-pick matching panels
 *   - No rescue: v1/v2 different scenes → leave archived
 *
 * Cross-page rescue (e.g., v1 SMS scenes archived on p11 belong on v2 p10) is reported
 * but NOT auto-applied — too risky without human content review.
 */
const EP = "/Users/junkawasaki/github/ghosthacker/260123-jump/resources/episodes/arc0-1-origin/episode.jsonld";
const episode = JSON.parse(await Deno.readTextFile(EP));

type RescueAction = "full" | "partial" | "none";
const policy: Record<number, { action: RescueAction; rescueIds?: string[]; rationale: string; crossPageHint?: string }> = {
  2:  { action: "none",    rationale: "v1 (Title Spread Right) → v2 (誘惑の種): different scene; v2 inserts already match" },
  6:  { action: "none",    rationale: "v1 (葛藤・Yuto card decision) → v2 (Renの部屋・捜査壁): completely different scene" },
  7:  { action: "none",    rationale: "v1 (カード入力) → v2 (Hacker Nues): completely different scene", crossPageHint: "v1 panels match v2 p5 (誘惑に負ける) — consider cross-page move" },
  11: { action: "none",    rationale: "v1 (親切な出品者・SMS interaction) → v2 (乗っ取りの一瞬・Bangkok operator): different POV", crossPageHint: "v1 panels match v2 p10 (SMSの罠) — consider cross-page move" },
  15: { action: "none",    rationale: "v1 (悪口公開・Mei/Saki abuse) → v2 (母父メッセ): different scene", crossPageHint: "v1 panels match v2 p13 (晒された本音) — consider cross-page move" },
  18: { action: "none",    rationale: "v1 (サイト消失・404) → v2 (翌朝、教室の隅): different scene", crossPageHint: "v1 panels match v2 p17 (絶望の濃度・SNS乗っ取り後) — consider cross-page move" },
  20: { action: "none",    rationale: "v1 (Neiの声かけ) → v2 (NeiからRenへ): related but different POV", crossPageHint: "v1 panels match v2 p19 (Nei、Yutoに声をかける) — consider cross-page move" },

  // Same-scene full rescue (v1 ≈ v2 title)
  31: { action: "full",    rationale: "幻肢痛 ≡ 幻肢痛: v1 panels depict v2 scene; restore all" },
  32: { action: "full",    rationale: "SIP Map起動 ≡ SIP Project Map: ON: v1 panels depict v2 scene; restore all" },
  33: { action: "full",    rationale: "情報場の可視化 ≡ 情報場の可視化／ロゴ見つける: v1 panels depict v2 scene; restore all" },
  35: { action: "full",    rationale: "ゴーストハックの時間だ ≡ ゴーストハックの時間だ: same scene; restore all" },

  // Partial rescue
  41: { action: "partial", rationale: "和解と対立 → 和解／nue／バンコク: 和解 portion same; skip Saki-runs-away (not in v2)",
        rescueIds: ["panel:mh7tzCh13JPT", "panel:6crDsR8iLyQY", "panel:4pm-EqPeHLCO", "panel:GhWiL_muukJC"] },
  42: { action: "partial", rationale: "真の敵 → 3段の和解: Ren'actor-is-enemy' beat survives; skip mid-class fluff",
        rescueIds: ["panel:qSU6KdfdzWyO", "panel:eNMR4ey5Ofkw", "panel:eFIQ3ztS-97b", "panel:zZKJ-hXZlNUe"] },

  // No rescue (different v2 scene)
  43: { action: "none",    rationale: "片付けと決意 → 放課後、Renの部屋: v1 is classroom cleanup, v2 is Ren's room with new evidence" },
  44: { action: "none",    rationale: "東京を見下ろす(spread左) → 決意: v1 is exterior Tokyo overview, v2 is interior Ren's room HHKB",
        crossPageHint: "v1 Tokyo panels could be repurposed for v2 p45 epilogue ending" },
  45: { action: "none",    rationale: "決意(spread右) → エピローグ／事務所開始: v1 is exterior decision pose, v2 is laptop typing 'agency-plan.md'" },
};

let stats = { fullRescued: 0, partialRescued: 0, panelsRescued: 0, crossPageHints: 0 };
const crossPageReport: any[] = [];

for (const page of episode["gh:pages"]) {
  const pageNum = page["gh:pageNumber"];
  const policyEntry = policy[pageNum];
  if (!policyEntry) continue;
  const deprecated = page["gh:deprecatedPanels"] ?? [];
  if (deprecated.length === 0) continue;

  const rescued: any[] = [];
  const remaining: any[] = [];

  for (const dp of deprecated) {
    let shouldRescue = false;
    if (policyEntry.action === "full") shouldRescue = true;
    else if (policyEntry.action === "partial" && policyEntry.rescueIds?.includes(dp["@id"])) shouldRescue = true;

    if (shouldRescue) {
      const r = { ...dp };
      delete r["gh:deprecatedAt"];
      delete r["gh:deprecatedReason"];
      r["gh:rescued"] = true;
      r["gh:rescuedAt"] = new Date().toISOString();
      r["gh:rescuedRevision"] = "phase3.3-rescue";
      r["gh:rescueRationale"] = policyEntry.rationale;
      rescued.push(r);
    } else {
      remaining.push(dp);
    }
  }

  if (rescued.length > 0) {
    // Append rescued panels after existing active panels (production may reorder)
    const active: any[] = page["gh:panels"] ?? [];
    page["gh:panels"] = [...active, ...rescued];
    page["gh:panels"].forEach((p: any, i: number) => {
      p["panel"] = i + 1;
      p["gh:panelIndex"] = i + 1;
    });
    page["gh:panelCount"] = page["gh:panels"].length;

    if (remaining.length > 0) page["gh:deprecatedPanels"] = remaining;
    else delete page["gh:deprecatedPanels"];

    page["gh:v2Migration"]["gh:phase"] = "phase3.3-rescue-applied";
    page["gh:v2Migration"]["gh:rescue"] = {
      "gh:action": policyEntry.action,
      "gh:rationale": policyEntry.rationale,
      "gh:rescuedCount": rescued.length,
      "gh:remainingDeprecated": remaining.length,
    };

    if (policyEntry.action === "full") stats.fullRescued++;
    else if (policyEntry.action === "partial") stats.partialRescued++;
    stats.panelsRescued += rescued.length;
  } else {
    page["gh:v2Migration"]["gh:rescue"] = {
      "gh:action": "none",
      "gh:rationale": policyEntry.rationale,
    };
  }

  if (policyEntry.crossPageHint) {
    crossPageReport.push({
      page: pageNum,
      title: page["gh:pageTitle"],
      hint: policyEntry.crossPageHint,
      deprecatedCount: deprecated.length,
    });
    stats.crossPageHints++;
  }
}

episode["gh:v2Migration"]["gh:phase"] = "phase3.3-rescue-applied";
episode["gh:v2Migration"]["gh:phase3.3RescueCompleted"] = {
  "gh:fullRescuePages": stats.fullRescued,
  "gh:partialRescuePages": stats.partialRescued,
  "gh:panelsRescued": stats.panelsRescued,
  "gh:crossPageRescueHints": crossPageReport,
  "gh:completedAt": new Date().toISOString(),
};

await Deno.writeTextFile(EP, JSON.stringify(episode, null, 2) + "\n");

console.log("=== Phase 3.3 rescue complete ===");
console.log(`Pages with full rescue:        ${stats.fullRescued}`);
console.log(`Pages with partial rescue:     ${stats.partialRescued}`);
console.log(`Total panels rescued:          ${stats.panelsRescued}`);
console.log(`Cross-page hints (manual):     ${stats.crossPageHints}`);

if (crossPageReport.length > 0) {
  console.log("\n=== Cross-page rescue candidates (NOT auto-applied) ===");
  for (const r of crossPageReport) console.log(`  p${r.page} (${r.title}): ${r.hint}`);
}
