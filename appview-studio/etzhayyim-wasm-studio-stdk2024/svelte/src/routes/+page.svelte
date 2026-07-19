<script lang="ts">
  import { onMount } from "svelte";
  import mermaid from "mermaid";
  import NodeGraph from "$lib/NodeGraph.svelte";

  type Assistant = {
    assistant_id: string;
    graph_id?: string;
    name?: string;
    description?: string;
  };

  type GraphNode = { id: string; type?: string };
  type GraphEdge = { source: string; target: string; conditional?: boolean };
  type GraphData = { nodes: GraphNode[]; edges: GraphEdge[] };

  type StageStatus = "pending" | "running" | "done" | "error";
  type StageCard = {
    node: string;
    status: StageStatus;
    invocations: number;        // Send fan-out count
    delta: unknown;             // last seen state delta
    startedAt?: number;
    completedAt?: number;
    error?: string;
  };

  let assistants = $state<Assistant[]>([]);
  let selectedAid = $state<string>("");   // assistant_id (UUID) — used for API calls
  let selectedGid = $state<string>("");   // graph_id (human name) — used for DEFAULT_INPUTS + display
  let graphData = $state<GraphData | null>(null);
  let mermaidSvg = $state<string>("");
  let inputJson = $state<string>("");
  let inputError = $state<string>("");
  let running = $state<boolean>(false);
  let rawLog = $state<string[]>([]);
  let showRaw = $state<boolean>(false);
  let stages = $state<Record<string, StageCard>>({});
  let stageOrder = $state<string[]>([]);
  let runMeta = $state<{ threadId?: string; runId?: string; startedAt?: number; finishedAt?: number }>({});
  let userEmail = $state<string>("");
  let dagMode = $state<"nodes" | "mermaid" | "comfyui">("nodes");
  // Default LAN ComfyUI; override via ?comfy= query string if you need to.
  const comfyUrl = (() => {
    if (typeof window === "undefined") return "http://192.168.1.70:8188";
    const q = new URLSearchParams(window.location.search).get("comfy");
    return q || "http://192.168.1.70:8188";
  })();

  // Per-graph default input that exercises the cine pipeline meaningfully.
  const DEFAULT_INPUTS: Record<string, object> = {
    cine_generate_scene: {
      subject_kind: "mangaka.panel",
      subject_ref: "at://did:web:mangaka.etzhayyim.com/com.etzhayyim.mangaka.panel/demo-001",
      prompt: "rooftop chase at dusk, neon reflections in puddles",
      style: "shonen-jump-inked",
      world_kind: "threeD",
      frame_start: 0,
      frame_end: 0,
      fps: 24,
      geom_regions: 4,
      dry_run: true,
    },
    cine_generate_panel: {
      pipeline_run_id: "<paste from cine_generate_scene output>",
      page_rkey: "demo-page-001",
      panels: [
        { panel_rkey: "p01", framing: "FullShot", charactersAppearing: ["hero"] },
        { panel_rkey: "p02", framing: "Closeup", charactersAppearing: ["hero", "rival"] },
      ],
      samples_per_pixel: 4,
      sampler_steps: 20,
      cfg_scale_x10: 75,
      denoise_permille: 350,
      dry_run: true,
    },
    cine_generate_video: {
      prompt: "samurai cyborg under cherry blossoms, dramatic wind, ink wash",
      frame_count: 6,
      fps: 6,
      size: "640x960",
      sampler_steps: 10,
      cfg_scale_x10: 70,
      style: "shonen-jump-inked",
      dry_run: true,
    },
    // com.etzhayyim.mangaka.character — design sheet (batch of N views)
    mangaka_generate_character: {
      name: "yuki",
      description: "young female cyberpunk hacker, asymmetric haircut, leather jacket, glowing visor",
      style: "anime, manga, ink, screentone",
      pose_hint: "character reference sheet, multiple views, neutral and dynamic expressions",
      batch: 2,
      seed: 0,
      steps: 22,
      cfg: 7.0,
    },
    // com.etzhayyim.mangaka.environment — establishing-shot scene
    mangaka_generate_scene: {
      name: "neon-alley",
      description: "narrow Tokyo back-alley, dense neon signs, wet pavement reflecting magenta and cyan light",
      style: "anime, manga, ink, screentone, detailed",
      time_of_day: "night",
      weather: "after rain",
      seed: 0,
      steps: 22,
      cfg: 7.0,
    },
    // LoRA training — wraps kohya-ss via Lora-Training-in-Comfy.
    // Prereq: prepare training data folder on the ComfyUI host:
    //   C:\Users\gad\lora-data\10_yuki_persona\img_001.png + img_001.txt + ...
    // (kohya layout: <data_path>/<num>_<trigger>/*.png+*.txt)
    // 20-40 min per 8-12 image, 10-epoch character LoRA on AMD ROCm.
    mangaka_train_character_lora: {
      output_name: "yuki_persona",
      data_path: "C:\\Users\\gad\\lora-data",
      ckpt_name: "illustriousXL_v01.safetensors",
      batch_size: 1,
      max_train_epochs: 10,
      save_every_n_epochs: 10,
      clip_skip: 2,
      output_dir: "models/loras",
    },
    // Wan 2.2 TI2V 5B video — image+text -> animated WebP. ~6-10 min on
    // AMD Radeon 8060S ROCm 7.2 for 33-frame 832x1216 clip @ 16 fps.
    mangaka_generate_video_wan: {
      video_rkey: "wan-yuki-walk-01",
      start_image_b64: "",        // empty = pure text-to-video (slower but no ref)
      start_image_mime: "image/png",
      prompt: "a cyberpunk hacker in a leather jacket and glowing visor walks slowly down a neon-soaked Tokyo back-alley, magenta and cyan light reflecting on wet pavement, atmospheric, cinematic, anime style",
      negative_prompt: "blurry, low quality, watermark, distorted, oversaturated, deformed",
      width: 832,
      height: 1216,
      length: 33,                  // 33 frames @ 16 fps = ~2 sec
      fps: 16,
      steps: 20,
      cfg: 5.0,
      shift: 8.0,
      seed: 0
    },
    // Flux + PuLID page composite — character identity preserved across
    // every panel via a shared reference image. ~14 min for 4 panels.
    mangaka_generate_page_flux_pulid: {
      page_rkey: "flux-pulid-page-001",
      page_width: 1280,
      page_height: 1817,
      gutter: 14,
      border: 2,
      reference_image_b64: "<base64 PNG of character ref>",
      reference_image_mime: "image/png",
      seed_base: 42,
      width: 1024,
      height: 1536,
      steps: 22,
      guidance: 3.5,
      pulid_weight: 0.7,
      panels: [
        { panel_rkey: "p1", x: 60,  y: 60,   w: 1160, h: 540,
          framing: "wide", characters: ["cyberpunk hacker yuki"],
          environment: "neon-soaked Tokyo back-alley at night, dense kanji signage",
          mood: "establishing", action: "walking forward into the alley" },
        { panel_rkey: "p2", x: 60,  y: 620,  w: 560,  h: 560,
          framing: "medium", characters: ["yuki"],
          environment: "same back-alley", mood: "tense",
          action: "looking back over the shoulder" },
        { panel_rkey: "p3", x: 660, y: 620,  w: 560,  h: 560,
          framing: "closeup", characters: ["yuki"],
          environment: "magenta neon close behind", mood: "focused",
          action: "hand slips into jacket pocket, fingertips glow" },
        { panel_rkey: "p4", x: 60,  y: 1200, w: 1160, h: 557,
          framing: "low-angle", characters: ["yuki"],
          environment: "same back-alley looking up", mood: "dramatic",
          action: "raises a glowing cyberdeck, sparks scattering" },
      ],
    },
    // Flux + PuLID panel — best of both worlds: Flux's clean line work
    // plus PuLID face identity from a reference image. Requires
    // ComfyUI_PuLID_Flux_ll custom node + facenet-pytorch + pulid_flux_v0.9.1.
    mangaka_generate_panel_flux_pulid: {
      panel_rkey: "flux-pulid-p001",
      reference_image_b64: "<base64 PNG of character ref (close-up face works best)>",
      reference_image_mime: "image/png",
      framing: "medium",
      characters: ["cyberpunk hacker yuki"],
      environment: "neon-soaked Tokyo back-alley at night",
      mood: "tense, cinematic",
      action: "looking back over shoulder",
      width: 1024,
      height: 1536,
      steps: 22,
      guidance: 3.5,
      pulid_weight: 0.7,        // 0.5 = subtle, 0.9 = strong
      pulid_start_at: 0.0,
      pulid_end_at: 1.0,
      seed: 0,
    },
    // Flux page composite — runs panel_flux per panel + PIL paste. Slow
    // (~3 min/panel on AMD ROCm, so 4 panels = ~12 min) but production-tier.
    mangaka_generate_page_flux: {
      page_rkey: "flux-page-001",
      page_width: 1280,
      page_height: 1817,
      gutter: 14,
      border: 2,
      seed_base: 42,
      width: 1024,
      height: 1536,
      steps: 22,
      guidance: 3.5,
      panels: [
        { panel_rkey: "p1", x: 60,  y: 60,   w: 1160, h: 540,
          framing: "wide", characters: ["cyberpunk hacker yuki, asymmetric haircut, leather jacket, glowing visor"],
          environment: "narrow neon-soaked Tokyo back-alley at night, dense kanji signage",
          mood: "establishing, atmospheric",
          action: "walking forward into the alley" },
        { panel_rkey: "p2", x: 60,  y: 620,  w: 560,  h: 560,
          framing: "medium", characters: ["yuki"],
          environment: "same back-alley",
          mood: "tense",
          action: "looking back over the shoulder" },
        { panel_rkey: "p3", x: 660, y: 620,  w: 560,  h: 560,
          framing: "closeup", characters: ["yuki"],
          environment: "same back-alley, magenta neon close behind",
          mood: "focused",
          action: "hand slips into jacket pocket, fingertips glow" },
        { panel_rkey: "p4", x: 60,  y: 1200, w: 1160, h: 557,
          framing: "low-angle", characters: ["yuki"],
          environment: "same back-alley, looking up",
          mood: "dramatic",
          action: "raises a glowing cyberdeck, sparks scattering" },
      ],
    },
    // Flux.1 [dev] GGUF Q4 panel — text-to-image only, dramatic quality jump
    // vs SDXL. Slower (~3 min for 1024x1536 on AMD ROCm) but cleaner. Long
    // natural-language prompts via T5 text encoder.
    mangaka_generate_panel_flux: {
      panel_rkey: "flux-p001",
      framing: "medium",
      characters: ["lone shrine maiden, traditional robes"],
      environment: "ancient stone steps in misty cedar forest at dawn",
      mood: "serene, atmospheric, mysterious",
      action: "walking up the steps, slight backward glance",
      width: 1024,
      height: 1536,
      steps: 22,
      guidance: 3.5,
      seed: 0,
    },
    // HQ panel — IPAdapter FaceID + ControlNet Union + Illustrious base.
    // Requires the tier-1+2 packs from install-comfy-quality-pack.ps1.
    mangaka_generate_panel_hq: {
      panel_rkey: "p001",
      reference_image_b64: "<base64 PNG of character ref>",
      reference_image_mime: "image/png",
      framing: "medium",
      characters: ["yuki"],
      environment: "neon Tokyo back-alley",
      mood: "tense, cinematic",
      action: "hand on jacket pocket, looking back over shoulder",
      width: 1024,
      height: 1536,
      steps: 32,
      cfg: 7.5,
      ipadapter_face_weight: 0.85,
      ipadapter_weight: 0.75,
      controlnet_strength: 0.55,
      seed: 0,
    },
    // HQ character 3D via Hunyuan3D-2 (texture + PBR).
    // First run pulls ~6 GB Hy3D weights.
    mangaka_generate_character_hy3d: {
      name: "yuki",
      reference_image_b64: "<base64 PNG of character ref (bg-removed preferred)>",
      reference_image_mime: "image/png",
      bake_size: 1024,
      mesh_simplify: 50000,
    },
    // Character → 3D mesh (TripoSR → GLB). Paste a base64 PNG of a clean
    // single-view crop of the character (e.g. one of the design-sheet rows).
    mangaka_generate_character_3d: {
      name: "yuki",
      reference_image_b64: "<base64 PNG of character ref (without data: prefix)>",
      reference_image_mime: "image/png",
      geometry_resolution: 256,
      threshold: 0.0,
    },
    // Character-stable panel via img2img — silhouette + palette + style
    // carry through from `reference_image_b64`, prompt drives framing/action.
    mangaka_generate_panel_stable: {
      panel_rkey: "p001",
      reference_image_b64: "<base64 PNG of character ref>",
      reference_image_mime: "image/png",
      framing: "medium",
      characters: ["yuki"],
      environment: "neon alley",
      mood: "tense, cinematic",
      action: "hand on jacket pocket, looking back",
      base_denoise: 0.65,
      refine_denoise: 0.4,
      seed: 0,
    },
    // Whole-page composite — runs panel_stable_workflow for every panel
    // (with the same character ref so the character stays consistent), then
    // PIL-composites them onto a manga page canvas using the per-panel bbox.
    mangaka_generate_page: {
      page_rkey: "demo-page-001",
      page_width: 1280,
      page_height: 1817,
      gutter: 14,
      border: 2,
      reference_image_b64: "<base64 PNG of character ref>",
      reference_image_mime: "image/png",
      seed_base: 42,
      panels: [
        { panel_rkey: "p1", x: 60,  y: 60,   w: 1160, h: 540,
          framing: "wide", characters: ["yuki"], environment: "neon alley",
          mood: "establishing", action: "walking forward into the alley" },
        { panel_rkey: "p2", x: 60,  y: 620,  w: 560,  h: 560,
          framing: "medium", characters: ["yuki"], environment: "neon alley",
          mood: "tense", action: "looking back over shoulder" },
        { panel_rkey: "p3", x: 660, y: 620,  w: 560,  h: 560,
          framing: "closeup", characters: ["yuki"], environment: "neon alley",
          mood: "focused", action: "hand reaches into jacket pocket" },
        { panel_rkey: "p4", x: 60,  y: 1200, w: 1160, h: 557,
          framing: "low-angle", characters: ["yuki"], environment: "neon alley",
          mood: "dramatic", action: "pulls out a cyberdeck, sparks fly" },
      ],
    },
    // com.etzhayyim.mangaka.panel — per-panel 2-pass (composition → inked refine)
    mangaka_generate_panel: {
      panel_rkey: "p001",
      framing: "medium",       // wide|medium|closeup|low-angle|high-angle|ots
      characters: ["yuki"],
      environment: "neon alley",
      mood: "tense, cinematic",
      action: "hand on jacket pocket, looking back over shoulder",
      base_steps: 20,
      refine_steps: 14,
      base_cfg: 7.0,
      refine_cfg: 6.5,
      refine_denoise: 0.4,
      seed: 0,
    },
    // mangaka cine pipeline as a native ComfyUI workflow — 2-pass
    // (composition → inked refine, denoise=0.4). Same JSON the
    // `scripts/install-mangaka-comfy-workflow.py` script ships to
    // ComfyUI's workflows library, in API format so /prompt accepts it.
    // Edit the CLIPTextEncode text fields (3, 4, 9, 10) to taste.
    comfy_run: {
      workflow: {
        "1":  { "class_type": "CheckpointLoaderSimple", "inputs": { "ckpt_name": "animagine-xl-4.0.safetensors" } },
        "2":  { "class_type": "EmptyLatentImage",       "inputs": { "width": 832, "height": 1216, "batch_size": 1 } },
        "3":  { "class_type": "CLIPTextEncode",         "inputs": {
                  "text": "establishing shot composition, dramatic chiaroscuro lighting, dynamic perspective, cinematic framing, lone figure silhouette",
                  "clip": ["1", 1] } },
        "4":  { "class_type": "CLIPTextEncode",         "inputs": {
                  "text": "blurry, low quality, color photograph, soft focus, deformed",
                  "clip": ["1", 1] } },
        "5":  { "class_type": "KSampler", "inputs": {
                  "seed": 0, "steps": 20, "cfg": 7.0, "sampler_name": "euler",
                  "scheduler": "normal", "denoise": 1.0,
                  "model": ["1", 0], "positive": ["3", 0], "negative": ["4", 0],
                  "latent_image": ["2", 0] } },
        "6":  { "class_type": "VAEDecode", "inputs": { "samples": ["5", 0], "vae": ["1", 2] } },
        "7":  { "class_type": "SaveImage", "inputs": { "filename_prefix": "mangaka-composition", "images": ["6", 0] } },
        "9":  { "class_type": "CLIPTextEncode", "inputs": {
                  "text": "manga inked panel, sharp black ink lines, screentone, hatching, high contrast monochrome, dynamic composition, shonen-jump style",
                  "clip": ["1", 1] } },
        "10": { "class_type": "CLIPTextEncode", "inputs": {
                  "text": "color photograph, blurry, low quality, watermark, soft focus, gradient",
                  "clip": ["1", 1] } },
        "11": { "class_type": "KSampler", "inputs": {
                  "seed": 0, "steps": 14, "cfg": 6.5, "sampler_name": "dpmpp_2m",
                  "scheduler": "karras", "denoise": 0.4,
                  "model": ["1", 0], "positive": ["9", 0], "negative": ["10", 0],
                  "latent_image": ["5", 0] } },
        "12": { "class_type": "VAEDecode", "inputs": { "samples": ["11", 0], "vae": ["1", 2] } },
        "13": { "class_type": "SaveImage", "inputs": { "filename_prefix": "mangaka-inked", "images": ["12", 0] } }
      },
      timeout_seconds: 300,
      poll_interval_ms: 1500,
    },
  };

  // Friendly labels for known cine stages.
  const STAGE_LABEL: Record<string, string> = {
    s1_world_model:     "1 · worldModel + ComfyUI preview",
    s2_usd_scene:       "2 · usdScene",
    s3_partition_geom:  "3a · partition",
    s3_neural_geom_one: "3 · neuralGeom (Send ×N)",
    s3_merge_geom:      "3b · merge",
    s4_temporal_field:  "4 · temporalField",
    load_scene:         "5a · loadScene",
    plan_panels:        "5b · planPanels",
    per_panel_render:   "5+6 · neuralRender + diffusionPass (Send ×N)",
    aggregate:          "agg · aggregate",
    finalize:           "fin · finalize",
    // cine_generate_video stages
    expand:             "1 · expand (per-frame plan)",
    render_frame:       "2 · render_frame (ComfyUI Send ×N)",
    encode:             "3 · encode (ffmpeg → mp4)",
    // comfy_run + mangaka_generate_* stages
    upload:             "0 · upload reference image → /upload/image",
    build:              "1 · build ComfyUI workflow JSON",
    submit:             "2 · submit workflow → /prompt",
    poll:               "3 · poll /history + /view (inline images)",
    plan:               "1 · validate page + upload ref",
    render:             "2 · render every panel (sequential)",
    composite:          "3 · PIL composite onto page canvas",
  };

  onMount(async () => {
    mermaid.initialize({ startOnLoad: false, theme: "dark", securityLevel: "loose" });
    await Promise.all([loadAssistants(), loadMeta()]);
  });

  async function loadMeta() {
    try {
      const r = await fetch("/_app/meta");
      if (r.ok) userEmail = (await r.json()).userEmail ?? "";
    } catch {}
  }

  async function loadAssistants() {
    try {
      const r = await fetch("/api/assistants/search", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ limit: 100 }),
      });
      if (!r.ok) {
        rawLog = [`loadAssistants HTTP ${r.status} — backend (langgraph dev :2024) up?`];
        return;
      }
      assistants = await r.json();
      if (!selectedAid && assistants.length) {
        // Prefer a cine graph; otherwise first.
        const cine = assistants.find((a) => (a.graph_id ?? a.name)?.startsWith("cine_"));
        const pick = cine ?? assistants[0];
        await selectGraph(pick.assistant_id, pick.graph_id ?? pick.name ?? "");
      }
    } catch (e) {
      rawLog = [`loadAssistants threw: ${String(e)}`];
    }
  }

  async function selectGraph(aid: string, gid: string) {
    selectedAid = aid;
    selectedGid = gid;
    graphData = null;
    mermaidSvg = "";
    inputJson = JSON.stringify(DEFAULT_INPUTS[gid] ?? { dry_run: true }, null, 2);
    stages = {};
    stageOrder = [];
    runMeta = {};

    try {
      const r = await fetch(`/api/assistants/${encodeURIComponent(aid)}/graph`);
      if (!r.ok) {
        mermaidSvg = `<pre style="color:#f55">graph fetch failed: HTTP ${r.status}</pre>`;
        return;
      }
      graphData = await r.json();
      // Pre-populate stage cards (pending) so the layout is visible before Run.
      const seen = new Set<string>();
      for (const n of graphData!.nodes) {
        if (n.id === "__start__" || n.id === "__end__") continue;
        if (seen.has(n.id)) continue;
        seen.add(n.id);
        stages[n.id] = { node: n.id, status: "pending", invocations: 0, delta: null };
        stageOrder.push(n.id);
      }
      const def = toMermaid(graphData!);
      const { svg } = await mermaid.render(`g-${aid.replace(/[^a-z0-9]/gi, "_")}`, def);
      mermaidSvg = svg;
    } catch (e) {
      mermaidSvg = `<pre>${String(e)}</pre>`;
    }
  }

  function toMermaid(g: GraphData): string {
    const lines = ["flowchart TD"];
    const seen = new Set<string>();
    g.nodes.forEach((n) => {
      const id = sanitize(n.id);
      if (seen.has(id)) return;
      seen.add(id);
      lines.push(`  ${id}(["${n.id}"])`);
    });
    g.edges.forEach((e) => {
      const arrow = e.conditional ? "-.->" : "-->";
      lines.push(`  ${sanitize(e.source)} ${arrow} ${sanitize(e.target)}`);
    });
    return lines.join("\n");
  }

  function sanitize(s: string): string { return s.replace(/[^a-zA-Z0-9_]/g, "_"); }

  async function run() {
    inputError = "";
    let input: unknown;
    try { input = JSON.parse(inputJson); }
    catch (e) { inputError = String(e); return; }

    running = true;
    rawLog = [];
    runMeta = { startedAt: Date.now() };
    // reset per-stage state (keep pending placeholders from DAG)
    for (const k of stageOrder) {
      stages[k] = { ...stages[k], status: "pending", invocations: 0, delta: null, startedAt: undefined, completedAt: undefined, error: undefined };
    }

    let thread: { thread_id: string };
    try {
      const tr = await fetch("/api/threads", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!tr.ok) throw new Error(`thread create HTTP ${tr.status}`);
      thread = await tr.json();
    } catch (e) {
      rawLog = [String(e)]; running = false; return;
    }
    runMeta.threadId = thread.thread_id;

    const sr = await fetch(`/api/threads/${thread.thread_id}/runs/stream`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        assistant_id: selectedAid,
        input,
        stream_mode: ["updates", "values"],
      }),
    });
    if (!sr.ok || !sr.body) {
      rawLog = [`run stream HTTP ${sr.status}`]; running = false; return;
    }

    const reader = sr.body.getReader();
    const decoder = new TextDecoder();
    // SSE block separator is two consecutive line breaks. langgraph dev
    // emits CRLF line endings (\r\n\r\n) — split on either form.
    const BLOCK = /\r?\n\r?\n/;
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split(BLOCK);
      buf = parts.pop() ?? "";
      for (const block of parts) {
        if (block.trim()) handleSse(block);
      }
    }
    if (buf.trim()) handleSse(buf);
    runMeta.finishedAt = Date.now();
    running = false;
  }

  // Mirror the LangGraph Python-side reducers for two known channels so the
  // UI sees the same accumulated state the graph sees. For everything else,
  // last-wins (consistent with the framework default).
  function mergeDelta(prev: unknown, next: unknown): unknown {
    if (!prev || typeof prev !== "object" || !next || typeof next !== "object") return next ?? prev;
    const a = prev as Record<string, unknown>;
    const b = next as Record<string, unknown>;
    const out: Record<string, unknown> = { ...a };
    for (const [k, v] of Object.entries(b)) {
      if (Array.isArray(a[k]) && Array.isArray(v)) {
        out[k] = [...(a[k] as unknown[]), ...v];
      } else if (a[k] && typeof a[k] === "object" && v && typeof v === "object" && !Array.isArray(v)) {
        out[k] = { ...(a[k] as Record<string, unknown>), ...(v as Record<string, unknown>) };
      } else {
        out[k] = v;
      }
    }
    return out;
  }

  function handleSse(block: string) {
    rawLog = [...rawLog, block];
    let event = "message", data = "";
    for (const rawLine of block.split(/\r?\n/)) {
      const line = rawLine.replace(/\r$/, "");
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data += line.slice(5).trim();
    }
    if (!data) return;
    let payload: any;
    try { payload = JSON.parse(data); } catch { return; }

    if (event === "updates" && payload && typeof payload === "object") {
      // payload shape: { "<node_name>": <state_delta> }
      for (const [node, delta] of Object.entries(payload)) {
        if (!stages[node]) {
          stages[node] = { node, status: "pending", invocations: 0, delta: null };
          if (!stageOrder.includes(node)) stageOrder.push(node);
        }
        const card = stages[node];
        if (!card.startedAt) card.startedAt = Date.now();
        card.invocations += 1;
        card.delta = mergeDelta(card.delta, delta);
        card.status = "done";
        card.completedAt = Date.now();
        stages[node] = card;
      }
    } else if (event === "error") {
      const node = payload?.node ?? "(unknown)";
      const card = stages[node] ?? { node, status: "pending", invocations: 0, delta: null };
      card.status = "error";
      card.error = String(payload?.error ?? payload);
      stages[node] = card;
    }
  }

  function statusColor(s: StageStatus): string {
    return s === "done" ? "#238636" : s === "running" ? "#d29922" : s === "error" ? "#f85149" : "#30363d";
  }

  type ImageItem = { src: string; caption: string; sub?: string };
  type Preview =
    | { kind: "json"; body: string }
    | { kind: "gallery"; items: ImageItem[]; body?: string }
    | { kind: "video"; src: string; mime: string; caption: string; body?: string };

  function preview(node: string, delta: unknown): Preview {
    if (!delta || typeof delta !== "object") return { kind: "json", body: JSON.stringify(delta, null, 2) };
    const d = delta as Record<string, unknown>;

    // ── mangaka_generate_page: composited page PNG (inline) ──
    if (typeof d.page_image_inline_b64 === "string" && (d.page_image_inline_b64 as string).length > 0) {
      const summary = `page ${d.elapsed_ms ?? "?"}ms · status ${d.status ?? "?"}`;
      return {
        kind: "gallery",
        items: [{
          src: `data:image/png;base64,${d.page_image_inline_b64}`,
          caption: summary,
          sub: `panels: ${(d.panel_results as any[] | undefined)?.length ?? 0}`,
        }],
        body: JSON.stringify({ status: d.status, elapsed_ms: d.elapsed_ms, panels: (d.panel_results as any[] | undefined)?.length }, null, 2),
      };
    }

    // ── comfy_run: gallery of every image returned by ComfyUI ──
    if (Array.isArray(d.images) && d.images.length && (d.images[0] as any).imageInlineB64 !== undefined) {
      const items: ImageItem[] = [];
      const summary: Array<Record<string, unknown>> = [];
      for (const im of d.images) {
        const it = im as Record<string, unknown>;
        const b64 = it.imageInlineB64 as string | undefined;
        const mime = (it.imageMime as string | undefined) ?? "image/png";
        if (b64) {
          items.push({
            src: `data:${mime};base64,${b64}`,
            caption: `node ${it.node} · ${it.filename} · ${it.byteLen} B`,
            sub: String(it.subfolder ? `${it.subfolder}/` : "") + String(it.type ?? "output"),
          });
        }
        summary.push({ node: it.node, filename: it.filename, type: it.type, byteLen: it.byteLen });
      }
      if (items.length) return { kind: "gallery", items, body: JSON.stringify(summary, null, 2) };
    }

    // ── per_panel_render: render gallery of all panels with imageInlineB64 ──
    if (d.panel_results && Array.isArray(d.panel_results) && d.panel_results.length) {
      const items: ImageItem[] = [];
      const summary: Array<Record<string, unknown>> = [];
      for (const p of d.panel_results) {
        const pr = p as Record<string, unknown>;
        const b64 = pr.imageInlineB64 as string | undefined;
        const mime = (pr.imageMime as string | undefined) ?? "image/png";
        if (b64) {
          items.push({
            src: `data:${mime};base64,${b64}`,
            caption: `${pr.panel_rkey} · score ${pr.score} · ${pr.source}`,
            sub: String(pr.refined_cid ?? ""),
          });
        }
        summary.push({ panel_rkey: pr.panel_rkey, score: pr.score, source: pr.source, refined_cid: pr.refined_cid });
      }
      if (items.length) return { kind: "gallery", items, body: JSON.stringify(summary, null, 2) };
      return { kind: "json", body: JSON.stringify(d.panel_results, null, 2) };
    }

    // ── cine_generate_video: encoded mp4 inline ──
    if (typeof d.videoInlineB64 === "string" && (d.videoInlineB64 as string).length > 0) {
      const mime = (d.videoMime as string | undefined) ?? "video/mp4";
      const fc = Number(d.frame_count ?? 0);
      const fps = Number(d.fps ?? 0);
      const elapsed = Number(d.elapsedMs ?? 0);
      return {
        kind: "video",
        src: `data:${mime};base64,${d.videoInlineB64}`,
        mime,
        caption: `${fc} frames · ${fps} fps · encoded in ${elapsed}ms`,
        body: JSON.stringify({ frame_count: fc, fps, mime, elapsedMs: elapsed, status: d.status }, null, 2),
      };
    }

    // ── cine_generate_video: per-frame gallery (during render before encode) ──
    if (Array.isArray(d.frames) && d.frames.length && (d.frames[0] as any).frameIndex !== undefined) {
      const items: ImageItem[] = [];
      for (const f of d.frames) {
        const fr = f as Record<string, unknown>;
        const summary = `frame ${fr.frameIndex} · ${fr.source ?? "?"} · ${fr.latencyMs ?? "?"}ms`;
        items.push({ src: "", caption: summary });
      }
      return { kind: "json", body: JSON.stringify(d.frames, null, 2) };
    }

    // ── cine_generate_scene worldModel preview image ──
    if (d.scene_preview && typeof d.scene_preview === "object") {
      const sp = d.scene_preview as Record<string, unknown>;
      const b64 = sp.imageInlineB64 as string | undefined;
      const mime = (sp.imageMime as string | undefined) ?? "image/png";
      if (b64) {
        return {
          kind: "gallery",
          items: [{
            src: `data:${mime};base64,${b64}`,
            caption: `world preview · ${sp.source ?? "?"} · ${sp.latencyMs ?? "?"}ms`,
            sub: String(sp.prompt ?? "").slice(0, 200),
          }],
          body: JSON.stringify({ source: sp.source, latencyMs: sp.latencyMs }, null, 2),
        };
      }
    }
    if (d.world_artifact) {
      const wa = d.world_artifact as Record<string, unknown>;
      const b64 = wa.previewImageInlineB64 as string | undefined;
      const mime = (wa.previewImageMime as string | undefined) ?? "image/png";
      if (b64) {
        return {
          kind: "gallery",
          items: [{
            src: `data:${mime};base64,${b64}`,
            caption: `world preview · ${wa.previewSource ?? "?"}`,
            sub: String(wa.previewImageCid ?? ""),
          }],
          body: JSON.stringify({ modelCid: wa.modelCid, tokenCount: wa.tokenCount, expansion: wa.expansion }, null, 2),
        };
      }
      return { kind: "json", body: JSON.stringify({ modelCid: wa.modelCid, tokenCount: wa.tokenCount, expansion: wa.expansion }, null, 2) };
    }
    if (d.usd_artifact) return { kind: "json", body: JSON.stringify(d.usd_artifact, null, 2) };
    if (d.geom_fragments || d.geom_artifact) {
      return { kind: "json", body: JSON.stringify(d.geom_artifact ?? d.geom_fragments, null, 2) };
    }
    if (d.temporal_artifact) return { kind: "json", body: JSON.stringify(d.temporal_artifact, null, 2) };

    return { kind: "json", body: JSON.stringify(delta, null, 2) };
  }

  function ms(card: StageCard): string {
    if (!card.startedAt || !card.completedAt) return "";
    return `${card.completedAt - card.startedAt} ms`;
  }
</script>

<svelte:head>
  <style>
    :global(body) { margin: 0; font: 13px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #0d1117; color: #c9d1d9; }
    :global(button) { background: #238636; color: white; border: 0; padding: 6px 14px; border-radius: 4px; cursor: pointer; font-weight: 600; }
    :global(button:disabled) { background: #30363d; cursor: not-allowed; }
    :global(select), :global(textarea), :global(input) {
      background: #161b22; color: #c9d1d9; border: 1px solid #30363d; border-radius: 4px; padding: 6px 8px; font-family: inherit;
    }
    :global(textarea) { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
    :global(pre) { margin: 0; padding: 8px; background: #010409; border-radius: 4px; overflow: auto; font-size: 11px; }
  </style>
</svelte:head>

<main>
  <header>
    <div class="brand">
      <strong>studio.etzhayyim.com</strong>
      <span class="sub">lg-mangaka · cine pipeline (kami-cine 1.0)</span>
    </div>
    <div class="who">{userEmail || "—"}</div>
  </header>

  <div class="cols">
    <aside>
      <h3>Graphs ({assistants.length})</h3>
      <ul>
        {#each assistants as a (a.assistant_id)}
          {@const gid = a.graph_id ?? a.name ?? a.assistant_id}
          <li>
            <button class:active={a.assistant_id === selectedAid} onclick={() => selectGraph(a.assistant_id, gid)}>
              {gid}
            </button>
          </li>
        {/each}
        {#if assistants.length === 0}
          <li class="empty">none — is `langgraph dev` running on :2024?</li>
        {/if}
      </ul>

      <h3 style="margin-top:18px">Input</h3>
      <textarea bind:value={inputJson} rows="20" spellcheck="false"></textarea>
      {#if inputError}<div class="err">{inputError}</div>{/if}
      <button onclick={run} disabled={running || !selectedAid}>
        {running ? "Running…" : "▶ Run"}
      </button>
      {#if runMeta.threadId}
        <div class="meta">
          thread <code>{runMeta.threadId.slice(0, 8)}…</code>
          {#if runMeta.finishedAt && runMeta.startedAt}
            · {runMeta.finishedAt - runMeta.startedAt} ms
          {/if}
        </div>
      {/if}
    </aside>

    <section class="dag">
      <header class="dag-h">
        <h3>
          {dagMode === "comfyui" ? `ComfyUI — ${comfyUrl}` : `Pregel DAG — ${selectedGid || "(none)"}`}
        </h3>
        <div class="tabs" role="tablist">
          <button
            role="tab"
            aria-selected={dagMode === "nodes"}
            class:active={dagMode === "nodes"}
            onclick={() => (dagMode = "nodes")}
          >Nodes</button>
          <button
            role="tab"
            aria-selected={dagMode === "mermaid"}
            class:active={dagMode === "mermaid"}
            onclick={() => (dagMode = "mermaid")}
          >Mermaid</button>
          <button
            role="tab"
            aria-selected={dagMode === "comfyui"}
            class:active={dagMode === "comfyui"}
            onclick={() => (dagMode = "comfyui")}
            title={comfyUrl}
          >ComfyUI</button>
        </div>
      </header>
      {#if dagMode === "nodes"}
        <div class="node-view">
          <NodeGraph
            {graphData}
            {stages}
            stageLabel={STAGE_LABEL}
          />
        </div>
      {:else if dagMode === "mermaid"}
        <div class="mermaid">{@html mermaidSvg}</div>
      {:else}
        <iframe
          class="comfy-frame"
          src={comfyUrl}
          title="ComfyUI"
          referrerpolicy="no-referrer"
          sandbox="allow-same-origin allow-scripts allow-forms allow-popups allow-downloads"
        ></iframe>
      {/if}
    </section>

    <section class="stages">
      <h3>Stages</h3>
      {#if stageOrder.length === 0}
        <div class="empty">select a graph to populate stage cards</div>
      {/if}
      {#each stageOrder as nodeId (nodeId)}
        {@const card = stages[nodeId]}
        {@const label = STAGE_LABEL[nodeId] ?? nodeId}
        {@const p = preview(nodeId, card.delta)}
        <article class="card" style:border-left-color={statusColor(card.status)}>
          <header class="card-h">
            <span class="dot" style:background={statusColor(card.status)}></span>
            <strong>{label}</strong>
            <span class="node">{nodeId}{card.invocations > 1 ? ` ×${card.invocations}` : ""}</span>
            <span class="dur">{ms(card)}</span>
          </header>
          {#if card.error}
            <pre class="err-pre">{card.error}</pre>
          {:else if card.delta}
            {#if p.kind === "gallery"}
              <div class="gallery">
                {#each p.items as item, i (i)}
                  <figure class="img-wrap">
                    <img src={item.src} alt={item.caption} />
                    <figcaption>{item.caption}{#if item.sub}<br/><code>{item.sub}</code>{/if}</figcaption>
                  </figure>
                {/each}
              </div>
              {#if p.body}<pre>{p.body}</pre>{/if}
            {:else if p.kind === "video"}
              <figure class="video-wrap">
                <video src={p.src} controls autoplay loop muted playsinline></video>
                <figcaption>{p.caption}</figcaption>
              </figure>
              {#if p.body}<pre>{p.body}</pre>{/if}
            {:else}
              <pre>{p.body}</pre>
            {/if}
          {:else}
            <div class="ghost">(no output yet)</div>
          {/if}
        </article>
      {/each}

      <details style="margin-top:12px">
        <summary>raw SSE log ({rawLog.length})</summary>
        <div class="raw">
          {#each rawLog as line, i (i)}<pre>{line}</pre>{/each}
        </div>
      </details>
    </section>
  </div>
</main>

<style>
  main { height: 100vh; display: flex; flex-direction: column; }
  header { padding: 8px 16px; border-bottom: 1px solid #30363d; display: flex; justify-content: space-between; align-items: center; }
  .brand strong { color: #e6edf3; margin-right: 12px; }
  .sub { color: #8b949e; font-size: 11px; }
  .who { color: #8b949e; font-size: 11px; }

  .cols { display: grid; grid-template-columns: 320px 1fr 1fr; gap: 1px; background: #30363d; flex: 1; overflow: hidden; }
  /* Narrow viewports (e.g. when CDP can't widen the Chrome window): stack vertically. */
  @media (max-width: 900px) {
    .cols { grid-template-columns: 1fr; grid-auto-rows: minmax(200px, auto); overflow: auto; }
  }
  aside, .dag, .stages { background: #0d1117; padding: 12px; overflow: auto; min-width: 0; }
  h3 { margin: 4px 0 8px; font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; }

  aside ul { list-style: none; margin: 0; padding: 0; }
  aside li { margin: 2px 0; }
  aside button {
    background: transparent; color: #c9d1d9; text-align: left; width: 100%;
    padding: 4px 8px; border-radius: 3px; font-weight: 400; font-size: 12px;
  }
  aside button:hover { background: #161b22; }
  aside button.active { background: #1f6feb; color: white; }
  aside textarea { width: 100%; box-sizing: border-box; }
  aside .meta { margin-top: 6px; color: #8b949e; font-size: 11px; }
  aside .err { color: #f85149; font-size: 11px; margin: 4px 0; }
  .empty { color: #8b949e; font-size: 11px; padding: 4px 0; }

  .dag { display: flex; flex-direction: column; padding: 0; }
  .dag .dag-h {
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 12px 4px; gap: 12px;
  }
  .dag .dag-h h3 { margin: 0; }
  .dag .tabs { display: flex; gap: 2px; background: #161b22; padding: 2px; border-radius: 4px; }
  .dag .tabs button {
    background: transparent; color: #8b949e; font-weight: 500;
    padding: 4px 12px; border-radius: 3px; font-size: 11px;
  }
  .dag .tabs button.active { background: #1f6feb; color: white; }
  .dag .tabs button:hover:not(.active) { color: #c9d1d9; }
  .dag .mermaid { padding: 0 12px 12px; }
  .dag .mermaid :global(svg) { max-width: 100%; height: auto; }
  .dag .node-view { flex: 1; min-height: 0; }
  .dag .comfy-frame {
    flex: 1; min-height: 0; width: 100%;
    border: 0; background: #010409;
  }

  .stages { display: flex; flex-direction: column; gap: 8px; }
  .card {
    background: #161b22; border-left: 3px solid #30363d; border-radius: 4px;
    padding: 8px 12px;
  }
  .card-h { display: flex; align-items: center; gap: 8px; }
  .card-h .dot { width: 8px; height: 8px; border-radius: 50%; }
  .card-h strong { color: #e6edf3; font-size: 12px; }
  .card-h .node { color: #8b949e; font-size: 10px; font-family: ui-monospace, monospace; }
  .card-h .dur { margin-left: auto; color: #8b949e; font-size: 10px; }
  .card pre { margin-top: 6px; max-height: 220px; }
  .card .ghost { margin-top: 6px; color: #6e7681; font-style: italic; font-size: 11px; }
  .card .err-pre { color: #f85149; background: #2d1115; }

  .gallery { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 6px; margin: 8px 0; }
  .img-wrap { margin: 0; padding: 6px; display: flex; flex-direction: column; align-items: center; background: #010409; border-radius: 4px; }
  .img-wrap img { max-width: 100%; max-height: 280px; border-radius: 3px; box-shadow: 0 2px 8px rgba(0,0,0,0.4); }
  .img-wrap figcaption { margin-top: 4px; color: #8b949e; font-size: 10px; text-align: center; word-break: break-all; }
  .img-wrap code { font-family: ui-monospace, monospace; color: #6e7681; font-size: 9px; }

  .video-wrap { margin: 8px 0; padding: 6px; display: flex; flex-direction: column; align-items: center; background: #010409; border-radius: 4px; }
  .video-wrap video { max-width: 100%; max-height: 420px; border-radius: 3px; box-shadow: 0 2px 8px rgba(0,0,0,0.4); }
  .video-wrap figcaption { margin-top: 4px; color: #8b949e; font-size: 10px; text-align: center; }

  .raw { display: flex; flex-direction: column; gap: 4px; margin-top: 6px; }
</style>
