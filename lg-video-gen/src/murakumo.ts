export const MODEL_ALIASES = {
  h3: "minimax-h3",
  "minimax-h3": "minimax-h3",
} as const;

export type VideoModel = "minimax-h3";
export type MurakumoStatus = "queued" | "running" | "done" | "failed" | "cancelled";

export interface H3VideoRequest {
  type: "video";
  model: "minimax-h3";
  prompt: string;
  input: { prompt: string; image?: string };
  params: {
    duration_ms: number;
    width: number;
    height: number;
    frames: number;
    steps: number;
    seed?: number;
  };
  actor: string;
}

export type VideoRequest = H3VideoRequest;

export interface TaskState {
  taskId: string;
  status: MurakumoStatus;
  progress?: number;
  artifactUrl?: string;
  contentHash?: string;
  error?: string;
}

export interface MurakumoTransport {
  create(request: VideoRequest): Promise<string>;
  query(taskId: string): Promise<TaskState>;
  download(taskId: string): Promise<Uint8Array>;
}

function errorMessage(body: any): string | undefined {
  if (typeof body?.error === "string") return body.error;
  return body?.error?.message ?? body?.message;
}

export class MurakumoClient implements MurakumoTransport {
  constructor(
    private readonly token: string,
    private readonly baseUrl = "https://api.murakumo.cloud",
  ) {
    if (!token) throw new Error("MURAKUMO_GENERATION_TOKEN is required");
  }

  private endpoint(path: string): string {
    return `${this.baseUrl.replace(/\/$/, "")}${path}`;
  }

  private async json(path: string, init?: RequestInit): Promise<any> {
    const response = await fetch(this.endpoint(path), {
      ...init,
      headers: {
        Authorization: `Bearer ${this.token}`,
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
    });
    const text = await response.text();
    const body = text ? JSON.parse(text) : {};
    if (!response.ok) throw new Error(`Murakumo HTTP ${response.status}: ${errorMessage(body) ?? text}`);
    return body;
  }

  async create(request: VideoRequest): Promise<string> {
    const body = await this.json("/api/v1/generation", {
      method: "POST",
      body: JSON.stringify(request),
    });
    const taskId = body.jobId ?? body.job_id ?? body.id;
    if (!taskId) throw new Error("Murakumo response is missing jobId");
    return String(taskId);
  }

  async query(taskId: string): Promise<TaskState> {
    const body = await this.json(`/api/v1/generation/jobs/${encodeURIComponent(taskId)}`);
    const artifact = body.artifacts?.[0];
    return {
      taskId: String(body.jobId ?? body.job_id ?? body.id ?? taskId),
      status: body.status as MurakumoStatus,
      progress: body.progress,
      artifactUrl: artifact?.url,
      contentHash: artifact?.contentHash ?? artifact?.content_hash,
      error: errorMessage(body),
    };
  }

  async download(taskId: string): Promise<Uint8Array> {
    const response = await fetch(this.endpoint(`/api/v1/generation/jobs/${encodeURIComponent(taskId)}/artifact`), {
      headers: { Authorization: `Bearer ${this.token}` },
    });
    if (!response.ok) throw new Error(`Murakumo artifact HTTP ${response.status}: ${await response.text()}`);
    return new Uint8Array(await response.arrayBuffer());
  }
}

export function resolveModel(alias: string): VideoModel {
  const model = MODEL_ALIASES[alias as keyof typeof MODEL_ALIASES];
  if (!model) throw new Error(`Unsupported video model alias: ${alias}`);
  return model;
}

export function snapH3Frames(durationSeconds: number): number {
  const requested = Math.ceil(durationSeconds * 24);
  const snapped = Math.ceil((requested - 5) / 17) * 17 + 5;
  return Math.max(22, snapped);
}

export function validateVideoRequest(request: VideoRequest): void {
  if (!request.prompt.trim() || request.input.prompt !== request.prompt) {
    throw new Error("prompt must not be empty and input.prompt must match it");
  }
  if (request.model !== "minimax-h3" || request.type !== "video") throw new Error("Murakumo H3 request must use video/minimax-h3");
  for (const [name, value] of [["width", request.params.width], ["height", request.params.height]] as const) {
    if (!Number.isInteger(value) || value < 256 || value > 1280 || value % 32 !== 0) {
      throw new Error(`${name} must be a multiple of 32 from 256 through 1280`);
    }
  }
  if (request.params.frames < 22 || request.params.frames > 175 || (request.params.frames - 5) % 17 !== 0) {
    throw new Error("H3 frames must be 17k+5 from 22 through 175");
  }
  if (!Number.isInteger(request.params.steps) || request.params.steps < 1 || request.params.steps > 50) {
    throw new Error("H3 steps must be an integer from 1 through 50");
  }
}

export async function waitForTask(
  transport: MurakumoTransport,
  taskId: string,
  options: { intervalMs?: number; timeoutMs?: number; onState?: (state: TaskState) => void } = {},
): Promise<TaskState> {
  const intervalMs = options.intervalMs ?? 15_000;
  const timeoutMs = options.timeoutMs ?? 4 * 60 * 60_000;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() <= deadline) {
    const state = await transport.query(taskId);
    options.onState?.(state);
    if (state.status === "done") return state;
    if (["failed", "cancelled"].includes(state.status)) throw new Error(state.error ?? `Murakumo task ${taskId} ${state.status}`);
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error(`Murakumo task ${taskId} timed out after ${timeoutMs}ms`);
}

function ednString(value: string): string {
  return JSON.stringify(value);
}

export function buildManifestEdn(input: {
  request: VideoRequest;
  promptSource: string;
  taskId?: string;
  video?: string;
  frames?: string[];
}): string {
  const frames = (input.frames ?? []).map(ednString).join(" ");
  const duration = input.request.params.duration_ms / 1000;
  return `{:schema "cloud.itonami.mangaka.scene-video.v1"\n` +
    ` :provider "murakumo"\n :engine "murakumo-h3"\n :model ${ednString(input.request.model)}\n` +
    ` :duration ${duration}\n :geometry [${input.request.params.width} ${input.request.params.height}]\n` +
    ` :generation-frames ${input.request.params.frames}\n :generation-steps ${input.request.params.steps}\n` +
    ` :prompt ${ednString(input.request.prompt)}\n :prompt-source ${ednString(input.promptSource)}\n` +
    (input.taskId ? ` :task-id ${ednString(input.taskId)}\n` : "") +
    (input.video ? ` :video ${ednString(input.video)}\n` : "") +
    ` :frame-selection "motion-peaks-and-scene-transitions"\n` +
    ` :storyboard "storyboard.json"\n` +
    ` :beats [{:beat/id "scene" :window [0.0 ${duration.toFixed(1)}] :frames [${frames}]}]}\n`;
}
