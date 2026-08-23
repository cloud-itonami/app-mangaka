#!/usr/bin/env node
import * as fs from "node:fs";
import * as path from "node:path";
import { compileScenePrompt, type ScenePromptInput } from "./prompt.js";
import {
  MiniMaxClient, buildManifestEdn, extractTimelineFrames, resolveModel,
  validateVideoRequest, waitForTask, type VideoRequest,
} from "./minimax.js";

interface Cli {
  prompt?: string;
  scene?: string;
  out: string;
  model: string;
  duration: 6 | 10;
  resolution: "768P" | "1080P";
  firstFrame?: string;
  optimize: boolean;
  dryRun: boolean;
  resume?: string;
  pollMs: number;
  timeoutMs: number;
}

function parseArgs(argv: string[]): Cli {
  const cli: Cli = {
    out: "out/minimax-h3-scene", model: "h3", duration: 6, resolution: "768P",
    optimize: false, dryRun: false, pollMs: 10_000, timeoutMs: 20 * 60_000,
  };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--prompt") cli.prompt = argv[++i];
    else if (arg === "--scene") cli.scene = argv[++i];
    else if (arg === "--out") cli.out = argv[++i];
    else if (arg === "--model") cli.model = argv[++i];
    else if (arg === "--duration") cli.duration = Number(argv[++i]) as 6 | 10;
    else if (arg === "--resolution") cli.resolution = argv[++i] as "768P" | "1080P";
    else if (arg === "--first-frame") cli.firstFrame = argv[++i];
    else if (arg === "--optimize") cli.optimize = true;
    else if (arg === "--dry-run") cli.dryRun = true;
    else if (arg === "--resume") cli.resume = argv[++i];
    else if (arg === "--poll-ms") cli.pollMs = Number(argv[++i]);
    else if (arg === "--timeout-ms") cli.timeoutMs = Number(argv[++i]);
    else throw new Error(`unknown argument: ${arg}`);
  }
  return cli;
}

function firstFrameValue(value: string | undefined): string | undefined {
  if (!value || /^https?:|^data:image\//.test(value)) return value;
  const absolute = path.resolve(value);
  const extension = path.extname(absolute).slice(1).toLowerCase().replace("jpg", "jpeg");
  if (!["jpeg", "png", "webp"].includes(extension)) throw new Error("first frame must be JPG, PNG, or WebP");
  const bytes = fs.readFileSync(absolute);
  if (bytes.length >= 20 * 1024 * 1024) throw new Error("first frame must be smaller than 20MB");
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

async function main(): Promise<void> {
  const cli = parseArgs(process.argv.slice(2));
  fs.mkdirSync(cli.out, { recursive: true });
  const { input, source } = loadScene(cli);
  const request: VideoRequest = {
    model: resolveModel(cli.model),
    prompt: compileScenePrompt(input),
    duration: cli.duration,
    resolution: cli.resolution,
    prompt_optimizer: cli.optimize,
    ...(cli.optimize ? { fast_pretreatment: true } : {}),
    ...(cli.firstFrame ? { first_frame_image: firstFrameValue(cli.firstFrame) } : {}),
  };
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
    intervalMs: cli.pollMs,
    timeoutMs: cli.timeoutMs,
    onState: (next) => {
      fs.writeFileSync(taskPath, JSON.stringify({ ...next, request }, null, 2) + "\n");
      console.log(`${taskId}: ${next.status}`);
    },
  });
  const file = await client.retrieve(state.fileId!);
  const videoName = "scene.mp4";
  fs.writeFileSync(path.join(cli.out, videoName), await client.download(file.downloadUrl));
  const frames = extractTimelineFrames(path.join(cli.out, videoName), cli.out, request.duration);
  fs.writeFileSync(path.join(cli.out, "manifest.edn"), buildManifestEdn({
    request, promptSource: source, taskId, fileId: state.fileId, video: videoName, frames,
  }));
  console.log(`done: ${path.join(cli.out, videoName)} (${frames.length} timeline frames)`);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
