$dest = "C:\Users\gad\ComfyUI\ComfyUI_windows_portable\ComfyUI\custom_nodes\ManagaTextOverlay"
$nodesPy = @'
"""mangaka v4: short tail, no margin (overflow OK), centered text,
   no speaker label, wider vertical columns."""
import math, os
import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont

_FONT_CANDIDATES = [
    "C:/Windows/Fonts/YuGothM.ttc", "C:/Windows/Fonts/yumin.ttf",
    "C:/Windows/Fonts/meiryo.ttc", "C:/Windows/Fonts/msgothic.ttc",
    "C:/Windows/Fonts/arial.ttf",
]
_SFX_FONT_CANDIDATES = [
    "C:/Windows/Fonts/YuGothB.ttc", "C:/Windows/Fonts/msgothic.ttc",
    "C:/Windows/Fonts/yumin.ttf", "C:/Windows/Fonts/arial.ttf",
]

def _find_font(size, sfx=False):
    for p in (_SFX_FONT_CANDIDATES if sfx else _FONT_CANDIDATES):
        if os.path.exists(p): return ImageFont.truetype(p, size)
    return ImageFont.load_default()

def _t2pil(image):
    arr = (image[0].cpu().numpy() * 255.0).clip(0, 255).astype("uint8")
    return Image.fromarray(arr).convert("RGB")

def _pil2t(im):
    arr = np.array(im.convert("RGB")).astype("float32") / 255.0
    return torch.from_numpy(arr).unsqueeze(0)

_VERT_ROTATE = set("ー－—…・、。「」『』（）()｛｝[]【】")

def _draw_vertical(draw, image, text, x, y, font, fill, line_height, col_width, max_h):
    """Vertical Japanese: chars top->bottom, columns right->left.
    Per-column auto-wrap when an explicit newline is missing."""
    paragraphs = text.split("\n")
    columns = []
    chars_per_col = max(1, max_h // line_height)
    if len(paragraphs) > 1:
        columns = paragraphs
    else:
        for i in range(0, len(text), chars_per_col):
            columns.append(text[i:i+chars_per_col])
    n = len(columns)
    for ci, col in enumerate(columns):
        cx = x + (n - 1 - ci) * col_width
        # Center each char horizontally within its column
        for ri, ch in enumerate(col):
            ry = y + ri * line_height
            ch_w = draw.textlength(ch, font=font)
            char_x = cx + max(0, (col_width - int(ch_w)) // 2)
            if ch in _VERT_ROTATE:
                tmp = Image.new("RGBA", (col_width, line_height), (0,0,0,0))
                ImageDraw.Draw(tmp).text((0, 0), ch, font=font, fill=fill)
                tmp = tmp.rotate(-90, resample=Image.BICUBIC)
                image.paste(tmp, (cx, ry), tmp)
            else:
                draw.text((char_x, ry), ch, font=font, fill=fill)


def _bubble_corner(panel_x, panel_y, panel_w, panel_h, bw, bh, anchor,
                    overflow=10):
    """Top-left of bubble at panel corner, with optional small overflow
    outward (negative margin) for that 'sticks to / spills past the
    panel' manga look."""
    if anchor == "top-right":
        return panel_x + panel_w - bw + overflow, panel_y - overflow
    if anchor == "top-left":
        return panel_x - overflow, panel_y - overflow
    if anchor == "bottom-right":
        return panel_x + panel_w - bw + overflow, panel_y + panel_h - bh + overflow
    if anchor == "bottom-left":
        return panel_x - overflow, panel_y + panel_h - bh + overflow
    if anchor == "top-center":
        return panel_x + (panel_w - bw)//2, panel_y - overflow
    return panel_x + (panel_w - bw)//2, panel_y + panel_h - bh + overflow


class MangakaTextOverlay:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "image": ("IMAGE",), "text": ("STRING", {"multiline": True, "default": ""}),
            "x": ("INT", {"default": 30, "min": -10000, "max": 10000}),
            "y": ("INT", {"default": 30, "min": -10000, "max": 10000}),
            "font_size": ("INT", {"default": 48, "min": 8, "max": 400}),
            "color": ("STRING", {"default": "white"}),
            "stroke_color": ("STRING", {"default": "black"}),
            "stroke_width": ("INT", {"default": 4, "min": 0, "max": 20}),
        }}
    RETURN_TYPES = ("IMAGE",); FUNCTION = "run"; CATEGORY = "mangaka"
    def run(self, image, text, x, y, font_size, color, stroke_color, stroke_width):
        im = _t2pil(image); d = ImageDraw.Draw(im); f = _find_font(font_size)
        for i, line in enumerate(text.split("\n")):
            d.text((x, y + i * int(font_size*1.2)), line, font=f, fill=color,
                   stroke_width=stroke_width, stroke_fill=stroke_color)
        return (_pil2t(im),)


class MangakaSpeechBubble:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "image": ("IMAGE",), "speaker": ("STRING", {"default": ""}),
            "text": ("STRING", {"multiline": True, "default": ""}),
            "anchor": (["bottom-center", "top-center", "bottom-left", "top-right"],),
            "max_width_frac": ("FLOAT", {"default": 0.72, "min": 0.2, "max": 1.0, "step": 0.05}),
            "font_size": ("INT", {"default": 22, "min": 8, "max": 80}),
            "padding": ("INT", {"default": 14, "min": 0, "max": 80}),
        }}
    RETURN_TYPES = ("IMAGE",); FUNCTION = "run"; CATEGORY = "mangaka"
    def run(self, image, speaker, text, anchor, max_width_frac, font_size, padding):
        im = _t2pil(image); W, H = im.size
        d = ImageDraw.Draw(im); f = _find_font(font_size)
        bw = int(d.textlength(text, font=f)) + 2*padding
        bh = int(font_size*1.2) + 2*padding
        if anchor == "bottom-center": bx=(W-bw)//2; by=H-bh-20
        elif anchor == "top-center":   bx=(W-bw)//2; by=20
        elif anchor == "bottom-left":  bx=20; by=H-bh-20
        else:                          bx=W-bw-20; by=20
        d.rounded_rectangle([bx,by,bx+bw,by+bh], radius=int(font_size*.6),
                            fill="white", outline="black", width=2)
        d.text((bx+padding, by+padding), text, font=f, fill="black")
        return (_pil2t(im),)


class MangakaMangaBubble:
    """v4: panel corner anchor with optional overflow, short stub tail
    (small bit out of bubble — not extending to face), centered text,
    NO speaker label (manga doesn't tag bubbles), wider vertical columns."""
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "image": ("IMAGE",),
            "text": ("STRING", {"multiline": True, "default": ""}),
            "panel_x": ("INT", {"default": 0, "min": 0, "max": 10000}),
            "panel_y": ("INT", {"default": 0, "min": 0, "max": 10000}),
            "panel_w": ("INT", {"default": 720, "min": 32, "max": 10000}),
            "panel_h": ("INT", {"default": 540, "min": 32, "max": 10000}),
            "anchor": (["top-right", "top-left", "bottom-right", "bottom-left",
                         "top-center", "bottom-center"],),
            "tail_dir": (["auto", "down", "up", "left", "right", "down-left",
                          "down-right", "up-left", "up-right", "none"],),
            "tail_length": ("INT", {"default": 16, "min": 0, "max": 200}),
            "width": ("INT", {"default": 200, "min": 60, "max": 5000}),
            "min_height": ("INT", {"default": 140, "min": 30, "max": 5000}),
            "font_size": ("INT", {"default": 24, "min": 8, "max": 120}),
            "padding": ("INT", {"default": 14, "min": 0, "max": 80}),
            "vertical": ("BOOLEAN", {"default": True}),
            "style": (["normal", "shout", "thought", "whisper", "narration"],),
            "outline_width": ("INT", {"default": 3, "min": 0, "max": 12}),
            "overflow": ("INT", {"default": 8, "min": 0, "max": 100}),
        }}
    RETURN_TYPES = ("IMAGE",); FUNCTION = "run"; CATEGORY = "mangaka"

    def run(self, image, text, panel_x, panel_y, panel_w, panel_h, anchor,
            tail_dir, tail_length, width, min_height, font_size, padding,
            vertical, style, outline_width, overflow):
        im = _t2pil(image); W, H = im.size
        d = ImageDraw.Draw(im); f = _find_font(font_size)
        line_h = int(font_size * 1.25)
        col_w = font_size + 8       # WIDER columns (was font_size + 2)

        # Compute bubble dimensions tight around text + padding
        if vertical:
            paragraphs = text.split("\n")
            if len(paragraphs) > 1:
                n_cols = len(paragraphs)
                max_chars = max(len(p) for p in paragraphs)
            else:
                max_chars_per_col = max(1, (min_height - 2*padding) // line_h)
                n_cols = max(1, math.ceil(len(text) / max_chars_per_col))
                max_chars = max_chars_per_col
            text_w = n_cols * col_w
            text_h = max_chars * line_h
        else:
            chars_per_line = max(1, (width - 2*padding) // (font_size//2 + 4))
            lines = []
            for para in text.split("\n"):
                for i in range(0, len(para), chars_per_line):
                    lines.append(para[i:i+chars_per_line])
            text_w = max((int(d.textlength(L, font=f)) for L in lines), default=0)
            text_h = len(lines) * line_h

        bw = max(width, text_w + 2*padding)
        bh = max(min_height, text_h + 2*padding)

        bx, by = _bubble_corner(panel_x, panel_y, panel_w, panel_h, bw, bh,
                                 anchor, overflow=overflow)
        cx, cy = bx + bw//2, by + bh//2

        # Bubble shape
        if style == "shout":
            spikes = 16; pts = []
            rx, ry = bw/2, bh/2
            for i in range(spikes*2):
                ang = (i / (spikes*2)) * 2*math.pi
                rm = 1.0 if i%2==0 else 0.75
                pts.append((cx + math.cos(ang)*rx*rm, cy + math.sin(ang)*ry*rm))
            d.polygon(pts, fill="white", outline="black")
            for _ in range(outline_width):
                d.polygon(pts, fill=None, outline="black")
        elif style == "thought":
            d.rounded_rectangle([bx, by, bx+bw, by+bh], radius=int(font_size*1.4),
                                 fill="white", outline="black", width=outline_width)
            for ad in (0, 60, 120, 180, 240, 300):
                ang = math.radians(ad)
                px = int(cx + math.cos(ang)*(bw/2)); py = int(cy + math.sin(ang)*(bh/2))
                r = font_size//2 + 4
                d.ellipse([px-r, py-r, px+r, py+r], fill="white",
                          outline="black", width=2)
        elif style == "whisper":
            d.rounded_rectangle([bx, by, bx+bw, by+bh],
                                 radius=int(font_size*0.4), fill="white")
            dash = 8; gap = 6
            cx2 = bx
            while cx2 < bx+bw:
                d.line([(cx2, by),(min(cx2+dash, bx+bw), by)], fill="black", width=outline_width)
                d.line([(cx2, by+bh),(min(cx2+dash, bx+bw), by+bh)], fill="black", width=outline_width)
                cx2 += dash+gap
            cy2 = by
            while cy2 < by+bh:
                d.line([(bx, cy2),(bx, min(cy2+dash, by+bh))], fill="black", width=outline_width)
                d.line([(bx+bw, cy2),(bx+bw, min(cy2+dash, by+bh))], fill="black", width=outline_width)
                cy2 += dash+gap
        elif style == "narration":
            d.rectangle([bx, by, bx+bw, by+bh], fill="white",
                        outline="black", width=outline_width)
        else:
            d.rounded_rectangle([bx, by, bx+bw, by+bh],
                                 radius=int(font_size*0.9),
                                 fill="white", outline="black", width=outline_width)

        # Short STUB tail — direction-controlled, doesn't reach the face
        if tail_dir != "none" and style not in ("narration", "whisper"):
            tdir = tail_dir
            if tdir == "auto":
                # Pick a sensible direction by anchor
                if anchor.startswith("top-"): tdir = "down"
                elif anchor.startswith("bottom-"): tdir = "up"
                else: tdir = "down"
            tail_base = max(font_size, 18)
            tlen = tail_length
            if tdir == "down":
                ax = bx + bw - bw//4
                base_l = (ax - tail_base, by + bh - 1)
                base_r = (ax + tail_base, by + bh - 1)
                tip = (ax + tail_base//2, by + bh + tlen)
            elif tdir == "up":
                ax = bx + bw//4
                base_l = (ax - tail_base, by + 1)
                base_r = (ax + tail_base, by + 1)
                tip = (ax + tail_base//2, by - tlen)
            elif tdir == "left":
                ay = by + bh//2
                base_l = (bx + 1, ay - tail_base)
                base_r = (bx + 1, ay + tail_base)
                tip = (bx - tlen, ay + tail_base//2)
            elif tdir == "right":
                ay = by + bh//2
                base_l = (bx + bw - 1, ay - tail_base)
                base_r = (bx + bw - 1, ay + tail_base)
                tip = (bx + bw + tlen, ay + tail_base//2)
            elif tdir == "down-left":
                base_l = (bx + bw//6 + tail_base, by + bh - 1)
                base_r = (bx + bw//6 - tail_base, by + bh - 1)
                tip = (bx - tlen//2, by + bh + tlen)
            elif tdir == "down-right":
                base_l = (bx + bw*5//6 - tail_base, by + bh - 1)
                base_r = (bx + bw*5//6 + tail_base, by + bh - 1)
                tip = (bx + bw + tlen//2, by + bh + tlen)
            elif tdir == "up-left":
                base_l = (bx + bw//6 + tail_base, by + 1)
                base_r = (bx + bw//6 - tail_base, by + 1)
                tip = (bx - tlen//2, by - tlen)
            else:  # up-right
                base_l = (bx + bw*5//6 - tail_base, by + 1)
                base_r = (bx + bw*5//6 + tail_base, by + 1)
                tip = (bx + bw + tlen//2, by - tlen)

            d.polygon([base_l, base_r, tip], fill="white")
            d.line([base_l, tip], fill="black", width=outline_width)
            d.line([tip, base_r], fill="black", width=outline_width)

        # Text — CENTERED in bubble, NO speaker label
        if vertical:
            inner_w = bw - 2*padding
            inner_h = bh - 2*padding
            block_w = n_cols * col_w
            text_x = bx + padding + max(0, (inner_w - block_w) // 2)
            text_y = by + padding + max(0, (inner_h - text_h) // 2)
            _draw_vertical(d, im, text, text_x, text_y, f, "black",
                           line_h, col_w, inner_h)
        else:
            chars_per_line = max(1, (bw - 2*padding) // (font_size//2 + 4))
            lines = []
            for para in text.split("\n"):
                for i in range(0, len(para), chars_per_line):
                    lines.append(para[i:i+chars_per_line])
            cur_y = by + padding + max(0, (bh - 2*padding - len(lines)*line_h)//2)
            for line in lines:
                lw = int(d.textlength(line, font=f))
                lx = bx + padding + max(0, (bw - 2*padding - lw)//2)
                d.text((lx, cur_y), line, font=f, fill="black")
                cur_y += line_h

        return (_pil2t(im),)


class MangakaSFX:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "image": ("IMAGE",),
            "text": ("STRING", {"multiline": True, "default": "ドーン"}),
            "x": ("INT", {"default": 100, "min": -10000, "max": 10000}),
            "y": ("INT", {"default": 100, "min": -10000, "max": 10000}),
            "font_size": ("INT", {"default": 110, "min": 16, "max": 800}),
            "rotation_deg": ("FLOAT", {"default": -8.0, "min": -90.0, "max": 90.0, "step": 1.0}),
            "color": ("STRING", {"default": "white"}),
            "stroke_color": ("STRING", {"default": "black"}),
            "stroke_width": ("INT", {"default": 8, "min": 0, "max": 40}),
            "motion_lines": ("INT", {"default": 0, "min": 0, "max": 24}),
            "motion_line_length": ("INT", {"default": 80, "min": 0, "max": 800}),
        }}
    RETURN_TYPES = ("IMAGE",); FUNCTION = "run"; CATEGORY = "mangaka"
    def run(self, image, text, x, y, font_size, rotation_deg, color,
            stroke_color, stroke_width, motion_lines, motion_line_length):
        im = _t2pil(image).convert("RGBA")
        f = _find_font(font_size, sfx=True)
        tw = th = 0
        dummy = Image.new("RGBA",(10,10)); dd = ImageDraw.Draw(dummy)
        for line in text.split("\n"):
            bbox = dd.textbbox((0,0), line, font=f, stroke_width=stroke_width)
            tw = max(tw, bbox[2]-bbox[0])
            th += int(font_size*1.15)
        pad = max(stroke_width+4, font_size//2)
        layer = Image.new("RGBA",(tw+2*pad, th+2*pad),(0,0,0,0))
        ld = ImageDraw.Draw(layer); cur_y = pad
        for line in text.split("\n"):
            ld.text((pad, cur_y), line, font=f, fill=color,
                    stroke_width=stroke_width, stroke_fill=stroke_color)
            cur_y += int(font_size*1.15)
        if rotation_deg != 0:
            layer = layer.rotate(rotation_deg, resample=Image.BICUBIC, expand=True)
        lw, lh = layer.size; cx, cy = x+lw//2, y+lh//2
        if motion_lines > 0:
            md = ImageDraw.Draw(im)
            for i in range(motion_lines):
                ang = (i/motion_lines)*2*math.pi
                r0 = max(lw,lh)//2 + 12; r1 = r0 + motion_line_length
                md.line([(cx+math.cos(ang)*r0, cy+math.sin(ang)*r0),
                         (cx+math.cos(ang)*r1, cy+math.sin(ang)*r1)],
                        fill="black", width=max(2, stroke_width//3))
        im.paste(layer, (x, y), layer)
        return (_pil2t(im.convert("RGB")),)


class MangakaPageCanvas:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"width": ("INT",{"default":1280,"min":128,"max":8192}),
                              "height": ("INT",{"default":1817,"min":128,"max":8192})}}
    RETURN_TYPES = ("IMAGE",); FUNCTION = "run"; CATEGORY = "mangaka"
    def run(self, width, height):
        return (_pil2t(Image.new("RGB",(width,height),"white")),)


class MangakaPanelPaste:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "page": ("IMAGE",), "panel": ("IMAGE",),
            "x": ("INT",{"default":40,"min":0,"max":10000}),
            "y": ("INT",{"default":40,"min":0,"max":10000}),
            "w": ("INT",{"default":1200,"min":32,"max":10000}),
            "h": ("INT",{"default":540,"min":32,"max":10000}),
            "border_width": ("INT",{"default":3,"min":0,"max":30}),
        }}
    RETURN_TYPES = ("IMAGE",); FUNCTION = "run"; CATEGORY = "mangaka"
    def run(self, page, panel, x, y, w, h, border_width):
        pi = _t2pil(page); ni = _t2pil(panel)
        sr = ni.width/ni.height; dr = w/h
        if sr > dr: nh = h; nw = int(round(h*sr))
        else: nw = w; nh = int(round(w/sr))
        ni = ni.resize((nw,nh), Image.LANCZOS)
        ox = (nw-w)//2; oy = (nh-h)//2
        ni = ni.crop((ox,oy,ox+w,oy+h))
        pi.paste(ni,(x,y))
        if border_width > 0:
            ImageDraw.Draw(pi).rectangle([(x,y),(x+w-1,y+h-1)],
                                          outline="black", width=border_width)
        return (_pil2t(pi),)


NODE_CLASS_MAPPINGS = {
    "MangakaTextOverlay": MangakaTextOverlay,
    "MangakaSpeechBubble": MangakaSpeechBubble,
    "MangakaMangaBubble": MangakaMangaBubble,
    "MangakaSFX": MangakaSFX,
    "MangakaPageCanvas": MangakaPageCanvas,
    "MangakaPanelPaste": MangakaPanelPaste,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "MangakaTextOverlay": "Mangaka Text Overlay (SFX simple)",
    "MangakaSpeechBubble": "Mangaka Speech Bubble (anchor)",
    "MangakaMangaBubble": "Mangaka Manga Bubble v4 (vertical + corner + stub tail + centered)",
    "MangakaSFX": "Mangaka SFX (styled)",
    "MangakaPageCanvas": "Mangaka Page Canvas",
    "MangakaPanelPaste": "Mangaka Panel Paste",
}
'@
Set-Content -Path "$dest\nodes.py" -Value $nodesPy -NoNewline
Write-Host "v4 nodes written"

# Restart
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*ComfyUI*main.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }
Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" |
    Where-Object { $_.CommandLine -like "*run_comfy*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }
Start-Sleep 3
& schtasks /run /tn ComfyUI-OneShot 2>&1 | Out-Null
for ($i=1; $i -le 90; $i++) {
    Start-Sleep 2
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8188/system_stats" -TimeoutSec 2 -UseBasicParsing -EA Stop
        if ($r.StatusCode -eq 200) { Write-Host "ready t+$($i*2)s"; break }
    } catch {}
}
