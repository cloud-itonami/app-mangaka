export const CAMERA_COMMANDS = new Set([
  "Truck left", "Truck right", "Pan left", "Pan right", "Push in", "Pull out",
  "Pedestal up", "Pedestal down", "Tilt up", "Tilt down", "Zoom in", "Zoom out",
  "Shake", "Tracking shot", "Static shot",
]);

export interface MangaFaceControls {
  rig?: string;
  intensity?: number;
  eyes?: {
    open?: number;
    upperLid?: string;
    lowerLid?: string;
    brow?: string;
    pupil?: number;
    iris?: number;
    gaze?: string;
    highlight?: string;
    tear?: string;
    asymmetry?: number;
  };
  mouth?: string;
  dimensions?: Array<{ label: string; confidence: number }>;
}

export interface SceneCharacter {
  name: string;
  identity?: string;
  action?: string;
  face?: MangaFaceControls;
}

export interface ScenePromptInput {
  prompt: string;
  setting?: string;
  atmosphere?: string;
  characters?: SceneCharacter[];
  camera?: string[];
  continuity?: string[];
  negative?: string[];
  style?: string;
}

const clean = (value: string) => value.replace(/\s+/g, " ").trim();

function finite01(value: number | undefined): number | undefined {
  if (value === undefined || !Number.isFinite(value)) return undefined;
  return Math.max(0, Math.min(1, value));
}

function describeFace(face: MangaFaceControls): string {
  const eye = face.eyes ?? {};
  const details = [
    face.rig && `construction ${face.rig}`,
    finite01(face.intensity) !== undefined && `manga exaggeration ${finite01(face.intensity)!.toFixed(2)}`,
    finite01(eye.open) !== undefined && `eye openness ${finite01(eye.open)!.toFixed(2)}`,
    eye.upperLid && `upper lid ${eye.upperLid}`,
    eye.lowerLid && `lower lid ${eye.lowerLid}`,
    eye.brow && `brow ${eye.brow}`,
    eye.pupil !== undefined && Number.isFinite(eye.pupil) && `pupil ratio ${eye.pupil.toFixed(2)}`,
    eye.iris !== undefined && Number.isFinite(eye.iris) && `iris ratio ${eye.iris.toFixed(2)}`,
    eye.gaze && `gaze ${eye.gaze}`,
    eye.highlight && `highlight ${eye.highlight}`,
    eye.tear && `tear ${eye.tear}`,
    finite01(eye.asymmetry) !== undefined && `left-right asymmetry ${finite01(eye.asymmetry)!.toFixed(2)}`,
    face.mouth && `mouth ${face.mouth}`,
  ].filter(Boolean);
  return details.join(", ");
}

export function normalizeCamera(commands: string[] = []): string[] {
  if (commands.length > 3) throw new Error("MiniMax supports at most 3 combined camera commands");
  return commands.map((command) => {
    const normalized = clean(command.replace(/^\[|\]$/g, ""));
    if (!CAMERA_COMMANDS.has(normalized)) throw new Error(`Unsupported MiniMax camera command: ${command}`);
    return normalized;
  });
}

/**
 * Compile authored scene semantics into a motion-first prompt. Hume expression
 * confidence is retained as provenance only; anatomy and authored intensity
 * are the visual instructions so the model is never asked to infer an inner state.
 */
export function compileScenePrompt(input: ScenePromptInput): string {
  if (!clean(input.prompt)) throw new Error("scene prompt is required");
  const camera = normalizeCamera(input.camera);
  const sections: string[] = [
    "Create one continuous manga-cinematic scene with stable character identity and coherent physical motion.",
    `SCENE: ${clean(input.prompt)}`,
  ];
  if (input.setting) sections.push(`SETTING: ${clean(input.setting)}`);
  if (input.atmosphere) sections.push(`ATMOSPHERE: ${clean(input.atmosphere)}`);
  for (const character of input.characters ?? []) {
    const parts = [
      character.identity && clean(character.identity),
      character.action && `action: ${clean(character.action)}`,
      character.face && `face: ${describeFace(character.face)}`,
    ].filter(Boolean);
    sections.push(`CHARACTER ${clean(character.name)}: ${parts.join("; ")}`);
  }
  if (camera.length) sections.push(`[${camera.join(",")}]`);
  if (input.continuity?.length) sections.push(`CONTINUITY: ${input.continuity.map(clean).join("; ")}`);
  sections.push(`STYLE: ${clean(input.style ?? "monochrome Japanese manga, clean expressive line art, controlled screentone, cinematic lighting")}`);
  const negatives = input.negative ?? [
    "no subtitles", "no speech bubbles", "no written text", "no identity drift",
    "no extra fingers", "no duplicate characters", "no abrupt shot discontinuity",
  ];
  sections.push(`AVOID: ${negatives.map(clean).join("; ")}`);
  const prompt = sections.join("\n");
  if (prompt.length > 2000) throw new Error(`MiniMax prompt exceeds 2000 characters (${prompt.length})`);
  return prompt;
}
