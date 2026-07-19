#!/usr/bin/env -S deno run --allow-read --allow-write
/**
 * Phase 3.1 — Image generation prep.
 *
 * After Phases 2, 3.2, 3.3, 3.3b, identify all panels that still need image generation
 * (gh:needsImageGeneration: true), build prompts following the existing v2-llm-diff
 * imagePrompt convention, and output a manifest for batch image generation.
 *
 * Output:
 *   - resources/episodes/arc0-1-origin/image-gen-manifest.json: list of {pageNum, panelId, prompt, outputPath, characters, references}
 *   - scripts/phase3-1-run-image-gen.sh: runnable shell script (calls existing pipeline)
 *
 * Each prompt follows the existing convention from v2-llm-diff inserts:
 *   "Anime / manga panel illustration. Cinematic storyboard thumbnail sketch, rough
 *   compositional guide for animators, layout reference. {visual}. Shot type: {shot}.
 *   Dialogue: {speaker}: {text}. Rough sketch aesthetic, monochrome with screen tones,
 *   cinematic composition. ... [reference selections per character]"
 */
const EP = "/Users/junkawasaki/github/ghosthacker/260123-jump/resources/episodes/arc0-1-origin/episode.jsonld";
const MANIFEST = "/Users/junkawasaki/github/ghosthacker/260123-jump/resources/episodes/arc0-1-origin/image-gen-manifest.json";
const RUN_SCRIPT = "/Users/junkawasaki/github/ghosthacker/260123-jump/scripts/phase3-1-run-image-gen.sh";

const episode = JSON.parse(await Deno.readTextFile(EP));

const STYLE_PREFIX = "Anime / manga panel illustration. Cinematic storyboard thumbnail sketch, rough compositional guide for animators, layout reference.";
const STYLE_SUFFIX = "Rough sketch aesthetic, monochrome with screen tones, cinematic composition.";
const REF_GUIDANCE = "Use the supplied face reference image(s) only for character identity: face shape, eye design, hairstyle, age impression, and manga line style. The supplied references may use different face angles and expressions; follow those angle/expression cues only when they match the current scene. Do not preserve the reference outfit, clothing, body pose, background, or props. Clothing must follow the current scene description, school setting, and panel prompt. Generate a new single-panel storyboard image for the described scene.";

function buildPrompt(panel: any, page: any): string {
  const visual = panel["visual"] ?? "";
  const shot = panel["shot"] ?? "Medium Shot";
  const dialogues: any[] = panel["dialogue"] ?? [];
  const setting = panel["gh:v2Setting"] ?? page["gh:v2Migration"]?.["gh:v2Setting"] ?? "";
  const visualNote = panel["gh:v2VisualNote"] ?? page["gh:v2Migration"]?.["gh:v2VisualNote"] ?? "";

  const parts: string[] = [STYLE_PREFIX];
  if (setting) parts.push(`Setting: ${setting}.`);
  if (visualNote) parts.push(`Visual note: ${visualNote}.`);
  parts.push(visual + ".");
  parts.push(`Shot type: ${shot}.`);
  if (dialogues.length > 0) {
    const dlg = dialogues.map((d) => `${d.speaker}: 「${d.text}」${d.emotion ? ` (${d.emotion})` : ""}`).join(" / ");
    parts.push(`Dialogue: ${dlg}.`);
  }
  // Special-text fields
  for (const k of ["gh:screenText", "gh:insertText", "gh:sfx", "gh:telop", "gh:adText", "gh:postText", "gh:dmText", "gh:messageText", "gh:emailText", "gh:smsText", "gh:alertText", "gh:sceneChange", "gh:section", "gh:memoText", "gh:outroText", "gh:overlayText", "gh:panelDescription", "gh:evidenceEntry"]) {
    if (panel[k]) parts.push(`${k.replace("gh:", "")}: ${panel[k]}.`);
  }
  if (panel["gh:evidenceItems"]) parts.push(`evidenceItems: ${panel["gh:evidenceItems"].join("; ")}.`);
  parts.push(STYLE_SUFFIX);
  parts.push(REF_GUIDANCE);
  return parts.join(" ");
}

const manifest: any[] = [];
let totalNeedGen = 0;

for (const page of episode["gh:pages"]) {
  const panels = page["gh:panels"] ?? [];
  for (const panel of panels) {
    if (!panel["gh:needsImageGeneration"]) continue;
    totalNeedGen++;
    const pageNum = page["gh:pageNumber"];
    const panelId = panel["@id"];
    const safeId = panelId.replace(/[^a-zA-Z0-9._-]/g, "_");
    const outputDir = `/Users/junkawasaki/github/ghosthacker/260123-jump/resources/images/episodes/episode:arc0-1-origin/pages/${pageNum}`;
    const outputFile = `${outputDir}/panel_${safeId}_sketch_v1.png`;
    const characters = (panel["characters"] ?? []).map((c: string) => c.replace("character:", ""));
    manifest.push({
      pageNum,
      panelId,
      panelIndex: panel["gh:panelIndex"],
      pageTitle: page["gh:pageTitle"],
      shot: panel["shot"],
      visual: panel["visual"],
      characters,
      dialogues: panel["dialogue"] ?? [],
      prompt: buildPrompt(panel, page),
      outputPath: outputFile,
      outputDir,
      referenceCharacters: characters,
      // Reference selection: default to focused_3q_right (most common in existing prompts)
      referenceSelections: characters.map((c: string) => ({ character: c, variant: "focused_3q_right", note: "default reference" })),
    });
  }
}

await Deno.writeTextFile(MANIFEST, JSON.stringify({
  episode: "episode:arc0-1-origin",
  generatedAt: new Date().toISOString(),
  totalPanels: totalNeedGen,
  defaultModel: "openai/gpt-image-2",
  panels: manifest,
}, null, 2) + "\n");

// Build runnable shell script (uses gpt-image generation pipeline; user customizes API key + endpoint)
const shellLines: string[] = [
  "#!/usr/bin/env bash",
  "# Phase 3.1 — Run image generation for all panels needing images.",
  "# Generated by phase3-1-image-gen-prep.ts on " + new Date().toISOString(),
  "# Total panels to generate: " + totalNeedGen,
  "# REQUIRES: OPENAI_API_KEY env var (or compatible image gen API)",
  "# CUSTOMIZE: replace gen_image() with your actual pipeline (e.g., LangGraph layered-bg-mask-character-edit)",
  "",
  "set -euo pipefail",
  "",
  'MANIFEST="/Users/junkawasaki/github/ghosthacker/260123-jump/resources/episodes/arc0-1-origin/image-gen-manifest.json"',
  "",
  "gen_image() {",
  '  local prompt="$1"',
  '  local outpath="$2"',
  '  local refs="$3"',
  '  echo "==> $outpath"',
  '  mkdir -p "$(dirname "$outpath")"',
  '  # Replace this with actual API call. Example using an image gen CLI:',
  '  # image-gen --prompt "$prompt" --refs "$refs" --output "$outpath" --model gpt-image-2',
  '  echo "[STUB] Would generate image with prompt length=${#prompt}, refs=$refs"',
  "}",
  "",
  "jq -c '.panels[]' \"$MANIFEST\" | while read -r panel; do",
  '  prompt=$(echo "$panel" | jq -r ".prompt")',
  '  outpath=$(echo "$panel" | jq -r ".outputPath")',
  '  refs=$(echo "$panel" | jq -r ".referenceCharacters | join(\\",\\")")',
  '  gen_image "$prompt" "$outpath" "$refs"',
  "done",
  "",
  "echo \"Done. $((${TOTAL:-0})) panels processed.\"",
];
await Deno.writeTextFile(RUN_SCRIPT, shellLines.join("\n") + "\n");
await Deno.chmod(RUN_SCRIPT, 0o755);

// Update episode metadata
episode["gh:v2Migration"]["gh:phase"] = "phase3.1-image-gen-prep";
episode["gh:v2Migration"]["gh:phase3.1ImageGenPrep"] = {
  "gh:totalPanelsNeedingImages": totalNeedGen,
  "gh:manifestPath": MANIFEST,
  "gh:runScript": RUN_SCRIPT,
  "gh:nextStep": "review prompts in image-gen-manifest.json, customize gen_image() in phase3-1-run-image-gen.sh, then execute",
  "gh:completedAt": new Date().toISOString(),
};
await Deno.writeTextFile(EP, JSON.stringify(episode, null, 2) + "\n");

console.log("=== Phase 3.1 image gen prep complete ===");
console.log(`Total panels needing images:    ${totalNeedGen}`);
console.log(`Manifest written:               ${MANIFEST}`);
console.log(`Run script written:             ${RUN_SCRIPT}`);
console.log("\nPer-page panels needing gen:");
const byPage = new Map<number, number>();
for (const m of manifest) byPage.set(m.pageNum, (byPage.get(m.pageNum) ?? 0) + 1);
for (const [pn, ct] of [...byPage.entries()].sort((a, b) => a[0] - b[0])) {
  console.log(`  p${pn}: ${ct}`);
}
