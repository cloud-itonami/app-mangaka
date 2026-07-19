"""ghost-hacker-arc0-1 page 4 — ALL-ComfyUI-native pipeline.

Generates page 4 (Yuto room dramatic scene, 4 panels) as ONE giant
ComfyUI workflow that includes:
  - 4 panel generators (Animagine XL + shared IPAdapter PLUS for panels
    with Yuto, plain SDXL for the no-char insert panel)
  - SFX text + speech-bubble overlays via the new Mangaka* custom nodes
  - Page canvas + 4 paste operations
  - Single SaveImage at the end

The final page is saved directly to ComfyUI's output dir so it appears
in Media Assets. No PIL post-process on the Mac side.

Output filename: mangaka-page-arc01-p4-final_NNNNN.png
"""
import io, json, os, sys, time, urllib.parse, urllib.request
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


def upload_image(local: Path, hint: str) -> str:
    data = local.read_bytes()
    boundary = "----native_p4"
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
    j = json.loads(b)
    if j.get("node_errors"):
        print(f"node_errors: {json.dumps(j['node_errors'], indent=2)[:2000]}")
    return j["prompt_id"]


def wait_for(pid, timeout=1200):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s, b = _http("GET", f"/history/{pid}")
        e = (json.loads(b) or {}).get(pid)
        if e and (e.get("outputs") or e.get("status", {}).get("status_str") == "error"):
            return e
        time.sleep(3)
    return {}


def fetch_view(filename, subfolder="", typ="output"):
    q = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": typ})
    s, b = _http("GET", f"/view?{q}")
    return b


def char_tag_prompt(name: str) -> str:
    raw = (CHARS / name / "reference.prompt.txt").read_text()
    pos = raw.split("--- negative ---")[0].strip()
    drop = ["character reference sheet", "T-pose or relaxed standing pose",
            "full body shot", "plain white background", "clean line art",
            "neutral expression"]
    tags = [t.strip() for t in pos.split(",")]
    return ", ".join(t for t in tags if t and not any(d in t for d in drop))


NEG = ("low quality, worst quality, normal quality, lowres, blurry, deformed, "
       "extra fingers, bad anatomy, malformed hands, bad proportions, "
       "photograph, photorealistic, 3d render, multiple panels, collage, "
       "comic page, wings, nsfw, logo, watermark, signature, text overlay")


def build_full_page4_workflow(yuto_ref_filename: str) -> dict:
    """Build the full 50+ node workflow for page 4."""
    manifest = json.loads((EPISODE / "image-gen-manifest.json").read_text())
    panels = [p for p in manifest["panels"] if p["pageNum"] == PAGE]
    assert len(panels) == 4, f"expected 4 panels, got {len(panels)}"

    yuto_tags = char_tag_prompt("Yuto")
    style = "manga inked panel, sharp ink lines, screentone, monochrome with subtle color accent, cinematic chiaroscuro, shonen-jump style"

    # Page 4 layout (4 panels)
    layout = [
        {"x": 40,  "y": 40,   "w": 720,  "h": 540},     # p1 Medium (Yuto laptop)
        {"x": 780, "y": 40,   "w": 460,  "h": 540},     # p2 CU small (Yuto fist)
        {"x": 40,  "y": 600,  "w": 1200, "h": 540},     # p3 Insert large (screen ad)
        {"x": 240, "y": 1160, "w": 800,  "h": 617},     # p4 CU medium (Yuto eager)
    ]

    wf: dict = {}

    # Shared resources
    wf["1"] = {"class_type": "CheckpointLoaderSimple",
               "inputs": {"ckpt_name": "animagine-xl-4.0.safetensors"}}
    wf["2"] = {"class_type": "LoadImage", "inputs": {"image": yuto_ref_filename}}
    wf["3"] = {"class_type": "IPAdapterUnifiedLoader", "inputs": {
                 "model": ["1", 0],
                 "preset": "PLUS (high strength)"}}
    wf["4"] = {"class_type": "IPAdapter", "inputs": {
                 "model": ["3", 0],
                 "ipadapter": ["3", 1],
                 "image": ["2", 0],
                 "weight": 0.65,
                 "start_at": 0.0, "end_at": 1.0,
                 "weight_type": "standard"}}

    # Per-panel
    last_overlay_id: dict[int, str] = {}    # panel_idx -> last image node id (after overlay)
    next_id = 100

    for idx, (p, slot) in enumerate(zip(panels, layout), 1):
        # Choose model: panel 3 = insert (screen ad, no char) -> plain SDXL (node 1)
        #               others = use IPAdapter-conditioned model (node 4)
        model_ref = ["1", 0] if idx == 3 else ["4", 0]

        # Build prompt
        if idx == 3:
            prompt = ", ".join([
                "(insert shot)",
                p["visual"],
                "manga panel, sharp ink lines, dramatic, screentone, monochrome accent",
            ])
        else:
            prompt = ", ".join([
                yuto_tags,
                f"({p['shot'].lower()} shot)",
                p["visual"],
                style,
            ])

        # POSITIVE encode
        pos_id = str(next_id); next_id += 1
        wf[pos_id] = {"class_type": "CLIPTextEncode",
                      "inputs": {"text": prompt, "clip": ["1", 1]}}
        # NEGATIVE encode
        neg_id = str(next_id); next_id += 1
        wf[neg_id] = {"class_type": "CLIPTextEncode",
                      "inputs": {"text": NEG, "clip": ["1", 1]}}
        # Empty latent (832x1216 portrait)
        lat_id = str(next_id); next_id += 1
        wf[lat_id] = {"class_type": "EmptyLatentImage",
                      "inputs": {"width": 832, "height": 1216, "batch_size": 1}}
        # KSampler
        ks_id = str(next_id); next_id += 1
        wf[ks_id] = {"class_type": "KSampler", "inputs": {
                       "seed": 33000 + idx * 1009,
                       "steps": 28, "cfg": 6.0,
                       "sampler_name": "dpmpp_2m_sde", "scheduler": "karras",
                       "denoise": 1.0,
                       "model": model_ref,
                       "positive": [pos_id, 0], "negative": [neg_id, 0],
                       "latent_image": [lat_id, 0]}}
        # VAEDecode
        dec_id = str(next_id); next_id += 1
        wf[dec_id] = {"class_type": "VAEDecode",
                      "inputs": {"samples": [ks_id, 0], "vae": ["1", 2]}}

        current = [dec_id, 0]

        # SFX overlay if panel 1 (dramatic phone glow)
        if idx == 1:
            sfx_id = str(next_id); next_id += 1
            wf[sfx_id] = {"class_type": "MangakaTextOverlay", "inputs": {
                            "image": current,
                            "text": "5万…", "x": 60, "y": 60,
                            "font_size": 56, "color": "white",
                            "stroke_color": "black", "stroke_width": 4}}
            current = [sfx_id, 0]

        # SFX overlay if panel 3 (screen ad)
        if idx == 3:
            sfx_id = str(next_id); next_id += 1
            wf[sfx_id] = {"class_type": "MangakaTextOverlay", "inputs": {
                            "image": current,
                            "text": "TokyoSneaker-Premium.shop", "x": 40, "y": 80,
                            "font_size": 36, "color": "yellow",
                            "stroke_color": "black", "stroke_width": 3}}
            current = [sfx_id, 0]
            # Also countdown
            ctd_id = str(next_id); next_id += 1
            wf[ctd_id] = {"class_type": "MangakaTextOverlay", "inputs": {
                            "image": current,
                            "text": "残り 04:32:11", "x": 40, "y": 140,
                            "font_size": 32, "color": "red",
                            "stroke_color": "black", "stroke_width": 2}}
            current = [ctd_id, 0]

        # Dialogue bubble(s) per manifest
        for d in (p.get("dialogues") or []):
            text = d.get("text") or ""
            if not text: continue
            spk = d.get("speaker") or ""
            bub_id = str(next_id); next_id += 1
            wf[bub_id] = {"class_type": "MangakaSpeechBubble", "inputs": {
                            "image": current,
                            "speaker": spk,
                            "text": text,
                            "anchor": "bottom-center",
                            "max_width_frac": 0.7,
                            "font_size": 26,
                            "padding": 14}}
            current = [bub_id, 0]

        last_overlay_id[idx] = current

    # Page canvas
    canvas_id = str(next_id); next_id += 1
    wf[canvas_id] = {"class_type": "MangakaPageCanvas",
                     "inputs": {"width": 1280, "height": 1817}}

    page_ref = [canvas_id, 0]
    for idx, slot in zip([1, 2, 3, 4], layout):
        paste_id = str(next_id); next_id += 1
        wf[paste_id] = {"class_type": "MangakaPanelPaste", "inputs": {
                          "page": page_ref,
                          "panel": last_overlay_id[idx],
                          "x": slot["x"], "y": slot["y"],
                          "w": slot["w"], "h": slot["h"],
                          "border_width": 3}}
        page_ref = [paste_id, 0]

    # Final save
    save_id = str(next_id); next_id += 1
    wf[save_id] = {"class_type": "SaveImage", "inputs": {
                     "filename_prefix": "mangaka-page-arc01-p4-final",
                     "images": page_ref}}

    return wf


def main() -> int:
    print(f"=== Ghost Hacker Arc 0-1 Page {PAGE} — ALL-NATIVE ComfyUI page workflow ===")
    yuto_ref = upload_image(CHARS / "Yuto/reference.png", hint="gh-yuto-native-p4")
    print(f"uploaded Yuto ref -> {yuto_ref}")

    wf = build_full_page4_workflow(yuto_ref)
    print(f"workflow: {len(wf)} nodes")

    # Persist workflow JSON for reference (saved on Mac side)
    out_wf = Path("/tmp/gh-p4-native-workflow.json")
    out_wf.write_text(json.dumps(wf, indent=2))
    print(f"workflow json -> {out_wf}")

    pid = submit(wf)
    print(f"prompt_id: {pid}")

    t0 = time.monotonic()
    e = wait_for(pid, timeout=1800)
    el = time.monotonic() - t0
    outs = e.get("outputs") or {}
    if not outs:
        status = e.get("status", {})
        print(f"FAIL ({el:.0f}s) status={status.get('status_str')}")
        for m in (status.get("messages") or [])[-5:]:
            print(f"  msg: {m}")
        return 1
    # Final SaveImage is the LAST node output — find the highest-numbered output node
    last_imgs = []
    for nid, out in outs.items():
        for img in out.get("images", []):
            last_imgs.append((int(nid), img))
    last_imgs.sort(key=lambda x: x[0])
    # Final page is from the SaveImage which is the highest numbered node
    last_nid, last_img = last_imgs[-1]
    print(f"final page from node {last_nid}: {last_img['filename']} (in {el:.0f}s)")
    data = fetch_view(last_img["filename"])
    local = Path(f"/tmp/gh-arc0-1-p{PAGE}-native.png")
    local.write_bytes(data)
    print(f"saved -> {local} ({len(data)} B)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
