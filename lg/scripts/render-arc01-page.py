"""Universal ghost-hacker arc 0-1 page renderer.

Usage:
    python3 render-arc01-page.py <page_num>           # render one page
    python3 render-arc01-page.py --all                # render every page (0..45)
    python3 render-arc01-page.py --range 0,3,4        # render specific pages

Reads:
    image-gen-manifest.json  — per-panel visual/dialogue/layout
    character profiles      — appearance tags
    per-character LoRA files — character identity (yuto/ren/nei)

Generates each panel via Animagine XL + appropriate character LoRA,
composites into a manga page with:
  - row-based layout derived from panelLayout.gh:row + gh:size
  - right-to-left manga reading order
  - vertical Japanese dialogue bubbles with corner anchors + short
    stub tails + 30pt Yu Gothic Bold + Latin -> katakana
  - panel borders + optional full-bleed for climax panels

Output filename: mangaka-page-arc01-pN-final_xxxxx.png on the ComfyUI host.
"""
from __future__ import annotations
import json, re, sys, time, urllib.parse, urllib.request
from pathlib import Path

COMFY = "http://192.168.1.22:8188"
ROOT = Path("/Users/junkawasaki/etzhayyim/etzhayyim-root/60-apps/etzhayyim-project-mangaka/data/ghosthacker")
EPISODE = ROOT / "resources/episodes/arc0-1-origin"
CHARS = ROOT / "resources/characters"

# Page dimensions
PAGE_W = 1280
PAGE_H = 1817
MARGIN = 40
GUTTER = 16

# Size -> relative width unit (small=1, medium=2, large=3, spread=full)
SIZE_UNIT = {"small": 1, "medium": 2, "large": 3, "spread": 6}

# Character → LoRA filename
CHAR_LORA = {
    "Yuto": "yuto_persona.safetensors",
    "Ren":  "ren_persona.safetensors",
    "Nei":  "nei_persona.safetensors",
}

# Katakana substitution
_KATAKANA = {
    "Akira": "アキラ", "Yuto": "ユウト", "Ren": "レン", "Nei": "ネイ",
    "Mei": "メイ", "Saki": "サキ", "nue": "ヌエ", "Chise": "チセ",
    "Kaname": "カナメ", "Holonium": "ホロニウム",
}
def latinize(s: str) -> str:
    for en, kat in _KATAKANA.items():
        s = s.replace(en, kat)
    return s


def _http(method, path, body=None, headers=None):
    req = urllib.request.Request(f"{COMFY}{path}", data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status, r.read()


def submit(wf):
    s, b = _http("POST", "/prompt", json.dumps({"prompt": wf}).encode(),
                 {"content-type": "application/json"})
    j = json.loads(b)
    if j.get("node_errors"):
        print(f"  node_errors: {json.dumps(j['node_errors'], indent=2)[:1500]}")
    return j.get("prompt_id")


def wait_for(pid, timeout=1800):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s, b = _http("GET", f"/history/{pid}")
        e = (json.loads(b) or {}).get(pid)
        if e and (e.get("outputs") or e.get("status", {}).get("status_str") in ("error", "success")):
            return e
        time.sleep(5)
    return {}


def fetch_view(filename):
    q = urllib.parse.urlencode({"filename": filename, "subfolder": "", "type": "output"})
    s, b = _http("GET", f"/view?{q}")
    return b


def char_tag_prompt(name: str, with_lora_token: bool = False) -> str:
    pfile = CHARS / name / "profile.jsonld"
    if not pfile.exists():
        return ""
    p = json.loads(pfile.read_text())
    a = p.get("gh:appearance", {})
    gender = "1girl" if "female" in (a.get("gh:face", "").lower()) else "1boy"
    tags = [
        ("anime illustration", True),
        (f"{name.lower()}_persona", with_lora_token),
        (gender, True),
        (f"{p.get('schema:age', 17)} year old", True),
        (a.get("gh:face", ""), True),
        (a.get("gh:hair", ""), True),
        (a.get("gh:eyes", ""), True),
    ]
    return ", ".join(t for t, keep in tags if t and keep)


NEG = ("low quality, worst quality, normal quality, lowres, blurry, deformed, "
       "extra fingers, bad anatomy, malformed hands, bad proportions, "
       "photograph, photorealistic, 3d render, multiple panels, collage, "
       "comic page, wings, nsfw, logo, watermark, signature, text overlay, "
       "laptop, desktop computer, headless body, futuristic device, "
       "glowing blue rectangle, cyan light box, neon device, holographic, "
       # ↓ Path A — style enforcement (no soft / colored / sketchy renders)
       "color illustration, soft sketch, watercolor, dull tones, "
       "soft pencil, muted colors, brown tones, sepia, washed out, "
       "low contrast, sketchy lines, unfinished")


def compute_layout(panels: list[dict]) -> list[dict]:
    """Given panels with panelLayout.gh:row + gh:size, distribute them into
    a right-to-left manga grid that fills the page.

    Returns a list of dicts with x, y, w, h, idx for each panel."""
    # Group by row
    rows: dict[int, list[dict]] = {}
    for p in panels:
        r = p.get("panelLayout", {}).get("gh:row") or 1
        rows.setdefault(int(r), []).append(p)
    sorted_rows = sorted(rows.keys())

    # Available height
    avail_h = PAGE_H - 2 * MARGIN - (len(sorted_rows) - 1) * GUTTER
    row_h = avail_h // len(sorted_rows)

    layout: list[dict] = []
    cur_y = MARGIN
    for r in sorted_rows:
        row_panels = rows[r]
        # Sort by reading order — for R→L manga, reverse panelIndex order
        # within row so panel 1 ends up rightmost on screen.
        row_panels = sorted(
            row_panels,
            key=lambda p: p.get("panelLayout", {}).get("gh:readingOrder", p["panelIndex"]),
        )
        # Compute relative widths
        units = [SIZE_UNIT.get(p.get("panelLayout", {}).get("gh:size", "medium"), 2)
                 for p in row_panels]
        total_units = sum(units)
        avail_w = PAGE_W - 2 * MARGIN - (len(row_panels) - 1) * GUTTER
        unit_w = avail_w / total_units

        # Place panels from RIGHT to LEFT (manga reading)
        cur_x = PAGE_W - MARGIN
        for i, p in enumerate(row_panels):
            w = int(round(units[i] * unit_w))
            cur_x -= w
            slot = {
                "idx": p["panelIndex"],
                "x": cur_x,
                "y": cur_y,
                "w": w,
                "h": row_h,
                "panel": p,
            }
            layout.append(slot)
            if i < len(row_panels) - 1:
                cur_x -= GUTTER
        cur_y += row_h + GUTTER

    # Order by idx so subsequent processing iterates in source order
    layout.sort(key=lambda s: s["idx"])
    return layout


# Per-page environment hint (Arc 0-1).
# Drawn from the page titles in episode.jsonld so the model gets an
# explicit setting in the prompt — not just whatever it infers from the
# panel's visual text.
PAGE_ENVIRONMENT = {
    0:  "dimly lit bedroom at night, single lamp, screentone shading",
    1:  "school classroom at lunch break, daytime, windows letting in soft light, desks and chairs",
    2:  "school classroom at lunch break, students gathered around a desk, daytime light",
    3:  "city street, after school, late afternoon, sidewalks and storefronts in the background",
    4:  "Yuto's dimly lit bedroom at night, single lamp, screentone shading",
    5:  "Yuto's dimly lit bedroom at night, single lamp, screentone shading, focus on the phone",
    6:  "Ren's cluttered private room, cyberpunk research wall with photographs and notes, monitors, late evening",
    7:  "Ren's cluttered private room with monitors and notes on the wall, dim ambient screen glow, late night",
    8:  "Yuto's dimly lit bedroom, light filtering through curtains, screentone shading",
    9:  "city street near the school, daytime, modern Japanese urban architecture",
    10: "school classroom or hallway, daytime",
    11: "Yuto's bedroom or living room, daytime",
    12: "Yuto's family living room, soft daylight",
    13: "Yuto's family living room, soft daylight, mother present",
    14: "school courtyard or hallway",
    15: "school classroom or hallway",
    16: "school setting",
    17: "Yuto's bedroom",
    18: "Yuto's bedroom",
    19: "Nei's apartment or Ren's room",
    20: "Ren's room with monitors, late at night",
    21: "cyberspace abstract environment, glowing data streams, dark background",
    22: "school setting",
    23: "school setting",
    24: "Ren's room with monitors",
    25: "Yuto's bedroom",
    26: "school classroom or hallway",
    27: "cyber defense agency virtual headquarters, holographic displays",
    28: "Yuto's bedroom",
    29: "Yuto's bedroom or family living room",
    30: "school courtyard or rooftop, daytime",
    31: "city street, daytime",
    32: "Akabane Hachiman shrine, traditional Japanese setting",
    33: "Akabane Hachiman shrine, traditional Japanese setting",
    34: "school classroom",
    35: "Ren's room with monitors",
    36: "school classroom",
    37: "cyber defense agency virtual office",
    38: "Yuto's bedroom",
    39: "school setting",
    40: "school setting",
    41: "school setting",
    42: "school setting",
    43: "school setting",
    44: "school setting, final reveal",
    45: "school setting, epilogue",
}


def panel_prompt(panel: dict, focused_char: str | None,
                 focused_chars: tuple = ()) -> str:
    # Style with weight emphasis — the prompt should be dominated by the
    # "monochrome inked manga page" cue so the model doesn't drift into
    # colored anime / watercolor / soft sketch interpretations.
    style = ("(monochrome inked manga panel:1.4), "
             "(sharp black ink lines:1.3), (screentone:1.2), "
             "(high contrast B&W:1.3), cinematic chiaroscuro, "
             "(shonen-jump style:1.2), detailed background, "
             "environment visible, professional manga page")
    shot = panel.get("shot", "medium").lower()
    visual = panel.get("visual", "")
    page_num = panel.get("pageNum", 0)
    env_hint = PAGE_ENVIRONMENT.get(page_num, "")
    tone = panel.get("tone", "")
    parts = []
    # If multiple focused characters with LoRAs, concatenate their tag
    # prompts (each carries its own LoRA trigger token).
    if focused_chars:
        for ch in focused_chars:
            tags = char_tag_prompt(ch, with_lora_token=True)
            if tags:
                parts.append(tags)
        # Cue the model that multiple people are in frame
        if len(focused_chars) >= 2:
            parts.append(f"{len(focused_chars)} characters in frame")
    elif focused_char and focused_char in CHAR_LORA:
        parts.append(char_tag_prompt(focused_char, with_lora_token=True))
    parts.append(f"{shot} shot")
    parts.append(visual)
    if env_hint:
        parts.append(f"setting: {env_hint}")
    if tone:
        parts.append(f"tone: {tone}")
    parts.append(style)
    return ", ".join(p for p in parts if p)


# ── SFX auto-placement ─────────────────────────────────────────────────────
# Map (sceneSubject lowercase or substring) -> (text, style_hints)
_SFX_MAP = {
    "notification sound":    ("ピロン",   {"font_size": 78, "color": "white", "stroke_color": "black", "stroke_width": 6, "motion_lines": 8, "rotation_deg": -6.0}),
    "flood of notifications":("ピピッ",   {"font_size": 88, "color": "white", "stroke_color": "black", "stroke_width": 7, "motion_lines": 12, "rotation_deg": -8.0}),
    "phone vibration":       ("ブルル",   {"font_size": 70, "color": "white", "stroke_color": "black", "stroke_width": 5, "motion_lines": 10}),
    "loud sound":            ("ドーン",   {"font_size": 110,"color": "white", "stroke_color": "black", "stroke_width": 9, "motion_lines": 16, "rotation_deg": -12.0}),
    "footsteps":             ("コツコツ", {"font_size": 56, "color": "white", "stroke_color": "black", "stroke_width": 4}),
    "alarm":                 ("リーン",   {"font_size": 90, "color": "yellow","stroke_color": "black", "stroke_width": 7, "motion_lines": 12}),
    "knock":                 ("コンコン", {"font_size": 70, "color": "white", "stroke_color": "black", "stroke_width": 5}),
    "tap":                   ("ピッ",     {"font_size": 60, "color": "white", "stroke_color": "black", "stroke_width": 4}),
    "scream":                ("キャー",   {"font_size": 120,"color": "white", "stroke_color": "black", "stroke_width": 9, "motion_lines": 16, "rotation_deg": -10.0}),
    "explosion":             ("バーン",   {"font_size": 130,"color": "white", "stroke_color": "black", "stroke_width": 10, "motion_lines": 20, "rotation_deg": -8.0}),
    "silence":               ("...",      {"font_size": 50, "color": "white", "stroke_color": "black", "stroke_width": 3}),
}

def derive_sfx(panel: dict) -> dict | None:
    """Look at sceneSubject / props / tone and return SFX overlay params,
    or None when the panel doesn't need a sound effect."""
    subj = (panel.get("sceneSubject") or "").lower().strip()
    if not subj:
        return None
    # Best match against the table
    for key, (text, style) in _SFX_MAP.items():
        if key in subj:
            # Tone-driven adjustment
            tone = (panel.get("tone") or "").lower()
            if "ominous" in tone:
                style = {**style, "color": "white", "stroke_width": style.get("stroke_width", 4) + 2}
            elif "dramatic" in tone or "intense" in tone:
                style = {**style, "stroke_width": style.get("stroke_width", 4) + 1,
                          "motion_lines": max(style.get("motion_lines", 0), 10)}
            return {"text": text, **style}
    return None


def _bubble_anchor_for_slot(slot: dict) -> tuple[str, str]:
    """Pick a bubble anchor + tail direction based on the panel's position
    on the page (heuristic). Returns (anchor, tail_dir)."""
    # If panel is on the right half → anchor top-right, tail down-left
    cx = slot["x"] + slot["w"] // 2
    cy = slot["y"] + slot["h"] // 2
    is_right = cx >= PAGE_W // 2
    is_top = cy <= PAGE_H // 2
    if is_top:
        if is_right: return "top-right", "down-left"
        return "top-left", "down-right"
    if is_right: return "bottom-right", "up-left"
    return "bottom-left", "up-right"


def build_workflow(page_num: int) -> dict:
    manifest = json.loads((EPISODE / "image-gen-manifest.json").read_text())
    page_panels = sorted(
        (p for p in manifest["panels"] if p["pageNum"] == page_num),
        key=lambda p: p["panelIndex"],
    )
    if not page_panels:
        raise ValueError(f"no panels for page {page_num}")

    layout = compute_layout(page_panels)
    slot_by_idx = {s["idx"]: s for s in layout}

    # Build per-character LoRA chains + multi-character blend chains.
    # Cache by sorted-char-tuple so panels with the same character set
    # share one chain.
    needed_combos: set[tuple] = set()
    for p in page_panels:
        focused = tuple(sorted(c for c in (p.get("focusedCharacters") or [])
                                if c in CHAR_LORA))
        if focused:
            needed_combos.add(focused)

    wf: dict = {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": "animagine-xl-4.0.safetensors"}},
    }
    model_refs: dict[tuple, list] = {(): ["1", 0]}
    next_id = 100
    for combo in needed_combos:
        # Chain all LoRAs at half-strength when multiple, full strength solo
        strength = 0.85 if len(combo) == 1 else max(0.45, 0.85 / len(combo))
        cur = ["1", 0]
        for char in combo:
            lid = str(next_id); next_id += 1
            wf[lid] = {"class_type": "LoraLoaderModelOnly", "inputs": {
                         "model": cur,
                         "lora_name": CHAR_LORA[char],
                         "strength_model": strength}}
            cur = [lid, 0]
        model_refs[combo] = cur

    panel_image_ids: dict[int, list] = {}
    next_id = 1000
    for slot in layout:
        idx = slot["idx"]
        p = slot["panel"]
        focused_chars = tuple(sorted(c for c in (p.get("focusedCharacters") or [])
                                      if c in CHAR_LORA))
        model_ref = model_refs.get(focused_chars, model_refs[()])
        focused = focused_chars[0] if focused_chars else None

        prompt = panel_prompt(p, focused, focused_chars)
        # Aspect: pick landscape latent for spread / large rows; portrait otherwise
        size = (p.get("panelLayout", {}).get("gh:size") or "medium").lower()
        is_landscape = size == "spread" or slot["w"] > slot["h"] * 1.2
        if is_landscape:
            w_lat, h_lat = 1216, 832
        else:
            w_lat, h_lat = 832, 1216

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
                      "seed": page_num * 100000 + idx * 1009,
                      "steps": 28, "cfg": 6.0,
                      "sampler_name": "dpmpp_2m_sde", "scheduler": "karras",
                      "denoise": 1.0,
                      "model": model_ref,
                      "positive": [pos_id, 0], "negative": [neg_id, 0],
                      "latent_image": [lat_id, 0]}}
        # ── Path A: img2img polish pass ─────────────────────────────
        # Re-encode the decoded panel into latent space and apply a
        # short low-denoise pass with a manga-style-only prompt. This
        # unifies tone across panels (no more brown / colored drift)
        # and sharpens the ink lines without changing composition or
        # character identity.
        dec1_id = str(next_id); next_id += 1
        wf[dec1_id] = {"class_type": "VAEDecode",
                       "inputs": {"samples": [ks_id, 0], "vae": ["1", 2]}}
        enc_id = str(next_id); next_id += 1
        wf[enc_id] = {"class_type": "VAEEncode",
                      "inputs": {"pixels": [dec1_id, 0], "vae": ["1", 2]}}
        polish_pos = str(next_id); next_id += 1
        wf[polish_pos] = {"class_type": "CLIPTextEncode", "inputs": {
                            "text": ("(monochrome inked manga panel:1.5), "
                                     "(sharp black ink lines:1.4), "
                                     "(B&W manga page:1.4), screentone, "
                                     "high contrast, professional manga, "
                                     "shonen-jump style, no color"),
                            "clip": ["1", 1]}}
        polish_neg = str(next_id); next_id += 1
        wf[polish_neg] = {"class_type": "CLIPTextEncode", "inputs": {
                            "text": ("color illustration, soft sketch, "
                                     "watercolor, dull tones, brown sepia, "
                                     "muted colors, washed out, low contrast"),
                            "clip": ["1", 1]}}
        ks2_id = str(next_id); next_id += 1
        wf[ks2_id] = {"class_type": "KSampler", "inputs": {
                       "seed": page_num * 100000 + idx * 1009 + 7,
                       "steps": 14, "cfg": 5.0,
                       "sampler_name": "dpmpp_2m_sde", "scheduler": "karras",
                       "denoise": 0.30,
                       "model": model_ref,
                       "positive": [polish_pos, 0], "negative": [polish_neg, 0],
                       "latent_image": [enc_id, 0]}}
        dec_id = str(next_id); next_id += 1
        wf[dec_id] = {"class_type": "VAEDecode",
                      "inputs": {"samples": [ks2_id, 0], "vae": ["1", 2]}}
        panel_image_ids[idx] = [dec_id, 0]

    # Page canvas + paste
    canvas_id = str(next_id); next_id += 1
    wf[canvas_id] = {"class_type": "MangakaPageCanvas",
                     "inputs": {"width": PAGE_W, "height": PAGE_H}}
    page_ref = [canvas_id, 0]
    for slot in layout:
        paste_id = str(next_id); next_id += 1
        wf[paste_id] = {"class_type": "MangakaPanelPaste", "inputs": {
                          "page": page_ref, "panel": panel_image_ids[slot["idx"]],
                          "x": slot["x"], "y": slot["y"],
                          "w": slot["w"], "h": slot["h"], "border_width": 3}}
        page_ref = [paste_id, 0]

    # SFX auto-placement — derived from sceneSubject + tone
    for slot in layout:
        sfx = derive_sfx(slot["panel"])
        if not sfx:
            continue
        # Position: place the SFX in the corner OPPOSITE to where the
        # bubble will be drawn so they don't fight. Bubble anchor was
        # computed earlier — re-compute the same heuristic here.
        anchor, _tdir = _bubble_anchor_for_slot(slot)
        # Bubble top-right -> SFX top-left of panel, etc.
        if "right" in anchor:
            sfx_x = slot["x"] + 24
        else:
            sfx_x = slot["x"] + slot["w"] - 280
        if "top" in anchor:
            sfx_y = slot["y"] + slot["h"] - 160
        else:
            sfx_y = slot["y"] - 20
        sfx_id = str(next_id); next_id += 1
        wf[sfx_id] = {"class_type": "MangakaSFX", "inputs": {
                        "image": page_ref,
                        "text": sfx["text"],
                        "x": sfx_x, "y": sfx_y,
                        "font_size": sfx.get("font_size", 80),
                        "rotation_deg": sfx.get("rotation_deg", -6.0),
                        "color": sfx.get("color", "white"),
                        "stroke_color": sfx.get("stroke_color", "black"),
                        "stroke_width": sfx.get("stroke_width", 6),
                        "motion_lines": sfx.get("motion_lines", 0),
                        "motion_line_length": sfx.get("motion_line_length", 70)}}
        page_ref = [sfx_id, 0]

    # Dialogue bubbles — TALL/NARROW for proper vertical Japanese manga
    for slot in layout:
        p = slot["panel"]
        dlgs = [d for d in (p.get("dialogues") or []) if (d.get("text") or "")]
        anchor, tail_dir = _bubble_anchor_for_slot(slot)
        for di, d in enumerate(dlgs):
            text = latinize(d.get("text") or "")
            # Truncate excessive runs — manga bubbles aren't paragraphs.
            if len(text) > 24:
                text = text[:24] + "…"
            cur_anchor = anchor
            if di > 0:
                if "top" in anchor: cur_anchor = anchor.replace("top", "bottom")
            bub_id = str(next_id); next_id += 1
            style = "shout" if re.search(r"[！？]|[!?][!?]", text) else "normal"
            # Compute bubble dimensions so vertical text looks TALL:
            # min_height = enough rows that the longest column packs many
            # characters, so the bubble stays NARROW (manga style).
            text_len = len(text)
            font_size = 28
            # Target 5-7 chars per column so a 21-char line wraps to 3-4 cols
            chars_per_col = 6
            line_h = int(font_size * 1.25)
            min_h = int(chars_per_col * line_h + 2 * 14) + 12   # +padding
            # Limit panel-relative max so bubble doesn't take 70% of the panel
            max_h = max(140, min(slot["h"] - 30, 320))
            min_h = min(min_h, max_h)
            # Width is small — 2-4 columns max
            width = font_size + 8 + 28  # 1 col baseline + padding
            wf[bub_id] = {"class_type": "MangakaMangaBubble", "inputs": {
                            "image": page_ref,
                            "text": text,
                            "panel_x": slot["x"], "panel_y": slot["y"],
                            "panel_w": slot["w"], "panel_h": slot["h"],
                            "anchor": cur_anchor,
                            "tail_dir": tail_dir,
                            "tail_length": 16,
                            "width": width,
                            "min_height": min_h,
                            "font_size": font_size,
                            "padding": 14,
                            "vertical": True,
                            "style": style,
                            "outline_width": 4,
                            "overflow": 6}}
            page_ref = [bub_id, 0]

    save_id = str(next_id); next_id += 1
    wf[save_id] = {"class_type": "SaveImage", "inputs": {
                     "filename_prefix": f"mangaka-page-arc01-p{page_num:02d}",
                     "images": page_ref}}
    return wf, len(page_panels)


def render_page(page_num: int) -> Path | None:
    print(f"\n=== Page {page_num} ===")
    wf, n_panels = build_workflow(page_num)
    print(f"  {n_panels} panels, {len(wf)} nodes")
    pid = submit(wf)
    if not pid:
        return None
    print(f"  prompt_id: {pid}")
    t0 = time.monotonic()
    e = wait_for(pid, timeout=1800)
    el = time.monotonic() - t0
    s = (e.get("status") or {}).get("status_str")
    outs = e.get("outputs") or {}
    if s == "error" or not outs:
        print(f"  FAIL ({el:.0f}s) status={s}")
        for m in (e.get("status", {}).get("messages") or [])[-3:]:
            print(f"    {m}")
        return None
    last = sorted([(int(nid), img) for nid, out in outs.items()
                   for img in out.get("images", [])], key=lambda x: x[0])[-1]
    print(f"  done in {el:.0f}s -> {last[1]['filename']}")
    data = fetch_view(last[1]["filename"])
    local = Path(f"/tmp/gh-arc0-1-p{page_num:02d}-final.png")
    local.write_bytes(data)
    return local


def main():
    if len(sys.argv) < 2:
        print("usage: render-arc01-page.py <page_num | --all | --range N,M,...>")
        return 1

    arg = sys.argv[1]
    skip_existing = "--skip-existing" in sys.argv
    if arg == "--all":
        pages = list(range(0, 46))
    elif arg == "--range":
        pages = [int(x) for x in sys.argv[2].split(",")]
    elif arg == "--resume":
        # Render pages 0..45 that don't already have a /tmp output
        from pathlib import Path as _P
        done = set()
        for f in _P("/tmp").iterdir():
            n = f.name
            if n.startswith("gh-arc0-1-p") and n.endswith("-final.png"):
                try:
                    done.add(int(n[len("gh-arc0-1-p"):-len("-final.png")]))
                except: pass
        pages = [n for n in range(0, 46) if n not in done]
        print(f"resume mode: {len(done)} done, {len(pages)} pending: {pages[:10]}{'...' if len(pages)>10 else ''}")
    else:
        pages = [int(arg)]

    results = []
    for pn in pages:
        if skip_existing and Path(f"/tmp/gh-arc0-1-p{pn:02d}-final.png").exists():
            print(f"skip page {pn} (existing)")
            continue
        try:
            local = render_page(pn)
            results.append((pn, local))
        except Exception as ex:
            print(f"  EXCEPTION page {pn}: {ex}")
            results.append((pn, None))

    print(f"\n=== summary: {sum(1 for _, l in results if l)}/{len(results)} pages rendered ===")
    for pn, local in results:
        print(f"  page {pn}: {local or 'FAILED'}")


if __name__ == "__main__":
    sys.exit(main() or 0)
