"""Train Yuto LoRA via ComfyUI native TrainLoraNode (no kohya subprocess).

Workflow:
  CheckpointLoaderSimple animagine-xl-4.0  -> MODEL, CLIP, VAE
  LoadImageTextDataSetFromFolder "lora-yuto" -> images, texts
  MakeTrainingDataset(images, vae, clip, texts) -> latents, conditioning
  TrainLoraNode(model, latents, positive=conditioning, ...) -> LORA, loss_map, steps
  SaveLoRA(lora, prefix="loras/yuto_persona", steps=...)

Should land at ComfyUI/models/loras/yuto_persona_<steps>.safetensors.
"""
import json, time, urllib.parse, urllib.request

COMFY = "http://192.168.1.70:8188"

wf = {
    "1": {"class_type": "CheckpointLoaderSimple",
          "inputs": {"ckpt_name": "animagine-xl-4.0.safetensors"}},
    # WAS NS folder loader (folder is relative to ComfyUI input/)
    "2": {"class_type": "LoadImageTextDataSetFromFolder",
          "inputs": {"folder": "lora-yuto"}},
    "3": {"class_type": "MakeTrainingDataset", "inputs": {
            "images": ["2", 0], "vae": ["1", 2], "clip": ["1", 1],
            "texts": ["2", 1]}},
    "4": {"class_type": "TrainLoraNode", "inputs": {
            "model": ["1", 0],
            "latents": ["3", 0],
            "positive": ["3", 1],
            "batch_size": 1,
            "grad_accumulation_steps": 1,
            "steps": 200,             # ~200 steps for 13 images (~15 epochs)
            "learning_rate": 0.0005,
            "rank": 16,                # 16-32 typical for character LoRA
            "optimizer": "AdamW",
            "loss_function": "MSE",
            "seed": 42,
            "training_dtype": "bf16",
            "lora_dtype": "bf16",
            "quantized_backward": False,
            "algorithm": "LoRA",
            "gradient_checkpointing": True,
            "checkpoint_depth": 1,
            "offloading": False,
            "existing_lora": "[None]",
            "bucket_mode": False,
            "bypass_mode": False}},
    "5": {"class_type": "SaveLoRA", "inputs": {
            "lora": ["4", 0],
            "prefix": "yuto_persona_native",
            "steps": ["4", 2]}},
}

req = urllib.request.Request(f"{COMFY}/prompt",
                              data=json.dumps({"prompt": wf}).encode(),
                              headers={"content-type": "application/json"},
                              method="POST")
resp = json.load(urllib.request.urlopen(req, timeout=30))
print(f"submitted: {resp}")
pid = resp.get("prompt_id")
if not pid:
    print(f"FAIL: {resp}")
    raise SystemExit(1)

# Poll
print("polling (long-running, ~5-15 min expected)...")
start = time.monotonic()
while True:
    el = int(time.monotonic() - start)
    try:
        e = json.load(urllib.request.urlopen(f"{COMFY}/history/{pid}", timeout=10)).get(pid)
        if e:
            s = e.get("status", {}).get("status_str")
            outs = e.get("outputs") or {}
            if s == "error" or outs:
                print(f"t+{el}s status={s} outputs={len(outs)}")
                if s == "error":
                    for m in (e.get("status",{}).get("messages") or [])[-5:]:
                        print(f"  {m}")
                break
        q = json.load(urllib.request.urlopen(f"{COMFY}/queue", timeout=10))
        rr = q.get("queue_running") or []
        pp = q.get("queue_pending") or []
        print(f"  t+{el}s queue r={len(rr)} p={len(pp)}")
    except Exception as ex:
        print(f"  t+{el}s poll: {ex}")
    if el > 1800:
        print("timeout 30 min")
        break
    time.sleep(30)
