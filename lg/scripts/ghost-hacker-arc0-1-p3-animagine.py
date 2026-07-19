"""ghost-hacker-arc0-1-p3 (Animagine XL): re-generate page 3 with the
SDXL Animagine XL 4.0 model + IPAdapter Plus FaceID v2 for stable
character identity. SDXL is ~5x faster than Flux on this hardware
(28s/panel typical) so a 7-panel page lands in ~3-4 minutes total.

Animagine XL is tag-style prompt friendly. We blend:
  - the character's reference.prompt.txt (1boy / 1girl + appearance tags)
  - the manifest's `visual` description (natural language, gets parsed OK)
  - a manga-ink style anchor

Per-panel IPAdapter FaceID PlusV2 with weight=0.85 pulls the face from
the character's reference.png. Reference image is uploaded once per
character and reused across panels.
"""

from __future__ import annotations

import json, os, sys, time, urllib.parse, urllib.request
from pathlib import Path
from typing import Any

ROOT = Path("/Users/junkawasaki/etzhayyim/etzhayyim-root/60-apps/etzhayyim-project-mangaka/data/ghosthacker")
COMFY = "http://192.168.1.70:8188"
EPISODE = ROOT / "resources/episodes/arc0-1-origin"
CHARS = ROOT / "resources/characters"
PAGE = 3


def _http(method: str, path: str, body: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(f"{COMFY}{path}", data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, r.read()


def upload_image(local: Path, hint: str) -> str:
    data = local.read_bytes()
    boundary = "----animagine_p3"
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


def submit(workflow: dict) -> str:
    s, b = _http("POST", "/prompt",
                 json.dumps({"prompt": workflow}).encode(),
                 {"content-type": "application/json"})
    return json.loads(b)["prompt_id"]


def wait_for(pid: str, timeout: int = 600) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s, b = _http("GET", f"/history/{pid}")
        j = json.loads(b)
        e = j.get(pid)
        if e and (e.get("outputs") or e.get("status", {}).get("status_str") == "error"):
            return e
        time.sleep(2)
    return {}


def fetch_view(filename: str, subfolder: str = "", typ: str = "output") -> bytes:
    q = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": typ})
    s, b = _http("GET", f"/view?{q}")
    return b


# -- prompt construction --------------------------------------------------

def char_tag_prompt(name: str) -> str:
    """Read reference.prompt.txt and strip the positive section only
    (everything before '--- negative ---'). Drop the 'character reference
    sheet, full body shot, plain white background, clean line art,
    T-pose or relaxed standing pose, neutral expression' fragments that
    would conflict with panel-specific framing."""
    raw = (CHARS / name / "reference.prompt.txt").read_text(encoding="utf-8")
    pos = raw.split("--- negative ---")[0].strip()
    # Drop reference-sheet tags that fight the panel framing
    drop = [
        "character reference sheet", "T-pose or relaxed standing pose",
        "full body shot", "plain white background", "clean line art",
        "neutral expression",
    ]
    tags = [t.strip() for t in pos.split(",")]
    tags = [t for t in tags if not any(d in t for d in drop)]
    return ", ".join(t for t in tags if t)


NEG_DEFAULT = (
    "low quality, worst quality, normal quality, lowres, blurry, deformed, "
    "extra fingers, bad anatomy, malformed hands, bad proportions, "
    "photograph, photorealistic, 3d render, multiple panels, collage, "
    "comic page, wings, nsfw, logo, watermark, signature, text overlay"
)


def build_workflow(*, ref_filename: str, panel_idx: int, prompt: str,
                   seed: int, width: int = 832, height: int = 1216) -> dict:
    """Animagine XL 4.0 + IPAdapter Plus FaceID v2 + KSampler.
    Single-pass from empty latent so the face injection drives the panel
    composition without losing the manga line work."""
    return {
        # Base SDXL model
        "1":  {"class_type": "CheckpointLoaderSimple",
               "inputs": {"ckpt_name": "animagine-xl-4.0.safetensors"}},
        "2":  {"class_type": "LoadImage", "inputs": {"image": ref_filename}},
        "3":  {"class_type": "EmptyLatentImage", "inputs": {
                 "width": width, "height": height, "batch_size": 1}},
        "4":  {"class_type": "CLIPTextEncode",
               "inputs": {"text": prompt, "clip": ["1", 1]}},
        "5":  {"class_type": "CLIPTextEncode",
               "inputs": {"text": NEG_DEFAULT, "clip": ["1", 1]}},

        # IPAdapter Plus FaceID v2 — face identity from ref.
        # The Unified loader auto-picks the right adapter + clip vision
        # for the "PLUS FACEID PROVIDED" preset and loads the LoRA.
        "10": {"class_type": "IPAdapterUnifiedLoaderFaceID", "inputs": {
                 "model": ["1", 0],
                 "preset": "FACEID PLUS V2",
                 "lora_strength": 0.6,
                 "provider": "CPU",
                 "weight_v2": True}},
        "11": {"class_type": "IPAdapterFaceID", "inputs": {
                 "model": ["10", 0],
                 "ipadapter": ["10", 1],
                 "image": ["2", 0],
                 "weight": 0.75,
                 "weight_faceidv2": 0.85,
                 "weight_type": "linear",
                 "combine_embeds": "concat",
                 "start_at": 0.0, "end_at": 1.0,
                 "embeds_scaling": "V only"}},

        # Sampling (Animagine recommended: dpmpp_2m_sde + karras, cfg 5-7)
        "20": {"class_type": "KSampler", "inputs": {
                 "seed": seed, "steps": 28, "cfg": 6.0,
                 "sampler_name": "dpmpp_2m_sde", "scheduler": "karras",
                 "denoise": 1.0,
                 "model": ["11", 0],
                 "positive": ["4", 0], "negative": ["5", 0],
                 "latent_image": ["3", 0]}},
        "21": {"class_type": "VAEDecode",
               "inputs": {"samples": ["20", 0], "vae": ["1", 2]}},
        "22": {"class_type": "SaveImage", "inputs": {
                 "filename_prefix": f"ghost-hacker-arc0-1-p{PAGE}-anim-panel{panel_idx}",
                 "images": ["21", 0]}},
    }


def main() -> int:
    print(f"=== Ghost Hacker Arc 0-1 Page {PAGE} (Animagine XL pipeline) ===")
    manifest = json.loads((EPISODE / "image-gen-manifest.json").read_text())
    panels = [p for p in manifest["panels"] if p["pageNum"] == PAGE]
    print(f"page {PAGE}: {len(panels)} panels")

    # Upload Ren + Nei refs
    refs: dict[str, str] = {}
    for name in ("Ren", "Nei"):
        refs[name] = upload_image(CHARS / name / "reference.png", hint=f"gh-anim-{name.lower()}")
        print(f"uploaded {name} -> {refs[name]}")

    # Per-character tag prompt cache
    tag_cache = {n: char_tag_prompt(n) for n in ("Ren", "Nei")}

    style = "manga inked panel, sharp ink lines, screentone, monochrome with subtle color accent, cinematic chiaroscuro, shonen-jump style"

    # Submit all panels
    submitted: list[dict[str, Any]] = []
    for p in panels:
        idx = p["panelIndex"]
        focused = (p.get("focusedCharacters") or [None])[0] or "Ren"
        ref = refs.get(focused, refs["Ren"])
        tags = tag_cache.get(focused, tag_cache["Ren"])

        # Hybrid prompt: tag-style for character + natural-language for scene + style
        scene = p["visual"]
        shot = p["shot"].lower()
        prompt = ", ".join([
            tags,
            f"({shot} shot)",
            scene,
            style,
        ])

        print(f"\npanel {idx} ({p['shot']}) -> {focused}")
        print(f"  scene: {scene[:120]}")
        wf = build_workflow(ref_filename=ref, panel_idx=idx,
                            prompt=prompt, seed=11000 + idx * 1009)
        pid = submit(wf)
        submitted.append({"panel_idx": idx, "prompt_id": pid, "focused": focused, "manifest": p})
        print(f"  prompt_id={pid}")

    # Poll + fetch
    results: list[dict[str, Any]] = []
    for s in submitted:
        print(f"\nwaiting panel {s['panel_idx']} ({s['focused']})...")
        t0 = time.monotonic()
        e = wait_for(s["prompt_id"], timeout=600)
        el = time.monotonic() - t0
        outs = e.get("outputs") or {}
        if not outs:
            err = e.get("status", {}).get("messages")
            print(f"  FAIL ({el:.0f}s) status={e.get('status',{}).get('status_str')}")
            results.append({**s, "error": "no outputs"})
            continue
        for nid, out in outs.items():
            for img in out.get("images", []):
                data = fetch_view(img["filename"])
                local = Path(f"/tmp/gh-arc0-1-p{PAGE}-anim-panel{s['panel_idx']}.png")
                local.write_bytes(data)
                print(f"  done in {el:.0f}s -> {local} ({len(data)} B)")
                results.append({**s, "local": str(local), "comfy": img["filename"]})
                break

    # Composite
    print("\ncompositing...")
    composite(results, panels)
    return 0


def composite(results, panels):
    from PIL import Image, ImageDraw, ImageFont

    W, H = 1280, 1817
    margin = 40
    gutter = 16
    canvas = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(canvas)

    row1_y, row1_h = margin, 540
    slots = [
        (margin,                          row1_y, 280, row1_h),
        (margin+280+gutter,               row1_y, 540, row1_h),
        (margin+280+gutter+540+gutter,    row1_y, 1200-280-540-2*gutter, row1_h),
    ]
    row2_y = row1_y + row1_h + gutter
    row2_h = 540
    slots += [
        (margin,                          row2_y, 720, row2_h),
        (margin+720+gutter,               row2_y, 1200-720-gutter, row2_h),
    ]
    row3_y = row2_y + row2_h + gutter
    row3_h = 597
    slots += [
        (margin,                          row3_y, 480, row3_h),
        (margin+480+gutter,               row3_y, 1200-480-gutter, row3_h),
    ]

    font_paths = [
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Hiragino Mincho ProN.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ]
    fp = next((f for f in font_paths if os.path.exists(f)), None)
    f_dlg = ImageFont.truetype(fp, 20) if fp else ImageFont.load_default()
    f_spk = ImageFont.truetype(fp, 16) if fp else ImageFont.load_default()

    for slot, mp in zip(slots, panels):
        idx = mp["panelIndex"]
        local = Path(f"/tmp/gh-arc0-1-p{PAGE}-anim-panel{idx}.png")
        if not local.exists():
            continue
        bx, by, bw, bh = slot
        tw, th = bw - gutter, bh - gutter
        img = Image.open(local).convert("RGB")
        sr, dr = img.width / img.height, tw / th
        if sr > dr:
            nh = th; nw = int(round(th * sr))
        else:
            nw = tw; nh = int(round(tw / sr))
        img = img.resize((nw, nh), Image.LANCZOS)
        ox = (nw - tw) // 2; oy = (nh - th) // 2
        img = img.crop((ox, oy, ox + tw, oy + th))
        canvas.paste(img, (bx + gutter // 2, by + gutter // 2))
        draw.rectangle(
            [(bx + gutter // 2, by + gutter // 2),
             (bx + gutter // 2 + tw - 1, by + gutter // 2 + th - 1)],
            outline="black", width=3,
        )
        for i, d in enumerate(mp.get("dialogues") or []):
            text = d.get("text") or ""
            speaker = d.get("speaker") or ""
            if not text: continue
            disp = text if len(text) <= 18 else text[:18] + "…"
            bw_b = min(tw - 30, 360)
            bh_b = 56
            bx_b = bx + 15
            by_b = by + th - 70 - (i * (bh_b + 6))
            draw.rounded_rectangle(
                [bx_b, by_b, bx_b + bw_b, by_b + bh_b],
                radius=12, fill="white", outline="black", width=2,
            )
            draw.text((bx_b + 10, by_b + 4), speaker, font=f_spk, fill="#666")
            draw.text((bx_b + 10, by_b + 24), disp, font=f_dlg, fill="black")

    out_path = f"/tmp/gh-arc0-1-p{PAGE}-anim.png"
    canvas.save(out_path, "PNG", optimize=True)
    print(f"page saved -> {out_path}")
    # Upload back
    data = Path(out_path).read_bytes()
    boundary = "----compose_anim_p3"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="gh-arc0-1-p3-anim-composite.png"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode() + data + (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="type"\r\n\r\n'
        f"input\r\n--{boundary}--\r\n"
    ).encode()
    req = urllib.request.Request(f"{COMFY}/upload/image", data=body, method="POST",
                                  headers={"content-type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        j = json.loads(r.read())
        print(f"uploaded composite -> {j['name']}")


if __name__ == "__main__":
    sys.exit(main())
