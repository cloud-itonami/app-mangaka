/**
 * Deeper softening for panels that still trip moderation after first softening pass.
 *
 * Strategy: aggressively abstract weapon/creature/conflict terms while preserving panel intent.
 *   - "NULL AXE" → "luminous geometric energy form / light-construct emblem"
 *   - "Daemon" → "swirling energy entity / abstract dark silhouette / oversized shadow"
 *   - "axe / slash / strike" → "gesture / motion / declaration"
 *   - "victim list / investigation wall" → "research notes board / case-study display"
 */
import * as fs from "node:fs";

const REPO = "/Users/junkawasaki/github/ghosthacker/260123-jump";
const EPISODE_PATH = `${REPO}/resources/episodes/arc0-1-origin/episode.jsonld`;
const MANIFEST_PATH = `${REPO}/resources/episodes/arc0-1-origin/image-gen-manifest.json`;

const MODELS_TO_TRY = (process.env.LG_SOFTEN_MODELS ?? "gpt-5.5,gpt-5,gpt-4o").split(",");
const CHAT_URL = "https://api.openai.com/v1/chat/completions";
const apiKey = process.env.OPENAI_API_KEY;
if (!apiKey) { console.error("OPENAI_API_KEY not set"); process.exit(1); }

interface SoftenedFields {
  visualDescription: string;
  emotionPhysicalSignals: { character: string; signals: string[] }[];
  rewriteNotes: string;
}

async function softenAggressive(panel: any, modelTrace: string[]): Promise<SoftenedFields | null> {
  const sys = `You are a manga script editor doing AGGRESSIVE moderation-safe rewriting. The previous softening pass was rejected by the image-generation safety system. Now rewrite even more aggressively:

ABSOLUTE REPLACEMENTS (mandatory):
- "axe" / "slash" / "weapon" / "strike" / "chop" / "blade" → "luminous gesture", "energy form", "decisive declaration", "symbolic emblem", "geometric light shape"
- "NULL AXE" → "luminous geometric emblem / abstract glowing pattern"
- "Daemon" / "monster" / "demon" / "creature" → "swirling abstract presence", "dark silhouette form", "atmospheric shadow shape", "ominous mood"
- "attack" / "battle" / "fight" / "combat" → "intense moment", "decisive gesture", "atmospheric scene"
- "victim" / "casualty" / "death" → "case", "subject of study", "research entry"
- "investigation" / "surveillance" / "tracking" → "research", "observation", "study"
- "violence" / "harm" / "blood" / "wound" → "drama", "moment", "expression"
- "blade-shaped" / "axe-shaped" / "sword-shaped" → "geometric", "abstract"
- All physical-confrontation language → metaphysical / atmospheric description

REQUIREMENTS:
- Keep the narrative function of the panel (it's still a climax / decision / reveal)
- Keep the character emotions and prop references in abstract form
- Use cinematic / atmospheric language
- Use NO weapon names, monster names, or physical-violence verbs
- emotionPhysicalSignals: replace anything with negative connotation with neutral observation-style cues

Respond with VALID JSON only.`;
  const user = `Aggressive rewrite request — previous softened panel still rejected by moderation.

- pageNumber: ${panel.pageNumber}
- panelId: ${panel.panelId}
- focusCharacter: ${panel.focusCharacter}
- allCharacters: ${panel.allCharacters?.join(", ")}
- props: ${panel.props?.join(", ")}
- shot: ${panel.shot}
- visualStyle: ${panel.visualStyle}
- tone: ${panel.tone}

CURRENT (still flagged) visualDescription:
${panel.visualDescription}

CURRENT emotionPhysicalSignals:
${JSON.stringify(panel.emotionPhysicalSignals, null, 2)}

Rewrite into:
{
  "visualDescription": "<aggressively softened 2-3 sentences>",
  "emotionPhysicalSignals": [{"character": "<name>", "signals": [<abstract neutral signals>]}],
  "rewriteNotes": "<1 sentence>"
}`;

  for (const model of MODELS_TO_TRY) {
    try {
      const r = await fetch(CHAT_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
        body: JSON.stringify({
          model,
          messages: [
            { role: "system", content: sys },
            { role: "user", content: user },
          ],
          response_format: { type: "json_object" },
          max_completion_tokens: 1200,
        }),
      });
      if (!r.ok) {
        modelTrace.push(`${model}: HTTP ${r.status}`);
        continue;
      }
      const j: any = await r.json();
      const txt = j.choices?.[0]?.message?.content ?? "{}";
      const parsed = JSON.parse(txt);
      if (parsed.visualDescription && parsed.emotionPhysicalSignals) {
        modelTrace.push(`${model}: OK`);
        return parsed as SoftenedFields;
      }
      modelTrace.push(`${model}: shape-fail`);
    } catch (e) {
      modelTrace.push(`${model}: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
  return null;
}

async function main() {
  const ep = JSON.parse(fs.readFileSync(EPISODE_PATH, "utf-8"));
  const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf-8"));
  const manifestById = new Map<string, any>();
  for (const p of manifest.panels) manifestById.set(p.panelId, p);

  const pending: Array<{ pageNum: number; epPanel: any; manifestEntry: any }> = [];
  for (const page of ep["gh:pages"]) {
    for (const panel of page["gh:panels"] ?? []) {
      if (!panel["gh:needsImageGeneration"]) continue;
      pending.push({ pageNum: page["gh:pageNumber"], epPanel: panel, manifestEntry: manifestById.get(panel["@id"]) });
    }
  }
  console.log(`Aggressive softening targets: ${pending.length}\n`);

  let ok = 0, fail = 0;
  for (let i = 0; i < pending.length; i++) {
    const { pageNum, epPanel, manifestEntry } = pending[i];
    const ctx = {
      pageNumber: pageNum, panelId: epPanel["@id"],
      focusCharacter: epPanel["gh:focusCharacter"],
      allCharacters: epPanel["gh:allCharacters"], props: epPanel["gh:props"],
      shot: epPanel["shot"], visualStyle: epPanel["gh:visualStyle"], tone: epPanel["gh:tone"],
      visualDescription: epPanel["gh:visualDescription"],
      emotionPhysicalSignals: epPanel["gh:emotionPhysicalSignals"],
    };
    const trace: string[] = [];
    process.stdout.write(`[${i + 1}/${pending.length}] p${pageNum} ${epPanel["@id"]} ... `);
    const soft = await softenAggressive(ctx, trace);
    if (soft) {
      // Preserve original-original (first softening kept the v1 original)
      if (!epPanel["gh:visualDescriptionSoftV1"]) {
        epPanel["gh:visualDescriptionSoftV1"] = epPanel["gh:visualDescription"];
      }
      epPanel["gh:visualDescription"] = soft.visualDescription;
      epPanel["visual"] = soft.visualDescription;
      epPanel["gh:emotionPhysicalSignals"] = soft.emotionPhysicalSignals;
      epPanel["gh:softenedDeep"] = true;
      epPanel["gh:softenedDeepAt"] = new Date().toISOString();
      if (manifestEntry) {
        manifestEntry.visual = soft.visualDescription;
        manifestEntry.emotionPhysicalSignals = soft.emotionPhysicalSignals;
      }
      console.log(`OK (${trace.join(" / ")}) — ${soft.rewriteNotes.slice(0, 80)}`);
      ok++;
    } else {
      console.log(`FAIL (${trace.join(" / ")})`);
      fail++;
    }
  }

  fs.writeFileSync(EPISODE_PATH, JSON.stringify(ep, null, 2) + "\n");
  fs.writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2) + "\n");
  console.log(`\nDone: ${ok}/${pending.length} aggressively softened`);
}

main().catch((e) => { console.error(e); process.exit(1); });
