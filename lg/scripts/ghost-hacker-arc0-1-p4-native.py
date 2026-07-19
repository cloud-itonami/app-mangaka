"""page 4 v10: panel 3 = generated "hands holding smartphone" + PIL EC
mockup composited onto the screen with rotation. Cinematic POV-style."""
import json, sys, time, urllib.parse, urllib.request
from pathlib import Path

COMFY = "http://192.168.1.70:8188"
ROOT = Path("/Users/junkawasaki/etzhayyim/etzhayyim-root/60-apps/etzhayyim-project-mangaka/data/ghosthacker")
EPISODE = ROOT / "resources/episodes/arc0-1-origin"
CHARS = ROOT / "resources/characters"
PAGE = 4


def _http(method, path, body=None, headers=None):
    req = urllib.request.Request(f"{COMFY}{path}", data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status, r.read()


def submit(wf):
    s, b = _http("POST", "/prompt", json.dumps({"prompt": wf}).encode(),
                 {"content-type": "application/json"})
    j = json.loads(b)
    if j.get("node_errors"):
        print(f"node_errors: {json.dumps(j['node_errors'], indent=2)[:2000]}")
    return j["prompt_id"]


def wait_for(pid, timeout=1800):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s, b = _http("GET", f"/history/{pid}")
        e = (json.loads(b) or {}).get(pid)
        if e and (e.get("outputs") or e.get("status", {}).get("status_str") in ("error", "success")):
            return e
        time.sleep(3)
    return {}


def fetch_view(filename):
    q = urllib.parse.urlencode({"filename": filename, "subfolder": "", "type": "output"})
    s, b = _http("GET", f"/view?{q}")
    return b


def char_tag_prompt(name, with_lora_token=False):
    raw = (CHARS / name / "reference.prompt.txt").read_text()
    pos = raw.split("--- negative ---")[0].strip()
    drop = ["character reference sheet", "T-pose or relaxed standing pose",
            "full body shot", "plain white background", "clean line art",
            "neutral expression"]
    tags = [t.strip() for t in pos.split(",")]
    out = [t for t in tags if t and not any(d in t for d in drop)]
    if with_lora_token:
        out.insert(0, f"{name.lower()}_persona")
    return ", ".join(out)


NEG = ("low quality, worst quality, normal quality, lowres, blurry, deformed, "
       "extra fingers, bad anatomy, malformed hands, bad proportions, "
       "photograph, photorealistic, 3d render, multiple panels, collage, "
       "comic page, wings, nsfw, logo, watermark, signature, text overlay, "
       "laptop, desktop computer, headless body")

_KATAKANA_SUBS = {
    "Akira": "アキラ", "Yuto": "ユウト", "Ren": "レン", "Nei": "ネイ",
    "Mei": "メイ", "Saki": "サキ", "nue": "ヌエ",
}
def latinize(s):
    for en, kat in _KATAKANA_SUBS.items():
        s = s.replace(en, kat)
    return s


LAYOUT = [
    {"x": 40,  "y": 40,   "w": 1200, "h": 380, "idx": 1, "bleed": False, "aspect": "landscape"},
    {"x": 40,  "y": 440,  "w": 580,  "h": 460, "idx": 2, "bleed": False, "aspect": "portrait"},
    {"x": 660, "y": 440,  "w": 580,  "h": 460, "idx": 3, "bleed": False, "aspect": "portrait"},
    {"x": 0,   "y": 920,  "w": 1280, "h": 897, "idx": 4, "bleed": True,  "aspect": "portrait"},
]

PANEL_PROMPTS = {
    1: ("wide establishing shot of a 16-year-old Japanese high school boy "
        "sitting on the floor of his bedroom at night, knees pulled up, "
        "holding a smartphone in both hands, full body visible from waist "
        "up including the entire face, eyes reflecting the blue smartphone "
        "screen glow, anxious expression, dimly lit room with a single "
        "lamp"),
    2: ("close-up shot of a 16-year-old Japanese boy's hand and face, "
        "thumb hovering tensely over a smartphone screen, sweat on temple, "
        "anxious determination, the phone screen blue glow on his face, "
        "dramatic side lighting"),
    # New: panel 3 = POV / over-the-shoulder showing the phone being held
    # The screen area should be relatively flat / blank so we can paste the
    # PIL EC mockup on top.
    3: ("close-up over-the-shoulder POV shot from behind a 16-year-old "
        "Japanese boy, looking down at his hands holding a smartphone "
        "centered in the frame, both hands gripping the phone with thumbs "
        "near the screen, the smartphone tilted slightly toward the viewer "
        "so the entire screen face is visible, the screen is blank and "
        "glowing white, dimly lit bedroom in the background blurred, focus "
        "on the phone in hand, dramatic chiaroscuro"),
    4: ("extreme close-up shot of a 16-year-old Japanese boy's face, wide "
        "eyes filled with desperate excitement, a single droplet of sweat "
        "rolling down his temple, lower lip bitten, the bluish reflection "
        "of a smartphone screen lighting one side of his face, the other "
        "side in deep shadow"),
}

DIALOGUE_PLACEMENT = {
    1: [{"anchor": "top-right", "style": "normal",
         "tail_dir": "down-left", "tail_length": 20,
         "width": 200, "min_h": 130}],
    2: [{"anchor": "top-left", "style": "normal",
         "tail_dir": "down-right", "tail_length": 18,
         "width": 180, "min_h": 130}],
    4: [
        {"anchor": "top-left", "style": "normal",
         "tail_dir": "down-right", "tail_length": 18,
         "width": 170, "min_h": 120},
        {"anchor": "top-right", "style": "shout",
         "tail_dir": "down-left", "tail_length": 22,
         "width": 220, "min_h": 140},
    ],
}


def build_workflow():
    manifest = json.loads((EPISODE / "image-gen-manifest.json").read_text())
    panels_by_idx = {p["panelIndex"]: p for p in manifest["panels"] if p["pageNum"] == PAGE}
    yuto_tags = char_tag_prompt("Yuto", with_lora_token=True)
    style = ("manga inked panel, sharp ink lines, screentone, monochrome with "
             "subtle color accent, cinematic chiaroscuro, shonen-jump style")

    wf = {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": "animagine-xl-4.0.safetensors"}},
        "2": {"class_type": "LoraLoaderModelOnly", "inputs": {
                "model": ["1", 0], "lora_name": "yuto_persona.safetensors",
                "strength_model": 0.85}},
    }
    next_id = 100
    panel_image_ids = {}

    # Generate ALL 4 panels via the model (including panel 3 hands-holding-phone)
    for slot in LAYOUT:
        idx = slot["idx"]
        if slot["aspect"] == "landscape":
            w_lat, h_lat = 1216, 832
        else:
            w_lat, h_lat = 832, 1216
        prompt = ", ".join([yuto_tags, PANEL_PROMPTS[idx], style])
        pos_id = str(next_id); next_id += 1
        wf[pos_id] = {"class_type": "CLIPTextEncode",
                      "inputs": {"text": prompt, "clip": ["1", 1]}}
        neg_id = str(next_id); next_id += 1
        wf[neg_id] = {"class_type": "CLIPTextEncode",
                      "inputs": {"text": NEG, "clip": ["1", 1]}}
        lat_id = str(next_id); next_id += 1
        wf[lat_id] = {"class_type": "EmptyLatentImage",
                      "inputs": {"width": w_lat, "height": h_lat, "batch_size": 1}}
        ks_id = str(next_id); next_id += 1
        wf[ks_id] = {"class_type": "KSampler", "inputs": {
                      "seed": 10000 + idx * 1009,
                      "steps": 28, "cfg": 6.0,
                      "sampler_name": "dpmpp_2m_sde", "scheduler": "karras",
                      "denoise": 1.0,
                      "model": ["2", 0],
                      "positive": [pos_id, 0], "negative": [neg_id, 0],
                      "latent_image": [lat_id, 0]}}
        dec_id = str(next_id); next_id += 1
        wf[dec_id] = {"class_type": "VAEDecode",
                      "inputs": {"samples": [ks_id, 0], "vae": ["1", 2]}}
        panel_image_ids[idx] = [dec_id, 0]

    # EC mockup (portrait phone, smaller dimensions for paste onto screen)
    ec_id = str(next_id); next_id += 1
    wf[ec_id] = {"class_type": "MangakaECMockup", "inputs": {
                   "width": 260, "height": 440,
                   "site_name": "TokyoSneaker",
                   "url_text": "tokyosneaker-premium.shop",
                   "product_title": "限定 Pure White Edition",
                   "price_now": "12,800",
                   "price_was": "25,000",
                   "countdown": "残り 04:32:11",
                   "cta_label": "今すぐ購入",
                   "review_text": "(2,847 件)",
                   "stars_filled": 5,
                   "screentone": True,
                   "phone_clock": "23:47"}}

    # Composite EC mockup onto panel 3's generated image (the blank phone screen)
    paste_id = str(next_id); next_id += 1
    wf[paste_id] = {"class_type": "MangakaPhoneScreenPaste", "inputs": {
                       "base": panel_image_ids[3],
                       "screen": [ec_id, 0],
                       # Position roughly at the center of the panel 3 image
                       # The generated image is 832x1216, phone centered.
                       # Phone in hand typically occupies ~50% of frame area.
                       "x": 290, "y": 380, "w": 250, "h": 460,
                       "rotation_deg": -4.0,
                       "shadow": True}}
    # Replace panel 3's image with the composited one
    panel_image_ids[3] = [paste_id, 0]

    canvas_id = str(next_id); next_id += 1
    wf[canvas_id] = {"class_type": "MangakaPageCanvas",
                     "inputs": {"width": 1280, "height": 1817}}
    page_ref = [canvas_id, 0]
    for slot in LAYOUT:
        paste_id2 = str(next_id); next_id += 1
        bw = 0 if slot.get("bleed") else 3
        wf[paste_id2] = {"class_type": "MangakaPanelPaste", "inputs": {
                          "page": page_ref, "panel": panel_image_ids[slot["idx"]],
                          "x": slot["x"], "y": slot["y"],
                          "w": slot["w"], "h": slot["h"], "border_width": bw}}
        page_ref = [paste_id2, 0]

    # SFX
    p1 = LAYOUT[0]
    sfx1 = str(next_id); next_id += 1
    wf[sfx1] = {"class_type": "MangakaSFX", "inputs": {
                  "image": page_ref, "text": "ピロン",
                  "x": p1["x"] + p1["w"] - 260, "y": p1["y"] + 20,
                  "font_size": 78, "rotation_deg": -8.0,
                  "color": "white", "stroke_color": "black", "stroke_width": 6,
                  "motion_lines": 10, "motion_line_length": 60}}
    page_ref = [sfx1, 0]
    p4 = LAYOUT[3]
    sfx4 = str(next_id); next_id += 1
    wf[sfx4] = {"class_type": "MangakaSFX", "inputs": {
                  "image": page_ref, "text": "ヤばい…",
                  "x": p4["x"] + 80, "y": p4["y"] + p4["h"] - 220,
                  "font_size": 96, "rotation_deg": -14.0,
                  "color": "white", "stroke_color": "black", "stroke_width": 8,
                  "motion_lines": 0, "motion_line_length": 0}}
    page_ref = [sfx4, 0]

    # Bubbles
    for slot in LAYOUT:
        idx = slot["idx"]
        if idx not in DIALOGUE_PLACEMENT:
            continue
        mp = panels_by_idx[idx]
        dlgs = [d for d in (mp.get("dialogues") or []) if (d.get("text") or "")]
        placements = DIALOGUE_PLACEMENT[idx]
        for di, d in enumerate(dlgs):
            placement = placements[min(di, len(placements)-1)]
            text = latinize(d.get("text") or "")
            bub_id = str(next_id); next_id += 1
            wf[bub_id] = {"class_type": "MangakaMangaBubble", "inputs": {
                            "image": page_ref,
                            "text": text,
                            "panel_x": slot["x"], "panel_y": slot["y"],
                            "panel_w": slot["w"], "panel_h": slot["h"],
                            "anchor": placement["anchor"],
                            "tail_dir": placement["tail_dir"],
                            "tail_length": placement["tail_length"],
                            "width": placement["width"],
                            "min_height": placement["min_h"],
                            "font_size": 26,
                            "padding": 14,
                            "vertical": True,
                            "style": placement["style"],
                            "outline_width": 3,
                            "overflow": 6}}
            page_ref = [bub_id, 0]

    save_id = str(next_id); next_id += 1
    wf[save_id] = {"class_type": "SaveImage", "inputs": {
                     "filename_prefix": "mangaka-page-arc01-p4-v10",
                     "images": page_ref}}
    return wf


def main():
    print("=== Page 4 v10 (panel 3 = generated hands+phone + PIL EC overlay) ===")
    wf = build_workflow()
    print(f"workflow: {len(wf)} nodes")
    pid = submit(wf)
    print(f"prompt_id: {pid}")
    t0 = time.monotonic()
    e = wait_for(pid, timeout=600)
    el = time.monotonic() - t0
    outs = e.get("outputs") or {}
    if not outs:
        print(f"FAIL ({el:.0f}s) {e.get('status',{}).get('status_str')}")
        for m in (e.get('status',{}).get('messages') or [])[-3:]:
            print(f"  {m}")
        return 1
    last = sorted([(int(nid), img) for nid, out in outs.items() for img in out.get('images',[])],
                  key=lambda x: x[0])[-1]
    print(f"final node {last[0]}: {last[1]['filename']} ({el:.0f}s)")
    data = fetch_view(last[1]["filename"])
    local = Path("/tmp/gh-arc0-1-p4-v10.png")
    local.write_bytes(data)
    print(f"saved -> {local} ({len(data)} B)")


if __name__ == "__main__":
    sys.exit(main())
