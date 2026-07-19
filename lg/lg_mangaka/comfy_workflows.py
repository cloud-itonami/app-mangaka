"""API-format ComfyUI workflow builders for mangaka.etzhayyim.com domain records.

Each builder returns a {node_id: {class_type, inputs}} dict that comfy_runner
POSTs to ComfyUI's /prompt endpoint. The same shape ComfyUI's
"Save (API Format)" produces, so the workflows are also openable in the
embedded ComfyUI editor when persisted via install-mangaka-comfy-workflow.py.

Three builders mirror the three domain record types Studio generates per
the mangaka schema (`com.etzhayyim.mangaka.{character,environment,panel}`):

  character_workflow(...)   one character design sheet, batch of N views
  scene_workflow(...)       one environment establishing shot, landscape
  panel_workflow(...)       one manga panel, 2-pass (composition → ink)

All builders accept overrides for checkpoint, sampler, seed, steps, cfg so
the artist can sweep parameters without editing the workflow JSON.
"""

from __future__ import annotations

import secrets
from typing import Any, Iterable

"""Default checkpoint picked at runtime.

Order of preference (env COMFY_DEFAULT_CKPT overrides):
  1. illustriousXL_v11.safetensors  — best manga line quality (install-comfy-quality-pack)
  2. noobaiXL_epsPred11.safetensors — alt. shounen ink style
  3. waiIllustriousSDXL_v160.safetensors  — already installed, second best
  4. animagine-xl-4.0.safetensors   — original default, last resort
"""
import os as _os
DEFAULT_CKPT = _os.environ.get(
    "COMFY_DEFAULT_CKPT",
    "waiIllustriousSDXL_v160.safetensors",
)
TRIPOSR_CKPT = "triposr-model.ckpt"
# Higher-quality default sampler combo for SDXL (animagine / illustrious / noobai).
DEFAULT_SAMPLER = "dpmpp_3m_sde_gpu"
DEFAULT_SCHEDULER = "karras"

# Flux.1 [dev] defaults. Q4_K_S GGUF is the best quality / VRAM trade-off
# verified on the AMD Radeon 8060S (ROCm 7.2) host: 1024x1536 in ~180s.
FLUX_UNET = _os.environ.get("COMFY_FLUX_UNET", "flux1-dev-Q4_K_S.gguf")
FLUX_T5 = _os.environ.get("COMFY_FLUX_T5", "t5xxl_fp8_e4m3fn.safetensors")
FLUX_CLIP_L = _os.environ.get("COMFY_FLUX_CLIP_L", "clip_l.safetensors")
FLUX_VAE = _os.environ.get("COMFY_FLUX_VAE", "flux_ae.safetensors")
FLUX_SAMPLER = _os.environ.get("COMFY_FLUX_SAMPLER", "euler")
FLUX_SCHEDULER = _os.environ.get("COMFY_FLUX_SCHEDULER", "simple")
NEG_DEFAULT = (
    "(low quality:1.4), (worst quality:1.4), blurry, jpeg artifacts, "
    "watermark, signature, deformed, extra limbs, bad anatomy, "
    "color photograph, soft focus, out of frame, mutated, cropped"
)

# ── character design sheet ─────────────────────────────────────────────────

def character_workflow(
    *,
    name: str,
    description: str,
    style: str = "masterpiece, best quality, very detailed, anime, manga, ink, screentone",
    pose_hint: str = "character reference sheet, multiple views, neutral and dynamic expressions",
    seed: int = 0,
    steps: int = 32,
    cfg: float = 8.0,
    sampler: str = DEFAULT_SAMPLER,
    scheduler: str = DEFAULT_SCHEDULER,
    width: int = 1024,
    height: int = 1536,
    batch: int = 2,
    ckpt: str = DEFAULT_CKPT,
    negative: str = NEG_DEFAULT,
) -> dict[str, Any]:
    """Single-pass character sheet generator. batch>=2 yields per-view variants.
    Saves as `mangaka-character-{name}_NNNNN.png` (one per batch row)."""
    prompt = (
        f"manga character design, {name}, {description}, {pose_hint}, "
        f"front three-quarter view, cleanly lined, {style}, "
        f"detailed face, expressive eyes, dynamic pose"
    )
    safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)[:60] or "character"
    real_seed = int(seed) or secrets.randbelow(2**32)
    return {
        "1":  {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2":  {"class_type": "EmptyLatentImage", "inputs": {
                "width": int(width), "height": int(height), "batch_size": max(1, int(batch))}},
        "3":  {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "4":  {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
        "5":  {"class_type": "KSampler", "inputs": {
                "seed": real_seed, "steps": int(steps), "cfg": float(cfg),
                "sampler_name": sampler, "scheduler": scheduler, "denoise": 1.0,
                "model": ["1", 0], "positive": ["3", 0], "negative": ["4", 0],
                "latent_image": ["2", 0]}},
        "6":  {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7":  {"class_type": "SaveImage", "inputs": {
                "filename_prefix": f"mangaka-character-{safe_name}", "images": ["6", 0]}},
    }


# ── environment / scene establishing shot ──────────────────────────────────

def scene_workflow(
    *,
    name: str,
    description: str,
    style: str = "masterpiece, best quality, very detailed, anime, manga, ink, screentone",
    time_of_day: str = "",
    weather: str = "",
    seed: int = 0,
    steps: int = 32,
    cfg: float = 8.0,
    sampler: str = DEFAULT_SAMPLER,
    scheduler: str = DEFAULT_SCHEDULER,
    width: int = 1536,
    height: int = 1024,
    ckpt: str = DEFAULT_CKPT,
    negative: str = NEG_DEFAULT,
) -> dict[str, Any]:
    """Single-pass establishing-shot generator, landscape by default.
    Saves as `mangaka-scene-{name}_NNNNN.png`."""
    parts = [f"manga establishing shot, {description}"]
    if time_of_day: parts.append(time_of_day)
    if weather:     parts.append(weather)
    parts += [
        "no characters, environment design, atmospheric perspective",
        "wide cinematic framing, detailed background",
        style,
    ]
    prompt = ", ".join(parts)
    safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)[:60] or "scene"
    real_seed = int(seed) or secrets.randbelow(2**32)
    return {
        "1":  {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2":  {"class_type": "EmptyLatentImage", "inputs": {
                "width": int(width), "height": int(height), "batch_size": 1}},
        "3":  {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "4":  {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
        "5":  {"class_type": "KSampler", "inputs": {
                "seed": real_seed, "steps": int(steps), "cfg": float(cfg),
                "sampler_name": sampler, "scheduler": scheduler, "denoise": 1.0,
                "model": ["1", 0], "positive": ["3", 0], "negative": ["4", 0],
                "latent_image": ["2", 0]}},
        "6":  {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7":  {"class_type": "SaveImage", "inputs": {
                "filename_prefix": f"mangaka-scene-{safe_name}", "images": ["6", 0]}},
    }


# ── HQ panel — IPAdapter FaceID + ControlNet Canny + Illustrious base ──────

def panel_hq_workflow(
    *,
    panel_rkey: str = "panel",
    reference_image_filename: str,
    framing: str = "medium",
    characters: Iterable[str] = (),
    environment: str = "",
    mood: str = "",
    action: str = "",
    seed: int = 0,
    width: int = 1024,
    height: int = 1536,
    steps: int = 32,
    cfg: float = 7.5,
    ckpt: str = DEFAULT_CKPT,
    ipadapter_weight: float = 0.75,
    ipadapter_face_weight: float = 0.85,
    controlnet_strength: float = 0.55,
    controlnet_model: str = "controlnet-union-sdxl-promax.safetensors",
    ipadapter_clip_vision: str = "CLIP-ViT-bigG-14-laion2B-39B-b160k.bin",
    ipadapter_model: str = "ip-adapter-plus_sdxl_vit-h.safetensors",
    ipadapter_faceid_model: str = "ip-adapter-faceid-plusv2_sdxl.bin",
    faceid_lora: str = "ip-adapter-faceid-plusv2_sdxl_lora.safetensors",
    insightface_provider: str = "CPU",
    negative: str = NEG_DEFAULT,
) -> dict[str, Any]:
    """High-quality panel using IPAdapter (face identity) + ControlNet (canny line
    structure from the reference). Requires the tier-1 + tier-2 packs from
    install-comfy-quality-pack.ps1; falls back gracefully if the optional nodes
    are missing (ComfyUI will surface a clear error on /prompt validation).

    Pipeline:
      LoadImage(ref) ─┬─→ IPAdapterFaceIDPlus (face identity, weight=0.85)
                      ├─→ Canny edge → ControlNetUnion (line structure, str=0.55)
                      └─→ (no img2img — fresh latent from prompt)
      EmptyLatentImage ─→ KSampler (IPAdapter-conditioned model + dual CN)
      VAEDecode → SaveImage
    """
    framing_hint = _FRAMING_HINT.get(framing.lower(), framing or "medium shot")
    char_clause = (", ".join(characters)) if characters else "scene"
    pos = ", ".join(p for p in [
        f"masterpiece, best quality, manga inked panel, {framing_hint}",
        f"characters: {char_clause}" if characters else "",
        f"environment: {environment}" if environment else "",
        f"mood: {mood}" if mood else "",
        f"action: {action}" if action else "",
        "sharp black ink lines, screentone, hatching, high contrast monochrome,",
        "shonen-jump style, dynamic composition, dramatic lighting",
    ] if p)
    safe_rkey = "".join(c if c.isalnum() or c in "-_" else "-" for c in panel_rkey)[:60] or "panel"
    real_seed = int(seed) or secrets.randbelow(2**32)
    return {
        "1":  {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2":  {"class_type": "LoadImage", "inputs": {"image": reference_image_filename}},
        "3":  {"class_type": "EmptyLatentImage", "inputs": {
                "width": int(width), "height": int(height), "batch_size": 1}},
        "4":  {"class_type": "CLIPTextEncode", "inputs": {"text": pos, "clip": ["1", 1]}},
        "5":  {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},

        # IPAdapter FaceID + Plus (cubiq ComfyUI_IPAdapter_plus)
        "10": {"class_type": "IPAdapterUnifiedLoader", "inputs": {
                "model": ["1", 0],
                "preset": "PLUS (high strength)",
                "lora_strength": 0.6,
                "provider": insightface_provider}},
        "11": {"class_type": "IPAdapterFaceID", "inputs": {
                "model": ["10", 0],
                "ipadapter": ["10", 1],
                "image": ["2", 0],
                "weight": float(ipadapter_face_weight),
                "weight_faceidv2": float(ipadapter_weight),
                "weight_type": "linear",
                "combine_embeds": "concat",
                "start_at": 0.0, "end_at": 1.0,
                "embeds_scaling": "V only"}},

        # ControlNet Union (canny channel)
        "20": {"class_type": "ControlNetLoader", "inputs": {"control_net_name": controlnet_model}},
        "21": {"class_type": "Canny", "inputs": {
                "image": ["2", 0], "low_threshold": 0.1, "high_threshold": 0.3}},
        "22": {"class_type": "ControlNetApplyAdvanced", "inputs": {
                "positive": ["4", 0], "negative": ["5", 0],
                "control_net": ["20", 0], "image": ["21", 0],
                "strength": float(controlnet_strength),
                "start_percent": 0.0, "end_percent": 0.75,
                "vae": ["1", 2]}},

        "30": {"class_type": "KSampler", "inputs": {
                "seed": real_seed, "steps": int(steps), "cfg": float(cfg),
                "sampler_name": DEFAULT_SAMPLER, "scheduler": DEFAULT_SCHEDULER,
                "denoise": 1.0,
                "model": ["11", 0],
                "positive": ["22", 0], "negative": ["22", 1],
                "latent_image": ["3", 0]}},
        "31": {"class_type": "VAEDecode", "inputs": {"samples": ["30", 0], "vae": ["1", 2]}},
        "32": {"class_type": "SaveImage", "inputs": {
                "filename_prefix": f"mangaka-panel-hq-{safe_rkey}", "images": ["31", 0]}},
    }


# ── Flux.1 [dev] panel — single-pass, text-to-image, no character ref ──────

def panel_flux_workflow(
    *,
    panel_rkey: str = "panel",
    framing: str = "medium",
    characters: Iterable[str] = (),
    environment: str = "",
    mood: str = "",
    action: str = "",
    width: int = 1024,
    height: int = 1536,
    steps: int = 22,
    guidance: float = 3.5,
    seed: int = 0,
    unet: str = FLUX_UNET,
    t5: str = FLUX_T5,
    clip_l: str = FLUX_CLIP_L,
    vae: str = FLUX_VAE,
    sampler: str = FLUX_SAMPLER,
    scheduler: str = FLUX_SCHEDULER,
    style_prompt: str = (
        "manga inked panel, sharp black ink lines, screentone, hatching, "
        "high contrast monochrome, masterpiece, best quality, shonen-jump style"
    ),
) -> dict[str, Any]:
    """Single-pass Flux.1 [dev] panel generator using GGUF Q4 (city96).

    Flux is text-to-image only here — no img2img reference. Quality jump
    over SDXL is large (verified 2026-05-21 smoke: 1024x1536 manga ink
    in ~180s on AMD Radeon 8060S ROCm 7.2). Guidance is the Flux-native
    knob (default 3.5; lower = more creative); CFG is fixed at 1.0 per
    the Flux distillation training. T5 understands long natural-language
    prompts, so we build a fuller sentence-style prompt than SDXL.

    For character identity preservation use mangaka_generate_panel_hq
    (SDXL + IPAdapter / PuLID) for now — Flux IPAdapter / PuLID Flux
    weights are a follow-on install (see PuLID Flux 0.9.1, ~6 GB)."""
    framing_hint = _FRAMING_HINT.get(framing.lower(), framing or "medium shot")
    char_clause = (", ".join(characters)) if characters else "scene"
    pos = ", ".join(p for p in [
        f"{framing_hint}",
        f"featuring {char_clause}" if characters else "",
        f"in {environment}" if environment else "",
        f"mood: {mood}" if mood else "",
        f"action: {action}" if action else "",
        "dramatic lighting, dynamic perspective, cinematic framing",
        style_prompt,
    ] if p)
    safe_rkey = "".join(c if c.isalnum() or c in "-_" else "-" for c in panel_rkey)[:60] or "panel"
    real_seed = int(seed) or secrets.randbelow(2**32)
    return {
        "1":  {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": unet}},
        "2":  {"class_type": "DualCLIPLoader", "inputs": {
                "clip_name1": t5, "clip_name2": clip_l, "type": "flux"}},
        "3":  {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "4":  {"class_type": "CLIPTextEncode", "inputs": {
                "text": pos, "clip": ["2", 0]}},
        "5":  {"class_type": "CLIPTextEncode", "inputs": {
                "text": "", "clip": ["2", 0]}},
        "6":  {"class_type": "FluxGuidance", "inputs": {
                "conditioning": ["4", 0], "guidance": float(guidance)}},
        "7":  {"class_type": "EmptyLatentImage", "inputs": {
                "width": int(width), "height": int(height), "batch_size": 1}},
        "8":  {"class_type": "ModelSamplingFlux", "inputs": {
                "model": ["1", 0],
                "max_shift": 1.15, "base_shift": 0.5,
                "width": int(width), "height": int(height)}},
        "9":  {"class_type": "KSampler", "inputs": {
                "seed": real_seed, "steps": int(steps), "cfg": 1.0,
                "sampler_name": sampler, "scheduler": scheduler, "denoise": 1.0,
                "model": ["8", 0],
                "positive": ["6", 0], "negative": ["5", 0],
                "latent_image": ["7", 0]}},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["3", 0]}},
        "11": {"class_type": "SaveImage", "inputs": {
                "filename_prefix": f"mangaka-panel-flux-{safe_rkey}",
                "images": ["10", 0]}},
    }


# ── Flux + PuLID — character identity preservation on Flux ──────────────────

PULID_FLUX_MODEL = _os.environ.get("COMFY_PULID_FLUX", "pulid_flux_v0.9.1.safetensors")


def train_character_lora_workflow(
    *,
    output_name: str,
    data_path: str,                       # Windows path on the ComfyUI host
    ckpt_name: str = "illustriousXL_v01.safetensors",
    batch_size: int = 1,
    max_train_epochs: int = 10,
    save_every_n_epochs: int = 10,
    clip_skip: int = 2,
    output_dir: str = "models/loras",
) -> dict[str, Any]:
    """Wraps `Lora Training in ComfyUI` (LarryJane491/Lora-Training-in-Comfy).
    Returns a workflow that, when submitted, runs kohya-ss/sd-scripts
    training in-process on the ComfyUI host.

    Training data layout (kohya convention on the host):
      <data_path>/<num>_<trigger_token>/
                  image_001.png
                  image_001.txt   (caption / tags)
                  ...
    <num> = repeats per epoch (8-12 typical). <trigger_token> is the
    word the artist will use in prompts to invoke the LoRA (e.g.
    "yuki_persona"). 8-12 cleanly captioned images is enough for a
    character LoRA at illustriousXL_v01.

    On AMD ROCm Windows the run falls back to AdamW fp32 (bitsandbytes
    8bit optimizers aren't available). Expect 20-40 min for 8 imgs,
    10 epochs at 512 res, batch_size=1.

    Output LoRA lands at `<output_dir>/<output_name>.safetensors` and
    is immediately usable by any subsequent LoraLoader node (panel_hq /
    panel_flux_pulid / etc.).
    """
    return {
        "1": {"class_type": "Lora Training in ComfyUI", "inputs": {
            "ckpt_name": ckpt_name,
            "data_path": data_path,
            "batch_size": int(batch_size),
            "max_train_epoches": int(max_train_epochs),
            "save_every_n_epochs": int(save_every_n_epochs),
            "output_name": output_name,
            "clip_skip": int(clip_skip),
            "output_dir": output_dir,
        }},
    }


# Wan 2.2 TI2V 5B (text+image to video). Already installed at the studio host.
WAN_UNET = _os.environ.get("COMFY_WAN_UNET", "wan2.2_ti2v_5B_fp16.safetensors")
WAN_VAE = _os.environ.get("COMFY_WAN_VAE", "wan2.2_vae.safetensors")
WAN_CLIP = _os.environ.get("COMFY_WAN_CLIP", "umt5_xxl_fp8_e4m3fn_scaled.safetensors")


def video_wan_workflow(
    *,
    video_rkey: str = "video",
    start_image_filename: str | None,    # uploaded ref image filename, or None for pure t2v
    prompt: str,
    negative_prompt: str = "blurry, low quality, watermark, distorted, oversaturated",
    width: int = 832,
    height: int = 1216,
    length: int = 33,                    # frames (Wan defaults 81, but 33-49 is faster)
    fps: int = 16,
    steps: int = 20,
    cfg: float = 5.0,
    shift: float = 8.0,                  # ModelSamplingSD3 shift (Wan recommended 8)
    seed: int = 0,
    sampler: str = "uni_pc",
    scheduler: str = "simple",
    unet: str = WAN_UNET,
    vae: str = WAN_VAE,
    clip: str = WAN_CLIP,
) -> dict[str, Any]:
    """Wan 2.2 TI2V 5B image-to-video / text-to-video workflow.

    Pipeline:
      UNETLoader(wan2.2_ti2v_5B) -> ModelSamplingSD3 -> KSampler
      CLIPLoader(umt5_xxl, type=wan) -> CLIPTextEncode -> conditioning
      LoadImage(start) [optional] + VAELoader(wan2.2_vae) -> WanImageToVideo
      KSampler -> VAEDecode -> SaveAnimatedWEBP

    Output is an animated WebP at the requested fps. For MP4 use VHS_VideoCombine
    or post-encode the WebP via ffmpeg.

    Wan 2.2 5B at fp16 is ~9 GB VRAM; the larger 14B is the better-quality
    variant if you have headroom. Length=33 frames at 16 fps = ~2s clip.
    """
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in video_rkey)[:60] or "video"
    real_seed = int(seed) or secrets.randbelow(2**32)
    has_start = bool(start_image_filename)

    wf: dict[str, Any] = {
        "1":  {"class_type": "UNETLoader", "inputs": {
                "unet_name": unet, "weight_dtype": "default"}},
        "2":  {"class_type": "CLIPLoader", "inputs": {
                "clip_name": clip, "type": "wan"}},
        "3":  {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "4":  {"class_type": "CLIPTextEncode", "inputs": {
                "text": prompt, "clip": ["2", 0]}},
        "5":  {"class_type": "CLIPTextEncode", "inputs": {
                "text": negative_prompt, "clip": ["2", 0]}},
        "6":  {"class_type": "ModelSamplingSD3", "inputs": {
                "model": ["1", 0], "shift": float(shift)}},
    }
    if has_start:
        wf["7"] = {"class_type": "LoadImage", "inputs": {"image": start_image_filename}}
        wf["8"] = {"class_type": "WanImageToVideo", "inputs": {
            "positive": ["4", 0], "negative": ["5", 0],
            "vae": ["3", 0],
            "width": int(width), "height": int(height),
            "length": int(length), "batch_size": 1,
            "start_image": ["7", 0]}}
        pos_ref, neg_ref, lat_ref = ["8", 0], ["8", 1], ["8", 2]
    else:
        wf["8"] = {"class_type": "Wan22ImageToVideoLatent", "inputs": {
            "vae": ["3", 0],
            "width": int(width), "height": int(height),
            "length": int(length), "batch_size": 1}}
        pos_ref, neg_ref, lat_ref = ["4", 0], ["5", 0], ["8", 0]

    wf["9"] = {"class_type": "KSampler", "inputs": {
        "seed": real_seed, "steps": int(steps), "cfg": float(cfg),
        "sampler_name": sampler, "scheduler": scheduler, "denoise": 1.0,
        "model": ["6", 0],
        "positive": pos_ref, "negative": neg_ref,
        "latent_image": lat_ref}}
    wf["10"] = {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["3", 0]}}
    wf["11"] = {"class_type": "SaveAnimatedWEBP", "inputs": {
        "images": ["10", 0],
        "filename_prefix": f"mangaka-video-wan-{safe}",
        "fps": float(fps),
        "lossless": False,
        "quality": 90,
        "method": "default"}}
    return wf


def panel_flux_pulid_workflow(
    *,
    panel_rkey: str = "panel",
    reference_image_filename: str,
    framing: str = "medium",
    characters: Iterable[str] = (),
    environment: str = "",
    mood: str = "",
    action: str = "",
    width: int = 1024,
    height: int = 1536,
    steps: int = 22,
    guidance: float = 3.5,
    pulid_weight: float = 0.7,
    pulid_start_at: float = 0.0,
    pulid_end_at: float = 1.0,
    seed: int = 0,
    unet: str = FLUX_UNET,
    t5: str = FLUX_T5,
    clip_l: str = FLUX_CLIP_L,
    vae: str = FLUX_VAE,
    sampler: str = FLUX_SAMPLER,
    scheduler: str = FLUX_SCHEDULER,
    pulid_model: str = PULID_FLUX_MODEL,
    style_prompt: str = (
        "manga inked panel, sharp black ink lines, screentone, hatching, "
        "high contrast monochrome, masterpiece, best quality, shonen-jump style"
    ),
) -> dict[str, Any]:
    """Flux + PuLID workflow: face identity from `reference_image_filename`
    + text-to-image generation. Uses the lldacing/ComfyUI_PuLID_Flux_ll
    custom node and the pulid_flux_v0.9.1 weights.

    Pipeline:
      UnetLoaderGGUF -> ModelSamplingFlux ─┐
                                            ├-> ApplyPulidFlux -> KSampler
      PulidFluxModelLoader ────────────────┤
      PulidFluxEvaClipLoader ──────────────┤
      PulidFluxInsightFaceLoader ──────────┤
      LoadImage (ref) ─────────────────────┘

    pulid_weight controls how strongly the reference face is injected.
    0.7 is a good default; >0.9 starts copying composition, <0.5 loses
    identity. start_at / end_at let you constrain PuLID to a substep
    window (e.g. 0.0 -> 0.5 keeps it out of the final denoise polish).
    """
    framing_hint = _FRAMING_HINT.get(framing.lower(), framing or "medium shot")
    char_clause = (", ".join(characters)) if characters else "scene"
    pos = ", ".join(p for p in [
        f"{framing_hint}",
        f"featuring {char_clause}" if characters else "",
        f"in {environment}" if environment else "",
        f"mood: {mood}" if mood else "",
        f"action: {action}" if action else "",
        "dramatic lighting, dynamic perspective, cinematic framing",
        style_prompt,
    ] if p)
    safe_rkey = "".join(c if c.isalnum() or c in "-_" else "-" for c in panel_rkey)[:60] or "panel"
    real_seed = int(seed) or secrets.randbelow(2**32)

    return {
        "1":  {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": unet}},
        "2":  {"class_type": "DualCLIPLoader", "inputs": {
                "clip_name1": t5, "clip_name2": clip_l, "type": "flux"}},
        "3":  {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "4":  {"class_type": "CLIPTextEncode", "inputs": {
                "text": pos, "clip": ["2", 0]}},
        "5":  {"class_type": "CLIPTextEncode", "inputs": {
                "text": "", "clip": ["2", 0]}},
        "6":  {"class_type": "FluxGuidance", "inputs": {
                "conditioning": ["4", 0], "guidance": float(guidance)}},
        "7":  {"class_type": "EmptyLatentImage", "inputs": {
                "width": int(width), "height": int(height), "batch_size": 1}},
        "8":  {"class_type": "ModelSamplingFlux", "inputs": {
                "model": ["1", 0],
                "max_shift": 1.15, "base_shift": 0.5,
                "width": int(width), "height": int(height)}},

        # PuLID Flux pipeline
        "20": {"class_type": "LoadImage", "inputs": {"image": reference_image_filename}},
        "21": {"class_type": "PulidFluxModelLoader", "inputs": {"pulid_file": pulid_model}},
        "22": {"class_type": "PulidFluxEvaClipLoader", "inputs": {}},
        "23": {"class_type": "PulidFluxInsightFaceLoader", "inputs": {"provider": "CPU"}},
        "24": {"class_type": "ApplyPulidFlux", "inputs": {
                "model": ["8", 0],
                "pulid_flux": ["21", 0],
                "eva_clip": ["22", 0],
                "face_analysis": ["23", 0],
                "image": ["20", 0],
                "weight": float(pulid_weight),
                "start_at": float(pulid_start_at),
                "end_at": float(pulid_end_at),
                "fusion": "mean",
                "fusion_weight_max": 1.0,
                "fusion_weight_min": 0.0,
                "train_step": 1000,
                "use_gray": True,
                "attn_mask": None,
                "prior_image": None}},

        "9":  {"class_type": "KSampler", "inputs": {
                "seed": real_seed, "steps": int(steps), "cfg": 1.0,
                "sampler_name": sampler, "scheduler": scheduler, "denoise": 1.0,
                "model": ["24", 0],
                "positive": ["6", 0], "negative": ["5", 0],
                "latent_image": ["7", 0]}},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["3", 0]}},
        "11": {"class_type": "SaveImage", "inputs": {
                "filename_prefix": f"mangaka-panel-flux-pulid-{safe_rkey}",
                "images": ["10", 0]}},
    }


# ── HQ character 3D via Hunyuan3D-2 (texture + PBR + USDZ) ──────────────────

def character_3d_hy3d_workflow(
    *,
    name: str,
    input_image_filename: str,
    bake_size: int = 1024,
    mesh_simplify: int = 50000,
    save_format: str = "glb",  # SaveGLB also accepts usdz/obj/fbx via extension
) -> dict[str, Any]:
    """Hunyuan3D-2 single-image → textured PBR mesh. Replaces the TripoSR path
    once the Hy3D Delight + Paint checkpoints have been auto-downloaded (first
    queue of this workflow triggers the ~6 GB DL from tencent/Hunyuan3D-2).

    Uses the existing Hy3D ComfyUI node family (already installed):
      DownloadAndLoadHy3DDelightModel  → MESH pipeline
      DownloadAndLoadHy3DPaintModel    → texture pipeline
      Hy3DGenerateMesh                 → vertices + faces
      Hy3DBakeFromMultiview            → albedo + PBR baked from N views
      Hy3DApplyTexture                 → wraps texture onto mesh
      Hy3DExportMesh / SaveGLB         → write GLB / USDZ

    NOTE: the exact node wiring depends on the Hy3D wrapper version. This
    template is the minimal "image-only" path; multi-view + delighting can
    be layered on later.
    """
    safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)[:60] or "character"
    return {
        "1":  {"class_type": "LoadImage", "inputs": {"image": input_image_filename}},
        "2":  {"class_type": "DownloadAndLoadHy3DDelightModel", "inputs": {
                "model": "hunyuan3d-delight-v2-0"}},
        "3":  {"class_type": "Hy3DGenerateMesh", "inputs": {
                "pipeline": ["2", 0],
                "image": ["1", 0],
                "mask": ["1", 1],
                "guidance_scale": 5.5,
                "steps": 32,
                "seed": 0,
                "scheduler": "FlowMatchEulerDiscreteScheduler",
                "force_offload": True}},
        # Convert latents → trimesh
        "4":  {"class_type": "Hy3DVAEDecode", "inputs": {
                "latents": ["3", 0], "vae": ["2", 1]}},
        # Optional simplification (heavy meshes degrade USDZ readers)
        "5":  {"class_type": "Hy3DFastSimplifyMesh", "inputs": {
                "trimesh": ["4", 0],
                "target_face_num": int(mesh_simplify)}},
        # Save
        "6":  {"class_type": "SaveGLB", "inputs": {
                "mesh": ["5", 0],
                "filename_prefix": f"mangaka-character-hy3d-{safe_name}"}},
    }


# ── manga panel (2-pass: composition → inked refine) ───────────────────────

# ── character → 3D mesh (TripoSR → GLB) ────────────────────────────────────

def character_3d_workflow(
    *,
    name: str,
    input_image_filename: str,
    geometry_resolution: int = 256,
    threshold: float = 0.0,
    triposr_ckpt: str = TRIPOSR_CKPT,
) -> dict[str, Any]:
    """LoadImage → TripoSRModelLoader → TripoSRSampler → SaveGLB.

    `input_image_filename` is the name returned by /upload/image — the
    artist uploads the character design sheet (or a clean single view)
    first, then this workflow turns it into a 3D mesh ComfyUI's SaveGLB
    node exports as a `.glb` file in ComfyUI's output dir.

    GLB → USD conversion happens downstream (Blender / Pixar USD tools)."""
    safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)[:60] or "character"
    return {
        "1":  {"class_type": "LoadImage", "inputs": {"image": input_image_filename}},
        "2":  {"class_type": "TripoSRModelLoader", "inputs": {
                "model": triposr_ckpt, "chunk_size": 8192}},
        "3":  {"class_type": "TripoSRSampler", "inputs": {
                "model": ["2", 0],
                "reference_image": ["1", 0],
                "reference_mask": ["1", 1],
                "geometry_resolution": int(geometry_resolution),
                "threshold": float(threshold)}},
        "4":  {"class_type": "SaveGLB", "inputs": {
                "mesh": ["3", 0],
                "filename_prefix": f"mangaka-character-3d-{safe_name}"}},
    }


# ── stable panel: img2img from character reference ─────────────────────────

def panel_stable_workflow(
    *,
    panel_rkey: str = "panel",
    reference_image_filename: str,
    framing: str = "medium",
    characters: Iterable[str] = (),
    environment: str = "",
    mood: str = "",
    action: str = "",
    base_denoise: float = 0.5,
    refine_denoise: float = 0.35,
    base_steps: int = 28,
    refine_steps: int = 18,
    base_cfg: float = 7.5,
    refine_cfg: float = 6.5,
    width: int = 1024,
    height: int = 1536,
    seed: int = 0,
    ckpt: str = DEFAULT_CKPT,
) -> dict[str, Any]:
    """Character-stable panel via img2img + 2-pass refine.

    Pipeline:
      LoadImage(ref) → VAEEncode → KSampler #1 (composition, denoise=0.65, new
      prompt: framing + action) → KSampler #2 (inked refine, denoise=0.4) →
      Decode + Save both stages.

    The character's color palette, silhouette and style carry through from
    the encoded latent — significantly more stable than a from-scratch run
    when you want the *same* character across panels."""
    framing_hint = _FRAMING_HINT.get(framing.lower(), framing or "medium shot")
    char_clause = (", ".join(characters)) if characters else "scene"
    composition_prompt = ", ".join(p for p in [
        f"manga panel composition, {framing_hint}",
        f"characters: {char_clause}" if characters else "",
        f"environment: {environment}" if environment else "",
        f"mood: {mood}" if mood else "",
        f"action: {action}" if action else "",
        "dramatic lighting, dynamic perspective, cinematic framing",
    ] if p)
    ink_prompt = ", ".join(p for p in [
        f"manga inked panel, {framing_hint}",
        f"characters: {char_clause}" if characters else "",
        "sharp black ink lines, screentone, hatching, high contrast monochrome",
        "shonen-jump style, dynamic composition",
    ] if p)
    safe_rkey = "".join(c if c.isalnum() or c in "-_" else "-" for c in panel_rkey)[:60] or "panel"
    real_seed = int(seed) or secrets.randbelow(2**32)
    return {
        "1":  {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2":  {"class_type": "LoadImage", "inputs": {"image": reference_image_filename}},
        "3":  {"class_type": "VAEEncode", "inputs": {
                "pixels": ["2", 0], "vae": ["1", 2]}},
        "4":  {"class_type": "CLIPTextEncode", "inputs": {"text": composition_prompt, "clip": ["1", 1]}},
        "5":  {"class_type": "CLIPTextEncode", "inputs": {"text": NEG_DEFAULT, "clip": ["1", 1]}},
        "6":  {"class_type": "KSampler", "inputs": {
                "seed": real_seed, "steps": int(base_steps), "cfg": float(base_cfg),
                "sampler_name": "euler", "scheduler": "normal",
                "denoise": float(base_denoise),
                "model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0],
                "latent_image": ["3", 0]}},
        "7":  {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        "8":  {"class_type": "SaveImage", "inputs": {
                "filename_prefix": f"mangaka-panel-stable-{safe_rkey}-composition",
                "images": ["7", 0]}},
        "9":  {"class_type": "CLIPTextEncode", "inputs": {"text": ink_prompt, "clip": ["1", 1]}},
        "10": {"class_type": "CLIPTextEncode", "inputs": {
                "text": "color photograph, " + NEG_DEFAULT, "clip": ["1", 1]}},
        "11": {"class_type": "KSampler", "inputs": {
                "seed": real_seed, "steps": int(refine_steps), "cfg": float(refine_cfg),
                "sampler_name": "dpmpp_2m", "scheduler": "karras",
                "denoise": float(refine_denoise),
                "model": ["1", 0], "positive": ["9", 0], "negative": ["10", 0],
                "latent_image": ["6", 0]}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["1", 2]}},
        "13": {"class_type": "SaveImage", "inputs": {
                "filename_prefix": f"mangaka-panel-stable-{safe_rkey}-inked",
                "images": ["12", 0]}},
    }


_FRAMING_HINT = {
    "wide":      "wide establishing shot, dramatic framing",
    "medium":    "medium shot, balanced composition",
    "closeup":   "extreme close-up, intense focus",
    "low-angle": "low angle shot, looking up",
    "high-angle":"high angle shot, looking down",
    "ots":       "over the shoulder shot",
}

def panel_workflow(
    *,
    panel_rkey: str = "panel",
    framing: str = "medium",
    characters: Iterable[str] = (),
    environment: str = "",
    mood: str = "",
    action: str = "",
    base_steps: int = 30,
    refine_steps: int = 20,
    base_cfg: float = 8.0,
    refine_cfg: float = 7.0,
    refine_denoise: float = 0.45,
    base_sampler: str = DEFAULT_SAMPLER,
    refine_sampler: str = DEFAULT_SAMPLER,
    base_scheduler: str = DEFAULT_SCHEDULER,
    refine_scheduler: str = DEFAULT_SCHEDULER,
    seed: int = 0,
    width: int = 1024,
    height: int = 1536,
    ckpt: str = DEFAULT_CKPT,
    negative_composition: str = NEG_DEFAULT,
    negative_ink: str = "color photograph, " + NEG_DEFAULT,
) -> dict[str, Any]:
    """Two-pass panel generator: KSampler base (denoise=1.0) → KSampler refine
    (denoise=0.4) sharing model/CLIP/VAE/latent_image graph from the first
    pass so structure carries through. Saves both stages as separate PNGs."""
    framing_hint = _FRAMING_HINT.get(framing.lower(), framing or "medium shot")
    char_clause = (", ".join(characters)) if characters else "scene"
    composition_prompt = ", ".join(p for p in [
        f"manga panel composition, {framing_hint}",
        f"characters: {char_clause}" if characters else "",
        f"environment: {environment}" if environment else "",
        f"mood: {mood}" if mood else "",
        f"action: {action}" if action else "",
        "dramatic lighting, dynamic perspective, cinematic framing",
    ] if p)
    ink_prompt = ", ".join(p for p in [
        f"manga inked panel, {framing_hint}",
        f"characters: {char_clause}" if characters else "",
        "sharp black ink lines, screentone, hatching, high contrast monochrome,",
        "shonen-jump style, dynamic composition",
    ] if p)
    safe_rkey = "".join(c if c.isalnum() or c in "-_" else "-" for c in panel_rkey)[:60] or "panel"
    real_seed = int(seed) or secrets.randbelow(2**32)
    return {
        "1":  {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2":  {"class_type": "EmptyLatentImage", "inputs": {
                "width": int(width), "height": int(height), "batch_size": 1}},
        "3":  {"class_type": "CLIPTextEncode", "inputs": {"text": composition_prompt, "clip": ["1", 1]}},
        "4":  {"class_type": "CLIPTextEncode", "inputs": {"text": negative_composition, "clip": ["1", 1]}},
        "5":  {"class_type": "KSampler", "inputs": {
                "seed": real_seed, "steps": int(base_steps), "cfg": float(base_cfg),
                "sampler_name": base_sampler, "scheduler": base_scheduler, "denoise": 1.0,
                "model": ["1", 0], "positive": ["3", 0], "negative": ["4", 0],
                "latent_image": ["2", 0]}},
        "6":  {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7":  {"class_type": "SaveImage", "inputs": {
                "filename_prefix": f"mangaka-panel-{safe_rkey}-composition", "images": ["6", 0]}},
        "9":  {"class_type": "CLIPTextEncode", "inputs": {"text": ink_prompt, "clip": ["1", 1]}},
        "10": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_ink, "clip": ["1", 1]}},
        "11": {"class_type": "KSampler", "inputs": {
                "seed": real_seed, "steps": int(refine_steps), "cfg": float(refine_cfg),
                "sampler_name": refine_sampler, "scheduler": refine_scheduler,
                "denoise": float(refine_denoise),
                "model": ["1", 0], "positive": ["9", 0], "negative": ["10", 0],
                "latent_image": ["5", 0]}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["1", 2]}},
        "13": {"class_type": "SaveImage", "inputs": {
                "filename_prefix": f"mangaka-panel-{safe_rkey}-inked", "images": ["12", 0]}},
    }
