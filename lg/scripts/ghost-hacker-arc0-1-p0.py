"""ghost-hacker-arc0-1-p0: generate page 0 (pre-title hook, 4 panels) of
Arc 0-1 「パスワードは覚えるな」 via Flux + PuLID, then PIL-composite into
a manga page with SFX text + dialogue speech bubbles.

Reads:
  - resources/episodes/arc0-1-origin/image-gen-manifest.json (panel prompts)
  - resources/characters/Yuto/{profile.jsonld,reference.png}
  - resources/characters/Yuto/reference.prompt.txt (anime ref style)

Submits 4 panels to the LAN ComfyUI directly (bypassing LangGraph for
control). Each panel: Flux.1 [dev] Q4_K_S GGUF + PuLID Flux v0.9.1 with
Yuto's reference.png for face identity. Saved to ComfyUI's output dir
with filenames `ghost-hacker-arc0-1-p0-panel{N}.png` so they show up in
the Media Assets panel.

After all 4 land, composites them onto an A4 page canvas with PIL +
SFX text + Japanese dialogue bubbles. Saves to /tmp/gh-arc0-1-p0.png
and uploads back to ComfyUI's input dir for convenience.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path("/Users/junkawasaki/etzhayyim/etzhayyim-root/60-apps/etzhayyim-project-mangaka/data/ghosthacker")
COMFY = "http://192.168.1.70:8188"
EPISODE_DIR = ROOT / "resources/episodes/arc0-1-origin"
CHARS = ROOT / "resources/characters"

# -- helpers ----------------------------------------------------------------

def _http(method: str, path: str, body: bytes | None = None, headers: dict | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(f"{COMFY}{path}", data=body, method=method,
                                 headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, r.read()


def _post_json(path: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    s, b = _http("POST", path, body, {"content-type": "application/json"})
    return json.loads(b)


def upload_image(local_path: Path, hint: str) -> str:
    """POST /upload/image. Returns the filename ComfyUI assigned."""
    data = local_path.read_bytes()
    boundary = "----gh_arc01"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{hint}.png"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode() + data + (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="type"\r\n\r\n'
        f"input\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    s, b = _http("POST", "/upload/image", body,
                 {"content-type": f"multipart/form-data; boundary={boundary}"})
    j = json.loads(b)
    return j["name"]


def submit_workflow(workflow: dict) -> str:
    j = _post_json("/prompt", {"prompt": workflow})
    return j["prompt_id"]


def wait_for(prompt_id: str, timeout_s: int = 600) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        s, b = _http("GET", f"/history/{prompt_id}")
        j = json.loads(b)
        entry = j.get(prompt_id)
        if entry and entry.get("outputs"):
            return entry
        time.sleep(3)
    raise TimeoutError(f"history poll deadline {timeout_s}s")


def fetch_view(filename: str, subfolder: str = "", typ: str = "output") -> bytes:
    q = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": typ})
    s, b = _http("GET", f"/view?{q}")
    return b


# -- workflow builder (Flux + PuLID) ---------------------------------------

def build_workflow(*, ref_filename: str, panel_idx: int, prompt: str,
                   seed: int, width: int = 832, height: int = 1216) -> dict:
    return {
        "1":  {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "flux1-dev-Q4_K_S.gguf"}},
        "2":  {"class_type": "DualCLIPLoader", "inputs": {
                 "clip_name1": "t5xxl_fp8_e4m3fn.safetensors",
                 "clip_name2": "clip_l.safetensors", "type": "flux"}},
        "3":  {"class_type": "VAELoader", "inputs": {"vae_name": "flux_ae.safetensors"}},
        "4":  {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "5":  {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
        "6":  {"class_type": "FluxGuidance", "inputs": {"conditioning": ["4", 0], "guidance": 3.5}},
        "7":  {"class_type": "EmptyLatentImage", "inputs": {
                 "width": width, "height": height, "batch_size": 1}},
        "8":  {"class_type": "ModelSamplingFlux", "inputs": {
                 "model": ["1", 0], "max_shift": 1.15, "base_shift": 0.5,
                 "width": width, "height": height}},
        "20": {"class_type": "LoadImage", "inputs": {"image": ref_filename}},
        "21": {"class_type": "PulidFluxModelLoader", "inputs": {"pulid_file": "pulid_flux_v0.9.1.safetensors"}},
        "22": {"class_type": "PulidFluxEvaClipLoader", "inputs": {}},
        "23": {"class_type": "PulidFluxInsightFaceLoader", "inputs": {"provider": "CPU"}},
        "24": {"class_type": "ApplyPulidFlux", "inputs": {
                 "model": ["8", 0], "pulid_flux": ["21", 0], "eva_clip": ["22", 0],
                 "face_analysis": ["23", 0], "image": ["20", 0],
                 "weight": 0.7, "start_at": 0.0, "end_at": 1.0,
                 "fusion": "mean", "fusion_weight_max": 1.0, "fusion_weight_min": 0.0,
                 "train_step": 1000, "use_gray": True,
                 "attn_mask": None, "prior_image": None}},
        "9":  {"class_type": "KSampler", "inputs": {
                 "seed": seed, "steps": 22, "cfg": 1.0,
                 "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
                 "model": ["24", 0], "positive": ["6", 0], "negative": ["5", 0],
                 "latent_image": ["7", 0]}},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["3", 0]}},
        "11": {"class_type": "SaveImage", "inputs": {
                 "filename_prefix": f"ghost-hacker-arc0-1-p0-panel{panel_idx}",
                 "images": ["10", 0]}},
    }


# -- main pipeline ----------------------------------------------------------

def main() -> int:
    print("=== Ghost Hacker Arc 0-1 Page 0 pipeline ===")

    # 1. Load manifest, character profile, character ref prompt
    manifest = json.loads((EPISODE_DIR / "image-gen-manifest.json").read_text())
    profile = json.loads((CHARS / "Yuto/profile.jsonld").read_text())
    yuto_app = profile.get("gh:appearance", {})

    p0_panels = [p for p in manifest["panels"] if p["pageNum"] == 0]
    print(f"page 0: {len(p0_panels)} panels")

    # 2. Upload Yuto reference image to ComfyUI
    yuto_ref = CHARS / "Yuto/reference.png"
    print(f"uploading Yuto ref: {yuto_ref.name}")
    ref_filename = upload_image(yuto_ref, hint="gh-yuto-ref")
    print(f"  -> {ref_filename}")

    # 3. Build prompt per panel and submit
    char_style = (
        "anime manga inked panel, masterpiece, best quality, sharp black ink lines, "
        "screentone, hatching, monochrome with single dramatic color accent, "
        "cinematic chiaroscuro, shonen-jump style"
    )
    char_desc = (
        "1boy, 16 year old Japanese high school student, "
        f"{yuto_app.get('gh:hair','black short hair')}, "
        f"{yuto_app.get('gh:eyes','brown eyes')}, "
        f"{yuto_app.get('gh:face','expressive face')}"
    )

    submitted: list[dict[str, Any]] = []
    for i, p in enumerate(p0_panels, 1):
        visual = p["visual"]
        shot = p["shot"]
        # Compose Flux prompt: shot type + visual + character + style
        parts = [f"{shot.lower()} shot", visual, char_desc, char_style]
        prompt = ". ".join([x.strip() for x in parts if x.strip()])
        print(f"\npanel {i} ({shot}): submitting...")
        print(f"  visual: {visual[:120]}")
        wf = build_workflow(ref_filename=ref_filename, panel_idx=i,
                            prompt=prompt, seed=4242 + i * 1009)
        pid = submit_workflow(wf)
        print(f"  prompt_id={pid}")
        submitted.append({"panel_idx": i, "shot": shot, "prompt_id": pid, "manifest": p})

    # 4. Poll all
    results: list[dict[str, Any]] = []
    for s in submitted:
        print(f"\nwaiting for panel {s['panel_idx']}...")
        t0 = time.monotonic()
        entry = wait_for(s["prompt_id"], timeout_s=900)
        elapsed = time.monotonic() - t0
        imgs = []
        for nid, out in (entry.get("outputs") or {}).items():
            for img in out.get("images", []):
                imgs.append(img)
        first = imgs[0] if imgs else None
        if first:
            data = fetch_view(first["filename"], first.get("subfolder", ""), first.get("type", "output"))
            local = Path(f"/tmp/gh-arc0-1-p0-panel{s['panel_idx']}.png")
            local.write_bytes(data)
            print(f"  done in {elapsed:.0f}s -> {local} ({len(data)} B), comfy filename: {first['filename']}")
            results.append({**s, "local": str(local), "comfy_file": first["filename"]})
        else:
            print(f"  no images!")
            results.append({**s, "error": "no images"})

    # 5. PIL composite into manga page with SFX + dialogue overlay
    print("\ncompositing page...")
    composite_page(results, p0_panels)
    print("done.")
    return 0


def composite_page(results: list[dict], panels: list[dict]) -> None:
    """A4 portrait manga page with panel layout + SFX text + dialogue bubbles."""
    from PIL import Image, ImageDraw, ImageFont

    # Page dimensions (manga A4 portrait)
    W, H = 1280, 1817
    canvas = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(canvas)

    # 4-panel layout for the pre-title hook:
    # - Top: wide establishing (panel 1, close-up of face)
    # - Mid-left: insert (panel 2, smartphone screen)
    # - Mid-right: medium (panel 3, sitting up)
    # - Bottom: wide title card (panel 4)
    gutter = 16
    layout = [
        {"x": 40,  "y": 40,   "w": 1200, "h": 540},   # p1 close-up
        {"x": 40,  "y": 600,  "w": 580,  "h": 560},   # p2 phone insert
        {"x": 660, "y": 600,  "w": 580,  "h": 560},   # p3 medium sit-up
        {"x": 40,  "y": 1180, "w": 1200, "h": 597},   # p4 wide title
    ]

    # Try to find a usable Japanese font on macOS
    font_paths = [
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Hiragino Mincho ProN.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    font_path = next((f for f in font_paths if os.path.exists(f)), None)
    font_dialog = ImageFont.truetype(font_path, 26) if font_path else ImageFont.load_default()
    font_sfx = ImageFont.truetype(font_path, 48) if font_path else ImageFont.load_default()

    for idx, (slot, panel_data, manifest_p) in enumerate(zip(layout, results, panels), 1):
        if "local" not in panel_data:
            continue
        img = Image.open(panel_data["local"]).convert("RGB")
        bx, by, bw, bh = slot["x"], slot["y"], slot["w"], slot["h"]
        target_w = bw - gutter
        target_h = bh - gutter
        # Resize keeping aspect + crop
        src_ratio = img.width / img.height
        dst_ratio = target_w / target_h
        if src_ratio > dst_ratio:
            new_h = target_h
            new_w = int(round(target_h * src_ratio))
        else:
            new_w = target_w
            new_h = int(round(target_w / src_ratio))
        img = img.resize((new_w, new_h), Image.LANCZOS)
        ox = (new_w - target_w) // 2
        oy = (new_h - target_h) // 2
        img = img.crop((ox, oy, ox + target_w, oy + target_h))
        canvas.paste(img, (bx + gutter // 2, by + gutter // 2))
        draw.rectangle(
            [(bx + gutter // 2, by + gutter // 2),
             (bx + gutter // 2 + target_w - 1, by + gutter // 2 + target_h - 1)],
            outline="black", width=3,
        )

        # SFX overlay for panel 1 (notification sound) and panel 2 (text flood)
        if idx == 1:
            sfx = "ピロン"
            draw.text(
                (bx + target_w - 230, by + 30),
                sfx, font=font_sfx, fill="white",
                stroke_width=4, stroke_fill="black",
            )
        elif idx == 2:
            for j, t in enumerate(["お前マジで最悪", "もう連絡するな", "絶縁"]):
                yy = by + 60 + j * 80
                draw.text((bx + 30, yy), t, font=font_dialog, fill="white",
                          stroke_width=3, stroke_fill="black")

        # Dialogue bubble for panel 3
        for d in (manifest_p.get("dialogues") or []):
            txt = d.get("text") or ""
            if not txt:
                continue
            # Word-wrap and draw a bubble at the bottom of the panel
            bubble_x = bx + 40
            bubble_y = by + target_h - 110
            bubble_w = target_w - 80
            # Draw bubble background (white ellipse)
            draw.rounded_rectangle(
                [bubble_x, bubble_y, bubble_x + bubble_w, bubble_y + 90],
                radius=20, fill="white", outline="black", width=3,
            )
            # Text inside (truncate to 30 chars)
            disp = txt[:30] + ("…" if len(txt) > 30 else "")
            draw.text((bubble_x + 16, bubble_y + 24),
                      disp, font=font_dialog, fill="black")

        # Title text for panel 4
        if idx == 4:
            title = "Ghost Hacker #00"
            subtitle = "「パスワードは覚えるな」"
            # White title text with shadow
            draw.text((bx + 60, by + target_h // 2 - 60),
                      title, font=font_sfx, fill="white",
                      stroke_width=4, stroke_fill="black")
            draw.text((bx + 60, by + target_h // 2 + 10),
                      subtitle, font=font_sfx, fill="white",
                      stroke_width=4, stroke_fill="black")

    out_path = "/tmp/gh-arc0-1-p0.png"
    canvas.save(out_path, "PNG", optimize=True)
    print(f"page saved -> {out_path}")
    # Also upload back to ComfyUI input/ so it shows up in MediaAssets
    fn = upload_image(Path(out_path), hint="gh-arc0-1-p0-composite")
    print(f"uploaded composite to ComfyUI as: {fn}")


if __name__ == "__main__":
    sys.exit(main())
