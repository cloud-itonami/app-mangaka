import * as fs from "node:fs";
import * as path from "node:path";
import { spawnSync } from "node:child_process";

export const MODEL_ALIASES = {
  h3: "MiniMax-Hailuo-2.3",
  "hailuo-2.3": "MiniMax-Hailuo-2.3",
  "MiniMax-Hailuo-2.3": "MiniMax-Hailuo-2.3",
} as const;

export type MiniMaxModelAlias = keyof typeof MODEL_ALIASES;
export type MiniMaxStatus = "Preparing" | "Queueing" | "Processing" | "Success" | "Fail";

export interface VideoRequest {
  model: "MiniMax-Hailuo-2.3";
  prompt: string;
  duration: 6 | 10;
  resolution: "768P" | "1080P";
  prompt_optimizer: boolean;
  fast_pretreatment?: boolean;
  first_frame_image?: string;
}

export interface TaskState {
  taskId: string;
  status: MiniMaxStatus;
  fileId?: string;
  width?: number;
  height?: number;
  error?: string;
}

export interface MiniMaxTransport {
  create(request: VideoRequest): Promise<string>;
  query(taskId: string): Promise<TaskState>;
  retrieve(fileId: string): Promise<{ downloadUrl: string; bytes?: number }>;
  download(url: string): Promise<Uint8Array>;
}

function assertApiSuccess(body: any, operation: string): void {
  const code = body?.base_resp?.status_code;
  if (code !== undefined && code !== 0) {
    throw new Error(`${operation} failed: ${code} ${body?.base_resp?.status_msg ?? "unknown"}`);
  }
}

export class MiniMaxClient implements MiniMaxTransport {
  constructor(
    private readonly apiKey: string,
    private readonly baseUrl = "https://api.minimax.io",
  ) {
    if (!apiKey) throw new Error("MINIMAX_API_KEY is required");
  }

  private async json(url: string, init?: RequestInit): Promise<any> {
    const response = await fetch(url, {
      ...init,
      headers: {
        Authorization: `Bearer ${this.apiKey}`,
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
    });
    if (!response.ok) throw new Error(`MiniMax HTTP ${response.status}: ${await response.text()}`);
    return response.json();
  }

  async create(request: VideoRequest): Promise<string> {
    const body = await this.json(`${this.baseUrl}/v1/video_generation`, {
      method: "POST",
      body: JSON.stringify(request),
    });
    assertApiSuccess(body, "create video task");
    if (!body.task_id) throw new Error("MiniMax response is missing task_id");
    return String(body.task_id);
  }

  async query(taskId: string): Promise<TaskState> {
    const url = new URL(`${this.baseUrl}/v1/query/video_generation`);
    url.searchParams.set("task_id", taskId);
    const body = await this.json(url.toString());
    assertApiSuccess(body, "query video task");
    return {
      taskId,
      status: body.status as MiniMaxStatus,
      fileId: body.file_id ? String(body.file_id) : undefined,
      width: body.video_width,
      height: body.video_height,
      error: body.error_message,
    };
  }

  async retrieve(fileId: string): Promise<{ downloadUrl: string; bytes?: number }> {
    const url = new URL(`${this.baseUrl}/v1/files/retrieve`);
    url.searchParams.set("file_id", fileId);
    const body = await this.json(url.toString());
    assertApiSuccess(body, "retrieve video file");
    if (!body.file?.download_url) throw new Error("MiniMax response is missing download_url");
    return { downloadUrl: body.file.download_url, bytes: body.file.bytes };
  }

  async download(url: string): Promise<Uint8Array> {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`video download HTTP ${response.status}`);
    return new Uint8Array(await response.arrayBuffer());
  }
}

export function resolveModel(alias: string): "MiniMax-Hailuo-2.3" {
  const model = MODEL_ALIASES[alias as MiniMaxModelAlias];
  if (!model) throw new Error(`Unsupported video model alias: ${alias}`);
  return model;
}

export function validateVideoRequest(request: VideoRequest): void {
  if (!request.prompt || request.prompt.length > 2000) throw new Error("prompt must contain 1..2000 characters");
  if (request.duration === 10 && request.resolution !== "768P") {
    throw new Error("MiniMax-Hailuo-2.3 supports 10s only at 768P");
  }
  if (![6, 10].includes(request.duration)) throw new Error("duration must be 6 or 10 seconds");
  if (!["768P", "1080P"].includes(request.resolution)) throw new Error("resolution must be 768P or 1080P");
}

export async function waitForTask(
  transport: MiniMaxTransport,
  taskId: string,
  options: { intervalMs?: number; timeoutMs?: number; onState?: (state: TaskState) => void } = {},
): Promise<TaskState> {
  const intervalMs = options.intervalMs ?? 10_000;
  const timeoutMs = options.timeoutMs ?? 20 * 60_000;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() <= deadline) {
    const state = await transport.query(taskId);
    options.onState?.(state);
    if (state.status === "Success") {
      if (!state.fileId) throw new Error("successful MiniMax task is missing file_id");
      return state;
    }
    if (state.status === "Fail") throw new Error(state.error ?? `MiniMax task ${taskId} failed`);
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error(`MiniMax task ${taskId} timed out after ${timeoutMs}ms`);
}

export function extractTimelineFrames(videoPath: string, outDir: string, duration: number): string[] {
  const ffmpeg = spawnSync("ffmpeg", [
    "-hide_banner", "-loglevel", "error", "-y", "-i", videoPath,
    "-vf", "fps=20/7", path.join(outDir, "frame-%03d.png"),
  ], { encoding: "utf8" });
  if (ffmpeg.error || ffmpeg.status !== 0) {
    throw new Error(`ffmpeg frame extraction failed: ${ffmpeg.error?.message ?? ffmpeg.stderr}`);
  }
  const frames = fs.readdirSync(outDir).filter((name) => /^frame-\d+\.png$/.test(name)).sort();
  return frames.map((name, index) => {
    const time = Math.min(duration, index * 0.35).toFixed(2);
    const target = `t${time}.png`;
    fs.renameSync(path.join(outDir, name), path.join(outDir, target));
    return target;
  });
}

function ednString(value: string): string {
  return JSON.stringify(value);
}

export function buildManifestEdn(input: {
  request: VideoRequest;
  promptSource: string;
  taskId?: string;
  fileId?: string;
  video?: string;
  frames?: string[];
}): string {
  const frames = (input.frames ?? []).map(ednString).join(" ");
  return `{:schema "cloud.itonami.mangaka.scene-video.v1"\n` +
    ` :engine "minimax-h3"\n :model ${ednString(input.request.model)}\n` +
    ` :duration ${input.request.duration}\n :resolution ${ednString(input.request.resolution)}\n` +
    ` :prompt ${ednString(input.request.prompt)}\n :prompt-source ${ednString(input.promptSource)}\n` +
    (input.taskId ? ` :task-id ${ednString(input.taskId)}\n` : "") +
    (input.fileId ? ` :file-id ${ednString(input.fileId)}\n` : "") +
    (input.video ? ` :video ${ednString(input.video)}\n` : "") +
    ` :beats [{:beat/id "scene" :window [0.0 ${input.request.duration.toFixed(1)}] :frames [${frames}]}]}\n`;
}
