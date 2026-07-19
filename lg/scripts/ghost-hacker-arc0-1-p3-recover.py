"""Recover page 3 — fetch already-completed panels 4/6/7 from ComfyUI,
retry the 2 errored Ren panels (3,5) using Flux WITHOUT PuLID
(text-only fallback), and finally composite the page."""
import json, time, urllib.parse, urllib.request
from pathlib import Path

COMFY = "http://192.168.1.70:8188"
ROOT = Path("/Users/junkawasaki/etzhayyim/etzhayyim-root/60-apps/etzhayyim-project-mangaka/data/ghosthacker")
EPISODE = ROOT / "resources/episodes/arc0-1-origin"
CHARS = ROOT / "resources/characters"
PAGE = 3

def _http(method, path, body=None, headers=None):
    req = urllib.request.Request(f"{COMFY}{path}", data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, r.read()

def fetch_view(filename, subfolder="", typ="output"):
    q = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": typ})
    s, b = _http("GET", f"/view?{q}")
    return b

def submit(workflow):
    s, b = _http("POST", "/prompt", json.dumps({"prompt": workflow}).encode(),
                 {"content-type": "application/json"})
    return json.loads(b)["prompt_id"]

def wait_for(pid, timeout=600):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s, b = _http("GET", f"/history/{pid}")
        j = json.loads(b)
        e = j.get(pid)
        if e and (e.get("outputs") or e.get("status",{}).get("status_str") == "error"):
            return e
        time.sleep(3)
    return {}

# 1. Fetch already-done panels 4/6/7 from ComfyUI history
print("=== fetching completed panels 4/6/7 ===")
done_map = {1: "ghost-hacker-arc0-1-p3-panel1_00001_.png",
            2: "ghost-hacker-arc0-1-p3-panel2_00001_.png",
            4: "ghost-hacker-arc0-1-p3-panel4_00001_.png",
            6: "ghost-hacker-arc0-1-p3-panel6_00001_.png",
            7: "ghost-hacker-arc0-1-p3-panel7_00001_.png"}
for idx, fn in done_map.items():
    local = Path(f"/tmp/gh-arc0-1-p{PAGE}-panel{idx}.png")
    if local.exists():
        print(f"  panel {idx}: already local ({local.stat().st_size} B)")
        continue
    data = fetch_view(fn)
    local.write_bytes(data)
    print(f"  panel {idx}: fetched -> {local} ({len(data)} B)")

# 2. Retry panels 3 and 5 — Flux text-only fallback (no PuLID monkey-patch)
print("\n=== retry panels 3 and 5 with Flux text-only ===")
manifest = json.loads((EPISODE / "image-gen-manifest.json").read_text())
panels_to_retry = [p for p in manifest["panels"] if p["pageNum"] == PAGE and p["panelIndex"] in (3, 5)]
ren_profile = json.loads((CHARS / "Ren/profile.jsonld").read_text())
ren_app = ren_profile.get("gh:appearance", {})

def build_flux_only(panel_idx, prompt, seed, width=832, height=1216):
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
        "9":  {"class_type": "KSampler", "inputs": {
                 "seed": seed, "steps": 22, "cfg": 1.0,
                 "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
                 "model": ["8", 0], "positive": ["6", 0], "negative": ["5", 0],
                 "latent_image": ["7", 0]}},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["3", 0]}},
        "11": {"class_type": "SaveImage", "inputs": {
                 "filename_prefix": f"ghost-hacker-arc0-1-p{PAGE}-panel{panel_idx}-retry",
                 "images": ["10", 0]}},
    }

ren_desc = ", ".join([
    "1boy", "17 year old",
    ren_app.get("gh:face",""),
    ren_app.get("gh:hair",""),
    ren_app.get("gh:eyes",""),
])
style = "anime manga inked panel, masterpiece, best quality, sharp black ink lines, screentone, hatching, monochrome with subtle color accent, cinematic chiaroscuro, shonen-jump style"

for p in panels_to_retry:
    visual = p["visual"]
    prompt = ". ".join([
        f"{p['shot'].lower()} shot",
        visual,
        ren_desc,
        style,
    ])
    print(f"\npanel {p['panelIndex']}: submitting flux-only retry...")
    print(f"  visual: {visual[:120]}")
    wf = build_flux_only(p["panelIndex"], prompt, seed=99000 + p["panelIndex"]*1009)
    pid = submit(wf)
    print(f"  prompt_id={pid}, waiting...")
    t0 = time.monotonic()
    e = wait_for(pid, timeout=600)
    el = time.monotonic() - t0
    outs = e.get("outputs") or {}
    if outs:
        for nid, out in outs.items():
            for img in out.get("images", []):
                data = fetch_view(img["filename"])
                local = Path(f"/tmp/gh-arc0-1-p{PAGE}-panel{p['panelIndex']}.png")
                local.write_bytes(data)
                print(f"  done in {el:.0f}s -> {local} ({len(data)} B, comfy: {img['filename']})")
                break
    else:
        st = e.get("status", {}).get("status_str")
        print(f"  FAIL status={st} ({el:.0f}s)")

print("\n=== ready for composite ===")
