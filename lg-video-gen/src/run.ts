#!/usr/bin/env node
import * as fs from "node:fs";
import * as path from "node:path";
import { compileScenePrompt, type ScenePromptInput } from "./prompt.js";
import {
  MurakumoClient, buildManifestEdn, resolveModel, snapH3Frames, validateVideoRequest, waitForTask,
  type H3VideoRequest,
} from "./murakumo.js";
import { createStoryboard } from "./storyboard.js";

interface Cli {
  prompt?: string;
  scene?: string;
  out: string;
  model: string;
  duration: number;
  width: number;
  height: number;
  frames?: number;
  steps: number;
  seed?: number;
  actor: string;
  firstFrame?: string;
  dryRun: boolean;
  resume?: string;
  pollMs: number;
  timeoutMs: number;
  maxPanels: number;
}

function parseArgs(argv: string[]): Cli {
  const cli: Cli = {
    out: "out/murakumo-h3-scene", model: "h3", duration: 5, width: 640, height: 640,
    steps: 20, actor: "mangaka:lg-video-gen", dryRun: false,
    pollMs: 15_000, timeoutMs: 4 * 60 * 60_000, maxPanels: 8,
  };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--prompt") cli.prompt = argv[++i];
    else if (arg === "--scene") cli.scene = argv[++i];
    else if (arg === "--out") cli.out = argv[++i];
    else if (arg === "--model") cli.model = argv[++i];
    else if (arg === "--duration") cli.duration = Number(argv[++i]);
    else if (arg === "--width") cli.width = Number(argv[++i]);
    else if (arg === "--height") cli.height = Number(argv[++i]);
    else if (arg === "--frames") cli.frames = Number(argv[++i]);
    else if (arg === "--steps") cli.steps = Number(argv[++i]);
    else if (arg === "--seed") cli.seed = Number(argv[++i]);
    else if (arg === "--actor") cli.actor = argv[++i];
    else if (arg === "--first-frame") cli.firstFrame = argv[++i];
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
  if (!value) return undefined;
  if (/^https?:\/\//.test(value)) return value;
  throw new Error("Murakumo H3 reference images must be reachable http(s) URLs");
}

function loadScene(cli: Cli): { input: ScenePromptInput; source: string } {
  if (cli.scene) {
    const source = path.resolve(cli.scene);
    return { input: JSON.parse(fs.readFileSync(source, "utf8")), source };
  }
  if (cli.prompt) return { input: { prompt: cli.prompt }, source: "cli:--prompt" };
  throw new Error("provide --prompt <text> or --scene <scene.json>");
}

function makeRequest(cli: Cli, prompt: string): H3VideoRequest {
  const model = resolveModel(cli.model);
  const frame = imageValue(cli.firstFrame);
  return {
    type: "video",
    model,
    prompt,
    input: { prompt, ...(frame ? { image: frame } : {}) },
    params: {
      duration_ms: Math.round(cli.duration * 1000),
      width: cli.width,
      height: cli.height,
      frames: cli.frames ?? snapH3Frames(cli.duration),
      steps: cli.steps,
      ...(cli.seed === undefined ? {} : { seed: cli.seed }),
    },
    actor: cli.actor,
  };
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
    console.log(`DRY RUN: Murakumo ${request.model} ${request.params.frames}f ${request.params.width}x${request.params.height}`);
    console.log(`request: ${requestPath}`);
    return;
  }

  const client = new MurakumoClient(
    process.env.MURAKUMO_GENERATION_TOKEN ?? "",
    process.env.MURAKUMO_GENERATION_URL ?? "https://api.murakumo.cloud",
  );
  const taskId = cli.resume ?? await client.create(request);
  const taskPath = path.join(cli.out, "task.json");
  fs.writeFileSync(taskPath, JSON.stringify({ taskId, status: "submitted", request }, null, 2) + "\n");
  console.log(`${cli.resume ? "Resuming" : "Submitted"} Murakumo task ${taskId}`);

  const state = await waitForTask(client, taskId, {
    intervalMs: cli.pollMs,
    timeoutMs: cli.timeoutMs,
    onState: (next) => {
      fs.writeFileSync(taskPath, JSON.stringify({ ...next, request }, null, 2) + "\n");
      console.log(`${taskId}: ${next.status}`);
    },
  });
  const videoName = "scene.mp4";
  fs.writeFileSync(path.join(cli.out, videoName), await client.download(taskId));
  const storyboard = createStoryboard(path.join(cli.out, videoName), cli.out, cli.maxPanels);
  const frames = storyboard.panels.map((panel) => panel.frame);
  fs.writeFileSync(path.join(cli.out, "manifest.edn"), buildManifestEdn({
    request, promptSource: source, taskId, video: videoName, frames,
  }));
  console.log(`done: ${path.join(cli.out, videoName)} (${frames.length} motion/transition panels)`);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
