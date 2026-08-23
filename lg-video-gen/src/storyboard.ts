import * as fs from "node:fs";
import * as path from "node:path";
import { spawnSync } from "node:child_process";

export interface MotionSample { time: number; score: number }
export interface SceneCut { time: number; score: number }
export type PanelRole = "establish" | "action-peak" | "transition-out" | "transition-in" | "resolve";
export type PanelSize = "wide" | "splash" | "large" | "medium" | "small";

export interface StoryboardPanel {
  id: string;
  time: number;
  motionScore: number;
  role: PanelRole;
  size: PanelSize;
  scene: number;
  frame: string;
  rationale: string;
  rect?: { x: number; y: number; width: number; height: number };
}

export interface Storyboard {
  schema: "cloud.itonami.mangaka.storyboard.v1";
  selection: "motion-peaks-and-scene-transitions";
  readingDirection: "right-to-left-top-to-bottom";
  duration: number;
  cuts: SceneCut[];
  panels: StoryboardPanel[];
}

function run(command: string, args: string[]): string {
  const result = spawnSync(command, args, { encoding: "utf8", maxBuffer: 32 * 1024 * 1024 });
  if (result.error || result.status !== 0) {
    throw new Error(`${command} failed: ${result.error?.message ?? result.stderr}`);
  }
  return `${result.stdout ?? ""}\n${result.stderr ?? ""}`;
}

export function probeDuration(videoPath: string): number {
  const output = run("ffprobe", [
    "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", videoPath,
  ]).trim();
  const duration = Number(output);
  if (!Number.isFinite(duration) || duration <= 0) throw new Error(`invalid video duration: ${output}`);
  return duration;
}

function parseMetadata(output: string, scoreKey: string): Array<{ time: number; score: number }> {
  const rows: Array<{ time: number; score: number }> = [];
  let time: number | undefined;
  for (const line of output.split(/\r?\n/)) {
    const timeMatch = line.match(/pts_time:([0-9.]+)/);
    if (timeMatch) time = Number(timeMatch[1]);
    const scoreMatch = line.match(new RegExp(`${scoreKey}=([0-9.eE+-]+)`));
    if (scoreMatch && time !== undefined) rows.push({ time, score: Number(scoreMatch[1]) });
  }
  return rows.filter((row) => Number.isFinite(row.time) && Number.isFinite(row.score));
}

export function analyzeMotion(videoPath: string, sampleFps = 8): MotionSample[] {
  const output = run("ffmpeg", [
    "-hide_banner", "-loglevel", "info", "-i", videoPath, "-an",
    "-vf", `fps=${sampleFps},tblend=all_mode=difference,format=gray,signalstats,metadata=print`,
    "-f", "null", "-",
  ]);
  return parseMetadata(output, "lavfi.signalstats.YAVG");
}

export function detectSceneCuts(videoPath: string, threshold = 0.18): SceneCut[] {
  const output = run("ffmpeg", [
    "-hide_banner", "-loglevel", "info", "-i", videoPath, "-an",
    "-vf", `select='gt(scene,${threshold})',metadata=print`, "-vsync", "vfr", "-f", "null", "-",
  ]);
  return parseMetadata(output, "lavfi.scene_score");
}

function scoreAt(samples: MotionSample[], time: number): number {
  if (!samples.length) return 0;
  return samples.reduce((best, sample) =>
    Math.abs(sample.time - time) < Math.abs(best.time - time) ? sample : best).score;
}

function sceneIndex(cuts: SceneCut[], time: number): number {
  return cuts.filter((cut) => cut.time <= time).length;
}

function strongestInWindow(samples: MotionSample[], start: number, end: number): MotionSample {
  const available = samples.filter((sample) => sample.time >= start && sample.time < end);
  if (!available.length) return { time: (start + end) / 2, score: 0 };
  return available.reduce((best, sample) => sample.score > best.score ? sample : best);
}

export function planStoryboard(
  duration: number,
  samples: MotionSample[],
  inputCuts: SceneCut[],
  maxPanels = 8,
): Storyboard {
  const rawCuts = inputCuts
    .filter((cut) => cut.time > 0.25 && cut.time < duration - 0.25)
    .sort((a, b) => a.time - b.time);
  const cuts: SceneCut[] = [];
  for (const cut of rawCuts) {
    const previous = cuts[cuts.length - 1];
    if (previous && cut.time - previous.time < 0.25) {
      if (cut.score > previous.score) cuts[cuts.length - 1] = cut;
    } else cuts.push(cut);
  }
  const candidates: Omit<StoryboardPanel, "id" | "frame">[] = [];
  const add = (time: number, role: PanelRole, size: PanelSize, rationale: string) => candidates.push({
    time: Math.max(0, Math.min(duration - 0.01, time)),
    motionScore: scoreAt(samples, time), role, size, scene: sceneIndex(cuts, time), rationale,
  });

  add(Math.min(0.12, duration / 10), "establish", "wide", "場面・人物・位置関係を先に示す導入コマ");
  const bounds = [0, ...cuts.map((cut) => cut.time), duration];
  for (let index = 0; index < bounds.length - 1; index++) {
    const inset = Math.min(0.12, (bounds[index + 1] - bounds[index]) / 5);
    const peak = strongestInWindow(samples, bounds[index] + inset, bounds[index + 1] - inset);
    candidates.push({
      time: peak.time, motionScore: peak.score, role: "action-peak", size: "large", scene: index,
      rationale: `場面${index + 1}でフレーム差分が最大になる動作の頂点`,
    });
  }
  for (const cut of cuts) {
    add(cut.time - 0.08, "transition-out", "medium", "切り替え直前の状況を保持するコマ");
    add(cut.time + 0.08, "transition-in", "medium", "切り替え直後の背景・人物配置を示すコマ");
  }
  add(duration - 0.08, "resolve", "small", "動作後の状態を見せ、次の読みへ着地させるコマ");

  const globalPeak = candidates
    .filter((candidate) => candidate.role === "action-peak")
    .sort((a, b) => b.motionScore - a.motionScore)[0];
  if (globalPeak) globalPeak.size = "splash";

  const priority = (panel: typeof candidates[number]): number => {
    if (panel === globalPeak) return 1000;
    if (panel.role.startsWith("transition")) return 800;
    if (panel.role === "establish") return 700;
    if (panel.role === "resolve") return 600;
    return 500 + panel.motionScore;
  };
  const chosen: typeof candidates = [];
  for (const panel of [...candidates].sort((a, b) => priority(b) - priority(a))) {
    if (chosen.some((other) => Math.abs(other.time - panel.time) < 0.06)) continue;
    chosen.push(panel);
    if (chosen.length === maxPanels) break;
  }
  chosen.sort((a, b) => a.time - b.time);
  const panels: StoryboardPanel[] = chosen.map((panel, index) => ({
    ...panel,
    id: `panel-${String(index + 1).padStart(2, "0")}`,
    frame: `panel-${String(index + 1).padStart(2, "0")}.png`,
  }));
  layoutPanels(panels);
  return {
    schema: "cloud.itonami.mangaka.storyboard.v1",
    selection: "motion-peaks-and-scene-transitions",
    readingDirection: "right-to-left-top-to-bottom",
    duration,
    cuts,
    panels,
  };
}

function layoutPanels(panels: StoryboardPanel[]): void {
  const margin = 48, gutter = 18, width = 1104;
  let y = margin;
  for (let index = 0; index < panels.length;) {
    const panel = panels[index];
    if (panel.role === "transition-out" && panels[index + 1]?.role === "transition-in") {
      const half = (width - gutter) / 2;
      panel.rect = { x: margin + half + gutter, y, width: half, height: 260 };
      panels[index + 1].rect = { x: margin, y, width: half, height: 260 };
      y += 260 + gutter;
      index += 2;
    } else if (["wide", "splash"].includes(panel.size)) {
      const height = panel.size === "splash" ? 380 : 230;
      panel.rect = { x: margin, y, width, height };
      y += height + gutter;
      index += 1;
    } else if (
      panels[index + 1] &&
      !panel.role.startsWith("transition") &&
      !panels[index + 1].role.startsWith("transition") &&
      !["wide", "splash"].includes(panels[index + 1].size)
    ) {
      const half = (width - gutter) / 2;
      panel.rect = { x: margin + half + gutter, y, width: half, height: 250 };
      panels[index + 1].rect = { x: margin, y, width: half, height: 250 };
      y += 250 + gutter;
      index += 2;
    } else {
      panel.rect = { x: margin, y, width, height: 230 };
      y += 230 + gutter;
      index += 1;
    }
  }
  const bottom = y - gutter + margin;
  if (bottom > 1696) {
    const scale = (1696 - margin * 2) / (bottom - margin * 2);
    for (const panel of panels) if (panel.rect) {
      panel.rect.y = margin + (panel.rect.y - margin) * scale;
      panel.rect.height *= scale;
    }
  }
}

export function extractStoryboardFrames(videoPath: string, outDir: string, storyboard: Storyboard): string[] {
  fs.mkdirSync(outDir, { recursive: true });
  for (const panel of storyboard.panels) {
    run("ffmpeg", [
      "-hide_banner", "-loglevel", "error", "-y", "-ss", panel.time.toFixed(3), "-i", videoPath,
      "-frames:v", "1", "-q:v", "2", path.join(outDir, panel.frame),
    ]);
  }
  return storyboard.panels.map((panel) => panel.frame);
}

function xml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;",
  })[character]!);
}

export function buildStoryboardSvg(storyboard: Storyboard): string {
  const panels = storyboard.panels.map((panel, index) => {
    const rect = panel.rect!;
    const clip = `clip-${index}`;
    return `<defs><clipPath id="${clip}"><rect x="${rect.x}" y="${rect.y}" width="${rect.width}" height="${rect.height}"/></clipPath></defs>\n` +
      `<image href="${xml(panel.frame)}" x="${rect.x}" y="${rect.y}" width="${rect.width}" height="${rect.height}" preserveAspectRatio="xMidYMid slice" clip-path="url(#${clip})"/>\n` +
      `<rect x="${rect.x}" y="${rect.y}" width="${rect.width}" height="${rect.height}" fill="none" stroke="#111" stroke-width="6"/>\n` +
      `<rect x="${rect.x + 8}" y="${rect.y + 8}" width="154" height="32" rx="5" fill="white" fill-opacity="0.86"/>\n` +
      `<text x="${rect.x + 16}" y="${rect.y + 31}" font-family="sans-serif" font-size="18" fill="#111">${xml(`${panel.id} ${panel.role}`)}</text>`;
  }).join("\n");
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="1696" viewBox="0 0 1200 1696">\n` +
    `<rect width="1200" height="1696" fill="white"/>\n${panels}\n</svg>\n`;
}

export function createStoryboard(videoPath: string, outDir: string, maxPanels = 8): Storyboard {
  const duration = probeDuration(videoPath);
  const storyboard = planStoryboard(duration, analyzeMotion(videoPath), detectSceneCuts(videoPath), maxPanels);
  extractStoryboardFrames(videoPath, outDir, storyboard);
  fs.writeFileSync(path.join(outDir, "storyboard.json"), JSON.stringify(storyboard, null, 2) + "\n");
  fs.writeFileSync(path.join(outDir, "storyboard.svg"), buildStoryboardSvg(storyboard));
  return storyboard;
}
