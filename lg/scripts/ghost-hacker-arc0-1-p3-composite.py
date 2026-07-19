"""Composite the 7 panels of page 3 into a 3-row manga layout with
dialogue bubbles, then upload to ComfyUI's input dir."""
import io, json, os, time, urllib.request
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

COMFY = "http://192.168.1.70:8188"
ROOT = Path("/Users/junkawasaki/etzhayyim/etzhayyim-root/60-apps/etzhayyim-project-mangaka/data/ghosthacker")
EPISODE = ROOT / "resources/episodes/arc0-1-origin"
PAGE = 3

manifest = json.loads((EPISODE / "image-gen-manifest.json").read_text())
panels = [p for p in manifest["panels"] if p["pageNum"] == PAGE]

W, H = 1280, 1817
margin = 40
gutter = 16
canvas = Image.new("RGB", (W, H), "white")
draw = ImageDraw.Draw(canvas)

# 3-row layout: row1 (3 panels), row2 (2 panels), row3 (2 panels)
row1_y, row1_h = margin, 540
slots_r1 = [
    (margin,                          row1_y, 280, row1_h),   # p1
    (margin+280+gutter,               row1_y, 540, row1_h),   # p2
    (margin+280+gutter+540+gutter,    row1_y, 1200-280-540-2*gutter, row1_h),  # p3
]
row2_y = row1_y + row1_h + gutter
row2_h = 540
slots_r2 = [
    (margin,                          row2_y, 720, row2_h),   # p4
    (margin+720+gutter,               row2_y, 1200-720-gutter, row2_h),  # p5
]
row3_y = row2_y + row2_h + gutter
row3_h = 597
slots_r3 = [
    (margin,                          row3_y, 480, row3_h),   # p6
    (margin+480+gutter,               row3_y, 1200-480-gutter, row3_h),  # p7
]
slots = slots_r1 + slots_r2 + slots_r3

# Japanese font
font_paths = [
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Hiragino Mincho ProN.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
]
font_path = next((f for f in font_paths if os.path.exists(f)), None)
font_dialog = ImageFont.truetype(font_path, 20) if font_path else ImageFont.load_default()
font_speaker = ImageFont.truetype(font_path, 16) if font_path else ImageFont.load_default()

for slot, mp in zip(slots, panels):
    idx = mp["panelIndex"]
    local = Path(f"/tmp/gh-arc0-1-p{PAGE}-panel{idx}.png")
    if not local.exists():
        print(f"  skip panel {idx}: not found")
        continue
    bx, by, bw, bh = slot
    target_w = bw - gutter
    target_h = bh - gutter
    img = Image.open(local).convert("RGB")
    sr = img.width / img.height
    dr = target_w / target_h
    if sr > dr:
        nh = target_h; nw = int(round(target_h * sr))
    else:
        nw = target_w; nh = int(round(target_w / sr))
    img = img.resize((nw, nh), Image.LANCZOS)
    ox = (nw - target_w) // 2
    oy = (nh - target_h) // 2
    img = img.crop((ox, oy, ox + target_w, oy + target_h))
    canvas.paste(img, (bx + gutter // 2, by + gutter // 2))
    draw.rectangle(
        [(bx + gutter // 2, by + gutter // 2),
         (bx + gutter // 2 + target_w - 1, by + gutter // 2 + target_h - 1)],
        outline="black", width=3,
    )

    # Dialogue bubble(s)
    dialogues = mp.get("dialogues") or []
    for i, d in enumerate(dialogues):
        text = d.get("text") or ""
        speaker = d.get("speaker") or ""
        if not text:
            continue
        disp = text if len(text) <= 18 else text[:18] + "…"
        spk = f"{speaker}" if speaker else ""
        bubble_w = min(target_w - 30, 360)
        bubble_h = 56
        bubble_x = bx + 15
        bubble_y = by + target_h - 70 - (i * (bubble_h + 6))
        draw.rounded_rectangle(
            [bubble_x, bubble_y, bubble_x + bubble_w, bubble_y + bubble_h],
            radius=12, fill="white", outline="black", width=2,
        )
        draw.text((bubble_x + 10, bubble_y + 4), spk, font=font_speaker, fill="#666")
        draw.text((bubble_x + 10, bubble_y + 24), disp, font=font_dialog, fill="black")

out_path = f"/tmp/gh-arc0-1-p{PAGE}.png"
canvas.save(out_path, "PNG", optimize=True)
print(f"page saved -> {out_path}")

# Upload back to ComfyUI input/
data = Path(out_path).read_bytes()
boundary = "----composite_p3"
body = (
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="image"; filename="gh-arc0-1-p3-composite.png"\r\n'
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
    print(f"uploaded to ComfyUI as: {j['name']}")
