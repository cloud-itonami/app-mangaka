"""Re-generate the 5 Nei panels using IPAdapter Plus (PLUS preset, no FaceID)
which uses CLIP-Vision and works on anime references."""
import json, os, sys, time, urllib.parse, urllib.request
from pathlib import Path

COMFY = "http://192.168.1.70:8188"
ROOT = Path("/Users/junkawasaki/etzhayyim/etzhayyim-root/60-apps/etzhayyim-project-mangaka/data/ghosthacker")
EPISODE = ROOT / "resources/episodes/arc0-1-origin"
CHARS = ROOT / "resources/characters"
PAGE = 3
NEI_PANELS = [1, 2, 4, 6, 7]   # the ones that errored

def _http(method, path, body=None, headers=None):
    req = urllib.request.Request(f"{COMFY}{path}", data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, r.read()

def upload_image(local, hint):
    data = local.read_bytes()
    boundary = "----nei_retry"
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

def submit(wf):
    s, b = _http("POST", "/prompt", json.dumps({"prompt": wf}).encode(),
                 {"content-type": "application/json"})
    return json.loads(b)["prompt_id"]

def wait_for(pid, timeout=600):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s, b = _http("GET", f"/history/{pid}")
        e = (json.loads(b) or {}).get(pid)
        if e and (e.get("outputs") or e.get("status", {}).get("status_str") == "error"):
            return e
        time.sleep(2)
    return {}

def fetch_view(filename):
    q = urllib.parse.urlencode({"filename": filename, "subfolder": "", "type": "output"})
    s, b = _http("GET", f"/view?{q}")
    return b

def char_tag_prompt(name):
    raw = (CHARS / name / "reference.prompt.txt").read_text()
    pos = raw.split("--- negative ---")[0].strip()
    drop = ["character reference sheet", "T-pose or relaxed standing pose",
            "full body shot", "plain white background", "clean line art",
            "neutral expression"]
    tags = [t.strip() for t in pos.split(",")]
    return ", ".join(t for t in tags if t and not any(d in t for d in drop))

NEG = ("low quality, worst quality, normal quality, lowres, blurry, deformed, "
       "extra fingers, bad anatomy, malformed hands, photograph, photorealistic, "
       "3d render, multiple panels, collage, comic page, watermark, signature, text overlay")

def build_workflow(*, ref_filename, panel_idx, prompt, seed):
    """SDXL Animagine + IPAdapter PLUS (no FaceID, CLIP-Vision only)."""
    return {
        "1":  {"class_type": "CheckpointLoaderSimple",
               "inputs": {"ckpt_name": "animagine-xl-4.0.safetensors"}},
        "2":  {"class_type": "LoadImage", "inputs": {"image": ref_filename}},
        "3":  {"class_type": "EmptyLatentImage", "inputs": {
                 "width": 832, "height": 1216, "batch_size": 1}},
        "4":  {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "5":  {"class_type": "CLIPTextEncode", "inputs": {"text": NEG, "clip": ["1", 1]}},

        # IPAdapter Plus (no FaceID — CLIP-Vision only, works on anime refs)
        "10": {"class_type": "IPAdapterUnifiedLoader", "inputs": {
                 "model": ["1", 0],
                 "preset": "PLUS (high strength)"}},
        "11": {"class_type": "IPAdapter", "inputs": {
                 "model": ["10", 0],
                 "ipadapter": ["10", 1],
                 "image": ["2", 0],
                 "weight": 0.85,
                 "start_at": 0.0, "end_at": 1.0,
                 "weight_type": "standard"}},

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
                 "filename_prefix": f"ghost-hacker-arc0-1-p{PAGE}-anim-panel{panel_idx}-r",
                 "images": ["21", 0]}},
    }

manifest = json.loads((EPISODE / "image-gen-manifest.json").read_text())
panels = {p["panelIndex"]: p for p in manifest["panels"] if p["pageNum"] == PAGE}

nei_ref = upload_image(CHARS / "Nei/reference.png", hint="gh-nei-retry-ipa")
print(f"uploaded Nei ref -> {nei_ref}")

nei_tags = char_tag_prompt("Nei")
style = "manga inked panel, sharp ink lines, screentone, monochrome with subtle color accent, cinematic chiaroscuro, shonen-jump style"

submitted = []
for idx in NEI_PANELS:
    p = panels[idx]
    prompt = ", ".join([
        nei_tags,
        f"({p['shot'].lower()} shot)",
        p["visual"],
        style,
    ])
    print(f"\npanel {idx} ({p['shot']}): submitting IPAdapter PLUS retry...")
    wf = build_workflow(ref_filename=nei_ref, panel_idx=idx,
                        prompt=prompt, seed=22000 + idx * 1009)
    pid = submit(wf)
    submitted.append((idx, pid, p))
    print(f"  prompt_id={pid}")

for idx, pid, p in submitted:
    print(f"\nwaiting panel {idx}...")
    t0 = time.monotonic()
    e = wait_for(pid, timeout=600)
    el = time.monotonic() - t0
    outs = e.get("outputs") or {}
    if not outs:
        print(f"  FAIL ({el:.0f}s) status={e.get('status',{}).get('status_str')}")
        continue
    for nid, out in outs.items():
        for img in out.get("images", []):
            data = fetch_view(img["filename"])
            local = Path(f"/tmp/gh-arc0-1-p{PAGE}-anim-panel{idx}.png")
            local.write_bytes(data)
            print(f"  done in {el:.0f}s -> {local} ({len(data)} B)")
            break

print("\n=== ready for re-composite ===")
