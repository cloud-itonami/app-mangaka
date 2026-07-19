"""Test the trained Yuto LoRA — generate a panel matching page 4 p1 prompt.
Compare with the IPAdapter version to see identity improvement."""
import json, time, urllib.parse, urllib.request

COMFY = "http://192.168.1.70:8188"
NEG = ("low quality, worst quality, normal quality, lowres, blurry, deformed, "
       "extra fingers, bad anatomy, malformed hands, bad proportions, "
       "photograph, photorealistic, 3d render, multiple panels, collage, "
       "comic page, wings, nsfw, logo, watermark, signature, text overlay")
PROMPT = (
    "yuto_persona, 1boy, 16 year old Japanese high school student, "
    "neat black schoolboy hair, expressive brown eyes, anxious expression, "
    "(medium shot), in a dimly lit bedroom leaning forward toward a glowing laptop, "
    "eyes reflecting the screen glow, eyes wide, manga inked panel, "
    "sharp ink lines, screentone, monochrome with subtle cyan accent, "
    "cinematic chiaroscuro, shonen-jump style"
)
wf = {
    "1": {"class_type": "CheckpointLoaderSimple",
          "inputs": {"ckpt_name": "animagine-xl-4.0.safetensors"}},
    "2": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["1", 0],
            "lora_name": "yuto_persona.safetensors",
            "strength_model": 1.0}},
    "3": {"class_type": "CLIPTextEncode",
          "inputs": {"text": PROMPT, "clip": ["1", 1]}},
    "4": {"class_type": "CLIPTextEncode",
          "inputs": {"text": NEG, "clip": ["1", 1]}},
    "5": {"class_type": "EmptyLatentImage",
          "inputs": {"width": 832, "height": 1216, "batch_size": 1}},
    "6": {"class_type": "KSampler", "inputs": {
            "seed": 42, "steps": 28, "cfg": 6.0,
            "sampler_name": "dpmpp_2m_sde", "scheduler": "karras",
            "denoise": 1.0,
            "model": ["2", 0],
            "positive": ["3", 0], "negative": ["4", 0],
            "latent_image": ["5", 0]}},
    "7": {"class_type": "VAEDecode",
          "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
    "8": {"class_type": "SaveImage", "inputs": {
            "filename_prefix": "yuto-lora-test",
            "images": ["7", 0]}},
}

req = urllib.request.Request(f"{COMFY}/prompt",
    data=json.dumps({"prompt": wf}).encode(),
    headers={"content-type": "application/json"}, method="POST")
resp = json.load(urllib.request.urlopen(req, timeout=30))
pid = resp["prompt_id"]
print(f"submitted: {pid}")

t0 = time.monotonic()
for _ in range(60):
    e = (json.load(urllib.request.urlopen(f"{COMFY}/history/{pid}", timeout=10)) or {}).get(pid)
    if e and (e.get("outputs") or e.get("status",{}).get("status_str") == "error"):
        break
    time.sleep(3)
e = (json.load(urllib.request.urlopen(f"{COMFY}/history/{pid}", timeout=10)) or {}).get(pid) or {}
print(f"done in {time.monotonic()-t0:.0f}s status={e.get('status',{}).get('status_str')}")
for nid, out in (e.get("outputs") or {}).items():
    for img in out.get("images", []):
        q = urllib.parse.urlencode({"filename": img['filename']})
        with urllib.request.urlopen(f"{COMFY}/view?{q}") as r:
            data = r.read()
        local = "/tmp/yuto-lora-test.png"
        open(local, "wb").write(data)
        print(f"saved {local} ({len(data)} B) — comfy: {img['filename']}")
