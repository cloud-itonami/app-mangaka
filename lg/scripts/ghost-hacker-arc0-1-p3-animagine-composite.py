import io, json, os, urllib.request
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

COMFY = "http://192.168.1.70:8188"
ROOT = Path("/Users/junkawasaki/etzhayyim/etzhayyim-root/60-apps/etzhayyim-project-mangaka/data/ghosthacker")
EPISODE = ROOT / "resources/episodes/arc0-1-origin"
PAGE = 3
manifest = json.loads((EPISODE / "image-gen-manifest.json").read_text())
panels = [p for p in manifest["panels"] if p["pageNum"] == PAGE]

W, H = 1280, 1817; margin = 40; gutter = 16
canvas = Image.new("RGB", (W, H), "white")
draw = ImageDraw.Draw(canvas)

row1_y, row1_h = margin, 540
slots = [
    (margin,                          row1_y, 280, row1_h),
    (margin+280+gutter,               row1_y, 540, row1_h),
    (margin+280+gutter+540+gutter,    row1_y, 1200-280-540-2*gutter, row1_h),
]
row2_y = row1_y + row1_h + gutter; row2_h = 540
slots += [
    (margin,                          row2_y, 720, row2_h),
    (margin+720+gutter,               row2_y, 1200-720-gutter, row2_h),
]
row3_y = row2_y + row2_h + gutter; row3_h = 597
slots += [
    (margin,                          row3_y, 480, row3_h),
    (margin+480+gutter,               row3_y, 1200-480-gutter, row3_h),
]

fp = next((f for f in [
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Hiragino Mincho ProN.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
] if os.path.exists(f)), None)
f_dlg = ImageFont.truetype(fp, 20) if fp else ImageFont.load_default()
f_spk = ImageFont.truetype(fp, 16) if fp else ImageFont.load_default()

for slot, mp in zip(slots, panels):
    idx = mp["panelIndex"]
    local = Path(f"/tmp/gh-arc0-1-p{PAGE}-anim-panel{idx}.png")
    if not local.exists(): continue
    bx, by, bw, bh = slot
    tw, th = bw - gutter, bh - gutter
    img = Image.open(local).convert("RGB")
    sr, dr = img.width/img.height, tw/th
    if sr > dr:
        nh, nw = th, int(round(th * sr))
    else:
        nw, nh = tw, int(round(tw / sr))
    img = img.resize((nw, nh), Image.LANCZOS)
    ox, oy = (nw - tw) // 2, (nh - th) // 2
    img = img.crop((ox, oy, ox + tw, oy + th))
    canvas.paste(img, (bx + gutter // 2, by + gutter // 2))
    draw.rectangle(
        [(bx + gutter // 2, by + gutter // 2),
         (bx + gutter // 2 + tw - 1, by + gutter // 2 + th - 1)],
        outline="black", width=3,
    )
    for i, d in enumerate(mp.get("dialogues") or []):
        text = d.get("text") or ""
        if not text: continue
        disp = text if len(text) <= 18 else text[:18] + "…"
        bw_b, bh_b = min(tw - 30, 360), 56
        bx_b = bx + 15
        by_b = by + th - 70 - (i * (bh_b + 6))
        draw.rounded_rectangle(
            [bx_b, by_b, bx_b + bw_b, by_b + bh_b],
            radius=12, fill="white", outline="black", width=2,
        )
        draw.text((bx_b + 10, by_b + 4), d.get("speaker") or "", font=f_spk, fill="#666")
        draw.text((bx_b + 10, by_b + 24), disp, font=f_dlg, fill="black")

out = "/tmp/gh-arc0-1-p3-anim.png"
canvas.save(out, "PNG", optimize=True)
print(f"page saved -> {out}")

data = Path(out).read_bytes()
boundary = "----c3a"
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
    print(f"uploaded: {json.loads(r.read())['name']}")
