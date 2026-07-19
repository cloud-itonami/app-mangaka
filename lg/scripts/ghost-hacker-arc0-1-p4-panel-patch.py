"""page 4 v12: surgical patch — start from v10 final, regenerate ONLY
panel 2 with the improved smartphone prompt, paste it over the panel 2
region, repaint the panel-2 bubble (the previous one is erased by the
paste), leave panels 1/3/4 + their bubbles + SFX intact."""
import json, sys, time, urllib.parse, urllib.request
from pathlib import Path

COMFY = "http://192.168.1.70:8188"
ROOT = Path("/Users/junkawasaki/etzhayyim/etzhayyim-root/60-apps/etzhayyim-project-mangaka/data/ghosthacker")
EPISODE = ROOT / "resources/episodes/arc0-1-origin"
CHARS = ROOT / "resources/characters"


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
       "laptop, desktop computer, headless body, futuristic device, "
       "glowing blue rectangle, cyan light box, neon device, holographic")

_KATAKANA_SUBS = {"Akira": "アキラ"}
def latinize(s):
    for en, kat in _KATAKANA_SUBS.items():
        s = s.replace(en, kat)
    return s


# Panel 2 slot (same as v10/v11)
PANEL2_SLOT = {"x": 40, "y": 440, "w": 580, "h": 460}


def build_workflow():
    manifest = json.loads((EPISODE / "image-gen-manifest.json").read_text())
    panel2 = next(p for p in manifest["panels"]
                  if p["pageNum"] == 4 and p["panelIndex"] == 2)
    yuto_tags = char_tag_prompt("Yuto", with_lora_token=True)
    style = ("manga inked panel, sharp ink lines, screentone, monochrome with "
             "subtle color accent, cinematic chiaroscuro, shonen-jump style")

    panel2_prompt = (
        "medium close-up shot of a 16-year-old Japanese boy's face, his "
        "right hand holding a small black modern smartphone with a clearly "
        "visible bezel raised slightly into the frame from below-left, "
        "thumb resting on the screen, sweat on his temple, anxious "
        "expression, blue light from the phone screen reflecting subtly on "
        "his cheek, dimly lit bedroom with screentone shading in the "
        "background")

    wf = {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": "animagine-xl-4.0.safetensors"}},
        "2": {"class_type": "LoraLoaderModelOnly", "inputs": {
                "model": ["1", 0], "lora_name": "yuto_persona.safetensors",
                "strength_model": 0.85}},
        # Load v10 final composite
        "3": {"class_type": "LoadImage", "inputs": {"image": "gh-p4-v10-final.png"}},

        # Generate fresh panel 2 only
        "10": {"class_type": "CLIPTextEncode", "inputs": {
                "text": ", ".join([yuto_tags, panel2_prompt, style]),
                "clip": ["1", 1]}},
        "11": {"class_type": "CLIPTextEncode", "inputs": {
                "text": NEG, "clip": ["1", 1]}},
        "12": {"class_type": "EmptyLatentImage", "inputs": {
                "width": 832, "height": 1216, "batch_size": 1}},
        "13": {"class_type": "KSampler", "inputs": {
                "seed": 30303, "steps": 28, "cfg": 6.0,
                "sampler_name": "dpmpp_2m_sde", "scheduler": "karras",
                "denoise": 1.0,
                "model": ["2", 0],
                "positive": ["10", 0], "negative": ["11", 0],
                "latent_image": ["12", 0]}},
        "14": {"class_type": "VAEDecode", "inputs": {
                "samples": ["13", 0], "vae": ["1", 2]}},

        # Paste new panel 2 onto v10 final at the (40,440,580,460) region.
        # This also erases the previous panel-2 bubble that was baked in.
        "20": {"class_type": "MangakaPanelPaste", "inputs": {
                 "page": ["3", 0],     # v10 final as the canvas
                 "panel": ["14", 0],   # fresh panel 2
                 "x": PANEL2_SLOT["x"], "y": PANEL2_SLOT["y"],
                 "w": PANEL2_SLOT["w"], "h": PANEL2_SLOT["h"],
                 "border_width": 3}},

        # Repaint panel-2 bubble (top-left, normal style, tail down-right)
        "21": {"class_type": "MangakaMangaBubble", "inputs": {
                 "image": ["20", 0],
                 "text": latinize(panel2["dialogues"][0]["text"]),
                 "panel_x": PANEL2_SLOT["x"], "panel_y": PANEL2_SLOT["y"],
                 "panel_w": PANEL2_SLOT["w"], "panel_h": PANEL2_SLOT["h"],
                 "anchor": "top-left",
                 "tail_dir": "down-right", "tail_length": 18,
                 "width": 220, "min_height": 160,
                 "font_size": 30, "padding": 16,
                 "vertical": True, "style": "normal",
                 "outline_width": 4, "overflow": 6}},

        "30": {"class_type": "SaveImage", "inputs": {
                 "filename_prefix": "mangaka-page-arc01-p4-v12-surgical",
                 "images": ["21", 0]}},
    }
    return wf


def main():
    print("=== Page 4 v12 (SURGICAL: only panel 2 replaced over v10 base) ===")
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
    local = Path("/tmp/gh-arc0-1-p4-v12.png")
    local.write_bytes(data)
    print(f"saved -> {local} ({len(data)} B)")


if __name__ == "__main__":
    sys.exit(main())
