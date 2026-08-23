#!/usr/bin/env node
import * as fs from "node:fs";
import * as path from "node:path";
import { compileScenePrompt, type ScenePromptInput } from "./prompt.js";
import {
  MiniMaxClient, buildManifestEdn, resolveModel, validateVideoRequest, waitForTask,
  type H3Ratio, type H3VideoRequest, type HailuoVideoRequest, type VideoRequest,
} from "./minimax.js";
import { createStoryboard } from "./storyboard.js";

interface Cli {
  prompt?: string;
  scene?: string;
  out: string;
  model: string;
  duration: number;
  resolution: "768P" | "1080P" | "2K";
  ratio: H3Ratio;
  firstFrame?: string;
  optimize: boolean;
  dryRun: boolean;
  resume?: string;
  pollMs: number;
  timeoutMs: number;
  maxPanels: number;
}

function parseArgs(argv: string[]): Cli {
  const cli: Cli = {
    out: "out/minimax-h3-scene", model: "h3", duration: 6, resolution: "768P", ratio: "16:9",
    optimize: false, dryRun: false, pollMs: 10_000, timeoutMs: 20 * 60_000, maxPanels: 8,
  };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--prompt") cli.prompt = argv[++i];
    else if (arg === "--scene") cli.scene = argv[++i];
    else if (arg === "--out") cli.out = argv[++i];
    else if (arg === "--model") cli.model = argv[++i];
    else if (arg === "--duration") cli.duration = Number(argv[++i]);
    else if (arg === "--resolution") cli.resolution = argv[++i] as Cli["resolution"];
    else if (arg === "--ratio") cli.ratio = argv[++i] as H3Ratio;
    else if (arg === "--first-frame") cli.firstFrame = argv[++i];
    else if (arg === "--optimize") cli.optimize = true;
    else if (arg === "--dry-run") cli.dryRun = true;
    else if (arg === "--resume") cli.resume = argv[++i];
    else if (arg === "--poll-ms") cli.pollMs = Number(argv[++i]);
    else if (arg === "--timeout-ms") cli.timeoutMs = Number(argv[++i]);
    else if (arg === "--max-panels") cli.maxPanels = Number(argv[++i]);
    else throw new Error(`unknown argument: ${arg}`);
  }
  if (!Number.isInteger(cli.maxPanels) || cli.maxPanels < 3 || cli.maxPanels > 12) {
    throw new Error("--max-panels must be an integer from 3 through 12");
  }
  return cli;
}

function imageValue(value: string | undefined): string | undefined {
  if (!value || /^https?:|^data:image\//.test(value)) return value;
  const absolute = path.resolve(value);
  const extension = path.extname(absolute).slice(1).toLowerCase().replace("jpg", "jpeg");
  if (!["jpeg", "png", "webp", "heic", "heif"].includes(extension)) {
    throw new Error("frame image must be JPG, PNG, WebP, HEIC, or HEIF");
  }
  const bytes = fs.readFileSync(absolute);
  if (bytes.length > 30 * 1024 * 1024) throw new Error("H3 frame image must be at most 30MB");
  return `data:image/${extension};base64,${bytes.toString("base64")}`;
}

function loadScene(cli: Cli): { input: ScenePromptInput; source: string } {
  if (cli.scene) {
    const source = path.resolve(cli.scene);
    return { input: JSON.parse(fs.readFileSync(source, "utf8")), source };
  }
  if (cli.prompt) return { input: { prompt: cli.prompt }, source: "cli:--prompt" };
  throw new Error("provide --prompt <text> or --scene <scene.json>");
}

function makeRequest(cli: Cli, prompt: string): VideoRequest {
  const model = resolveModel(cli.model);
  if (model === "MiniMax-H3") {
    if (cli.resolution === "1080P") throw new Error("MiniMax-H3 uses 768P or 2K, not 1080P");
    const frame = imageValue(cli.firstFrame);
    const request: H3VideoRequest = {
      model,
      content: [
        { type: "text", text: prompt },
        ...(frame ? [{ type: "image_url" as const, image_url: { url: frame }, role: "first_frame" as const }] : []),
      ],
      duration: cli.duration,
      resolution: cli.resolution,
      ratio: frame ? "adaptive" : cli.ratio,
    };
    return request;
  }
  if (cli.resolution === "2K") throw new Error("Hailuo 2.3 does not support 2K in the V1 API");
  const request: HailuoVideoRequest = {
    model,
    prompt,
    duration: cli.duration as 6 | 10,
    resolution: cli.resolution,
    prompt_optimizer: cli.optimize,
    ...(cli.optimize ? { fast_pretreatment: true } : {}),
    ...(cli.firstFrame ? { first_frame_image: imageValue(cli.firstFrame) } : {}),
  };
  return request;
}

async function main(): Promise<void> {
  const cli = parseArgs(process.argv.slice(2));
  fs.mkdirSync(cli.out, { recursive: true });
  const { input, source } = loadScene(cli);
  const request = makeRequest(cli, compileScenePrompt(input));
  validateVideoRequest(request);
  const requestPath = path.join(cli.out, "request.json");
  fs.writeFileSync(requestPath, JSON.stringify(request, null, 2) + "\n");

  if (cli.dryRun) {
    fs.writeFileSync(path.join(cli.out, "manifest.edn"), buildManifestEdn({ request, promptSource: source }));
    console.log(`DRY RUN: ${request.model} ${request.duration}s ${request.resolution}`);
    console.log(`request: ${requestPath}`);
    return;
  }

  const client = new MiniMaxClient(process.env.MINIMAX_API_KEY ?? "");
  const taskId = cli.resume ?? await client.create(request);
  const taskPath = path.join(cli.out, "task.json");
  fs.writeFileSync(taskPath, JSON.stringify({ taskId, status: "submitted", request }, null, 2) + "\n");
  console.log(`${cli.resume ? "Resuming" : "Submitted"} MiniMax task ${taskId}`);

  const state = await waitForTask(client, taskId, {
    model: request.model,
    intervalMs: cli.pollMs,
    timeoutMs: cli.timeoutMs,
    onState: (next) => {
      fs.writeFileSync(taskPath, JSON.stringify({ ...next, request }, null, 2) + "\n");
      console.log(`${taskId}: ${next.status}`);
    },
  });
  const downloadUrl = state.downloadUrl ?? (await client.retrieve(state.fileId!)).downloadUrl;
  const videoName = "scene.mp4";
  fs.writeFileSync(path.join(cli.out, videoName), await client.download(downloadUrl));
  const storyboard = createStoryboard(path.join(cli.out, videoName), cli.out, cli.maxPanels);
  const frames = storyboard.panels.map((panel) => panel.frame);
  fs.writeFileSync(path.join(cli.out, "manifest.edn"), buildManifestEdn({
    request, promptSource: source, taskId, fileId: state.fileId, video: videoName, frames,
  }));
  console.log(`done: ${path.join(cli.out, videoName)} (${frames.length} motion/transition panels)`);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
