"""ghost-hacker-arc0-1-p3: generate page 3 (Ren + Nei walking, 7 panels)
of Arc 0-1 via Flux + PuLID with PER-PANEL character ref selection +
PIL composite with 3-row classic manga layout + dialogue bubbles.

Layout (left->right, top->bottom, each row a horizontal strip):

  row 1: [p1 OTS small Nei] [p2 Medium Nei]   [p3 CU small Ren]
  row 2: [p4 Medium medium Nei]              [p5 CU small Ren]
  row 3: [p6 Medium medium Nei]              [p7 CU large Nei]

Each panel uses the appropriate PuLID reference image based on
`focusedCharacters[0]` from the manifest. Nei -> Nei/reference.png,
Ren -> Ren/reference.png. PuLID weight 0.7.
"""

from __future__ import annotations

import base64
import io
import json
import os
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
PAGE_NUM = 3


# -- HTTP helpers ----------------------------------------------------------

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
    data = local_path.read_bytes()
    boundary = "----gh_arc01_p3"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{hint}.png"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode() + data + (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="type"\r\n\r\n'
        f"input\r\n--{boundary}--\r\n"
    ).encode()
    s, b = _http("POST", "/upload/image", body,
                 {"content-type": f"multipart/form-data; boundary={boundary}"})
    return json.loads(b)["name"]


def submit_workflow(workflow: dict) -> str:
    j = _post_json("/prompt", {"prompt": workflow})
    return j["prompt_id"]


def wait_for(prompt_id: str, timeout_s: int = 900) -> dict:
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


# -- workflow builder ------------------------------------------------------

def build_workflow(*, ref_filename: str, panel_idx: int, prompt: str,
                   seed: int, width: int = 832, height: int = 1216,
                   pulid_weight: float = 0.7) -> dict:
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
                 "weight": pulid_weight, "start_at": 0.0, "end_at": 1.0,
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
                 "filename_prefix": f"ghost-hacker-arc0-1-p{PAGE_NUM}-panel{panel_idx}",
                 "images": ["10", 0]}},
    }


# -- per-character profile / style descriptors ----------------------------

def char_desc(name: str) -> str:
    profile = json.loads((CHARS / name / "profile.jsonld").read_text())
    a = profile.get("gh:appearance", {})
    role = profile.get("schema:role", "")
    return ", ".join([
        ("1girl" if "female" in (a.get("gh:face","").lower()) else "1boy"),
        f"{profile.get('schema:age','17')} year old",
        a.get("gh:face", ""),
        a.get("gh:hair", ""),
        a.get("gh:eyes", ""),
        a.get("gh:build", "") if a.get("gh:build") else "",
    ])


# -- main pipeline ---------------------------------------------------------

def main() -> int:
    print(f"=== Ghost Hacker Arc 0-1 Page {PAGE_NUM} pipeline ===")

    manifest = json.loads((EPISODE_DIR / "image-gen-manifest.json").read_text())
    panels = [p for p in manifest["panels"] if p["pageNum"] == PAGE_NUM]
    print(f"page {PAGE_NUM}: {len(panels)} panels")

    # Upload Ren + Nei reference images once
    refs: dict[str, str] = {}
    for name in ("Ren", "Nei"):
        p = CHARS / name / "reference.png"
        print(f"uploading {name} ref...")
        refs[name] = upload_image(p, hint=f"gh-{name.lower()}-ref")
        print(f"  -> {refs[name]}")

    # Build per-character description cache
    char_descriptions = {n: char_desc(n) for n in ("Ren", "Nei", "Yuto")}

    style = (
        "anime manga inked panel, masterpiece, best quality, sharp black ink lines, "
        "screentone, hatching, monochrome with subtle color accent, "
        "cinematic chiaroscuro, shonen-jump style"
    )

    # Submit all 7 panels
    submitted: list[dict[str, Any]] = []
    for i, p in enumerate(panels, 1):
        focused = (p.get("focusedCharacters") or [None])[0] or "Ren"
        ref = refs.get(focused) or refs["Ren"]
        cdesc = char_descriptions.get(focused, char_descriptions["Ren"])
        prompt_parts = [
            f"{p['shot'].lower()} shot",
            p["visual"],
            cdesc,
            style,
        ]
        prompt = ". ".join([x.strip() for x in prompt_parts if x and x.strip()])
        layout = p.get("panelLayout", {})
        print(f"\npanel {i} ({p['shot']}, row{layout.get('gh:row')}, {layout.get('gh:size')}) -> {focused}")
        print(f"  visual: {p['visual'][:120]}")
        wf = build_workflow(ref_filename=ref, panel_idx=i,
                            prompt=prompt, seed=7777 + i * 1009)
        pid = submit_workflow(wf)
        submitted.append({"panel_idx": i, "prompt_id": pid, "focused": focused, "manifest": p})
        print(f"  prompt_id={pid}")

    # Poll each
    results: list[dict[str, Any]] = []
    for s in submitted:
        print(f"\nwaiting for panel {s['panel_idx']} ({s['focused']})...")
        t0 = time.monotonic()
        entry = wait_for(s["prompt_id"], timeout_s=900)
        elapsed = time.monotonic() - t0
        imgs = [img for nid, out in (entry.get("outputs") or {}).items()
                for img in out.get("images", [])]
        if imgs:
            first = imgs[0]
            data = fetch_view(first["filename"], first.get("subfolder",""), first.get("type","output"))
            local = Path(f"/tmp/gh-arc0-1-p{PAGE_NUM}-panel{s['panel_idx']}.png")
            local.write_bytes(data)
            print(f"  done in {elapsed:.0f}s -> {local} ({len(data)} B)")
            results.append({**s, "local": str(local)})
        else:
            print("  no images!")
            results.append({**s, "error": "no images"})

    # Composite
    print("\ncompositing page 3...")
    composite_page_3(results, panels)
    return 0


# -- composite (3-row layout, dialogue bubbles) ----------------------------

def composite_page_3(results: list[dict], panels: list[dict]) -> None:
    from PIL import Image, ImageDraw, ImageFont

    W, H = 1280, 1817
    margin = 40
    gutter = 16
    canvas = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(canvas)

    # Layout: 3 rows, varying column widths per the manifest layout sizes.
    # Row heights: top 540 / mid 540 / bot 597 (total 1677 + 2 gutters)
    inner_w = W - 2 * margin                       # 1200
    # Row 1: [small 280, medium 540, small 380] = 1200
    row1_y = margin
    row1_h = 540
    slots_r1 = [
        (margin,            row1_y, 280, row1_h),  # p1 small
        (margin+280+gutter, row1_y, 540, row1_h),  # p2 medium
        (margin+280+gutter+540+gutter, row1_y, 1200-280-540-2*gutter, row1_h),  # p3 small
    ]

    row2_y = row1_y + row1_h + gutter              # 596
    row2_h = 540
    slots_r2 = [
        (margin,            row2_y, 720, row2_h),  # p4 medium-large
        (margin+720+gutter, row2_y, 1200-720-gutter, row2_h),  # p5 small
    ]

    row3_y = row2_y + row2_h + gutter              # 1152
    row3_h = 597
    slots_r3 = [
        (margin,            row3_y, 480, row3_h),  # p6 medium
        (margin+480+gutter, row3_y, 1200-480-gutter, row3_h),  # p7 large
    ]

    slots = slots_r1 + slots_r2 + slots_r3
    assert len(slots) == 7, f"expected 7 slots, got {len(slots)}"

    # Pick a usable Japanese font
    font_paths = [
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Hiragino Mincho ProN.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    font_path = next((f for f in font_paths if os.path.exists(f)), None)
    font_dialog = ImageFont.truetype(font_path, 22) if font_path else ImageFont.load_default()
    font_speaker = ImageFont.truetype(font_path, 18) if font_path else ImageFont.load_default()

    for slot, r, mp in zip(slots, results, panels):
        if "local" not in r:
            continue
        bx, by, bw, bh = slot
        target_w = bw - gutter
        target_h = bh - gutter
        img = Image.open(r["local"]).convert("RGB")

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

        # Dialogue bubble(s) — stack at bottom of panel
        dialogues = mp.get("dialogues") or []
        for i, d in enumerate(dialogues):
            text = d.get("text") or ""
            speaker = d.get("speaker") or ""
            if not text:
                continue
            # Truncate
            disp = text if len(text) <= 22 else text[:22] + "…"
            spk = f"{speaker}: " if speaker else ""

            # Bubble dims
            bubble_w = target_w - 50
            bubble_h = 50
            bubble_x = bx + 25
            # Stack from bottom upward
            bubble_y = by + target_h - 70 - (i * (bubble_h + 8))

            draw.rounded_rectangle(
                [bubble_x, bubble_y, bubble_x + bubble_w, bubble_y + bubble_h],
                radius=14, fill="white", outline="black", width=2,
            )
            draw.text((bubble_x + 12, bubble_y + 6),
                      spk, font=font_speaker, fill="#666")
            draw.text((bubble_x + 12, bubble_y + 24),
                      disp, font=font_dialog, fill="black")

    out_path = f"/tmp/gh-arc0-1-p{PAGE_NUM}.png"
    canvas.save(out_path, "PNG", optimize=True)
    print(f"page saved -> {out_path}")
    fn = upload_image(Path(out_path), hint=f"gh-arc0-1-p{PAGE_NUM}-composite")
    print(f"uploaded composite to ComfyUI as: {fn}")


if __name__ == "__main__":
    sys.exit(main())
