"""mangaka v4: short tail, no margin (overflow OK), centered text,
   no speaker label, wider vertical columns."""
import math, os
import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont

_FONT_CANDIDATES = [
    "C:/Windows/Fonts/YuGothB.ttc", "C:/Windows/Fonts/yumin.ttf",
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

_VERT_ROTATE = set("aŸ¬‹¬?ƒ?"ƒ?ÝaŸ¯a??a?,a?Oa??a?Za??‹¬^‹¬%()‹«>‹«?[]a??a?`")

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
    (small bit out of bubble ƒ?" not extending to face), centered text,
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

        # Short STUB tail ƒ?" direction-controlled, doesn't reach the face
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

        # Text ƒ?" CENTERED in bubble, NO speaker label
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
            "text": ("STRING", {"multiline": True, "default": "aŸ%aŸ¬aŸ3"}),
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

class MangakaECMockup:
    """PORTRAIT smartphone screen mockup ƒ?" manga-style sneaker shop UI
    in a vertical iPhone-ish frame. Tightly readable elements: status
    bar (time/signal/battery), back arrow + site name, URL bar, hero
    sneaker silhouette w/ screentone, red countdown banner, product
    title, strikethrough was-price, big now-price, 5-star widget,
    review count, CTA buy button, home indicator at bottom."""
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "width": ("INT", {"default": 580, "min": 256, "max": 8192}),
            "height": ("INT", {"default": 980, "min": 128, "max": 8192}),
            "site_name": ("STRING", {"default": "TokyoSneaker"}),
            "url_text": ("STRING", {"default": "tokyosneaker-premium.shop"}),
            "product_title": ("STRING", {"default": "‚T?†rs Pure White Edition"}),
            "price_now": ("STRING", {"default": "12,800"}),
            "price_was": ("STRING", {"default": "25,000"}),
            "countdown": ("STRING", {"default": "‘r<a,S 04:32:11"}),
            "cta_label": ("STRING", {"default": "„¯Sa?Ta??Š3¬†."}),
            "review_text": ("STRING", {"default": "(2,847 „¯)"}),
            "stars_filled": ("INT", {"default": 5, "min": 0, "max": 5}),
            "screentone": ("BOOLEAN", {"default": True}),
            "phone_clock": ("STRING", {"default": "23:47"}),
        }}
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "run"
    CATEGORY = "mangaka"

    def run(self, width, height, site_name, url_text, product_title,
            price_now, price_was, countdown, cta_label, review_text,
            stars_filled, screentone, phone_clock):
        import math as _m
        from PIL import Image, ImageDraw, ImageFont
        im = Image.new("RGB", (width, height), "white")
        d = ImageDraw.Draw(im)

        # Fonts (bold for headers, regular for body)
        big   = _find_font(int(height * 0.075), sfx=True)
        mid   = _find_font(int(height * 0.040), sfx=True)
        small = _find_font(int(height * 0.030))
        tiny  = _find_font(int(height * 0.024))

        # Phone outer frame ƒ?" large rounded rect
        margin = 18
        rad = 40
        ph = [margin, margin, width - margin, height - margin]
        # Black bezel
        d.rounded_rectangle([ph[0]-6, ph[1]-6, ph[2]+6, ph[3]+6],
                            radius=rad+6, fill=(15,15,15))
        # Inner white screen
        d.rounded_rectangle(ph, radius=rad, fill="white",
                            outline="black", width=2)

        # Status bar (clock + signal + battery)
        sb_h = int(height * 0.04)
        d.text((ph[0]+24, ph[1]+8), phone_clock, font=tiny, fill="black")
        d.text((ph[2]-110, ph[1]+8), ".all  Wi-Fi  100%", font=tiny, fill="black")
        # Notch (camera island)
        notch_w = int(width * 0.30); notch_h = 22
        nx = (width - notch_w) // 2
        d.rounded_rectangle([nx, ph[1]+4, nx+notch_w, ph[1]+4+notch_h],
                            radius=12, fill=(15,15,15))

        # Top bar (browser chrome)
        top_y = ph[1] + sb_h + 12
        top_h = int(height * 0.045)
        d.rectangle([ph[0]+8, top_y, ph[2]-8, top_y+top_h],
                    fill=(240,240,240), outline="black", width=1)
        d.text((ph[0]+18, top_y+6), "<", font=small, fill="black")
        d.text((ph[0]+46, top_y+6), site_name, font=small, fill="black")
        d.text((ph[2]-32, top_y+6), "...", font=small, fill="black")

        # URL bar
        ub_y = top_y + top_h + 6
        ub_h = int(height * 0.035)
        d.rounded_rectangle([ph[0]+14, ub_y, ph[2]-14, ub_y+ub_h],
                            radius=6, fill=(230,230,230), outline="black", width=1)
        d.text((ph[0]+24, ub_y+5), "[lock]", font=tiny, fill="black")
        d.text((ph[0]+72, ub_y+5), url_text, font=tiny, fill="black")

        # Hero ƒ?" sneaker shoe centered
        hero_y0 = ub_y + ub_h + 14
        hero_h = int(height * 0.30)
        hero_y1 = hero_y0 + hero_h
        hero_x0 = ph[0] + 14
        hero_x1 = ph[2] - 14
        # Screentone background
        if screentone:
            for px in range(hero_x0+4, hero_x1-4, 8):
                for py in range(hero_y0+4, hero_y1-4, 8):
                    d.ellipse([px, py, px+2, py+2], fill=(225,225,225))
        # Sneaker silhouette
        ssw = int((hero_x1 - hero_x0) * 0.75)
        ssh = int(hero_h * 0.55)
        ssx = hero_x0 + ((hero_x1 - hero_x0) - ssw) // 2
        ssy = hero_y0 + (hero_h - ssh) // 2
        # Outline path
        d.polygon([
            (ssx,            ssy + ssh - 6),
            (ssx + ssw//6,   ssy + ssh*4//6),
            (ssx + ssw//3,   ssy + ssh//4),
            (ssx + ssw//2,   ssy + ssh//5),
            (ssx + ssw*2//3, ssy + ssh//4),
            (ssx + ssw*5//6, ssy + ssh*2//5),
            (ssx + ssw,      ssy + ssh*3//5),
            (ssx + ssw,      ssy + ssh - 6),
        ], fill="white", outline="black", width=3)
        # Sole stripe
        d.rectangle([ssx, ssy + ssh - 6, ssx + ssw, ssy + ssh + 4],
                    fill="black")
        # Laces hatching
        for li in range(3):
            lx = ssx + ssw*2//5 + li * 10
            ly = ssy + ssh*2//5
            d.line([(lx, ly), (lx + 10, ly - 10)], fill="black", width=2)

        # Countdown red banner
        cd_y = hero_y1 + 8
        cd_h = int(height * 0.045)
        d.rectangle([ph[0]+14, cd_y, ph[2]-14, cd_y+cd_h], fill=(220, 30, 30))
        # Center the countdown text
        cbbox = d.textbbox((0,0), countdown, font=mid)
        ctw = cbbox[2]-cbbox[0]
        d.text((ph[0]+14 + ((ph[2]-ph[0]-28) - ctw)//2, cd_y + 4),
               countdown, font=mid, fill="white")

        # Product title (center)
        pt_y = cd_y + cd_h + 16
        ptbb = d.textbbox((0,0), product_title, font=mid)
        ptw = ptbb[2]-ptbb[0]
        d.text((ph[0] + (width - ptw)//2 - margin, pt_y),
               product_title, font=mid, fill="black")

        # Strikethrough was-price (left)
        wp_y = pt_y + int(height * 0.05)
        was_str = f"A{price_was}"
        d.text((ph[0]+24, wp_y), was_str, font=small, fill=(140,140,140))
        wbb = d.textbbox((ph[0]+24, wp_y), was_str, font=small)
        d.line([(wbb[0], (wbb[1]+wbb[3])//2),
                (wbb[2], (wbb[1]+wbb[3])//2)],
               fill=(140,140,140), width=2)

        # Now-price (LEFT BIG)
        np_y = wp_y + int(height * 0.03)
        now_str = f"A{price_now}"
        d.text((ph[0]+24, np_y), now_str, font=big, fill="black")

        # Stars + reviews (right)
        star_y = wp_y + 4
        star_size = int(height * 0.030)
        star_block_w = 5 * (star_size + 3) - 3
        sx_start = ph[2] - 24 - star_block_w
        for si in range(5):
            sx = sx_start + si * (star_size + 3)
            fill = (240, 180, 30) if si < stars_filled else "white"
            cx = sx + star_size//2; cy = star_y + star_size//2
            pts = []
            for j in range(10):
                ang = -_m.pi/2 + j * _m.pi / 5
                rm = star_size//2 if j % 2 == 0 else star_size//4
                pts.append((cx + _m.cos(ang)*rm, cy + _m.sin(ang)*rm))
            d.polygon(pts, fill=fill, outline="black")
        d.text((sx_start, star_y + star_size + 4), review_text,
               font=tiny, fill="black")

        # CTA buy button (red)
        cta_h = int(height * 0.06)
        cta_y = ph[3] - cta_h - 40
        d.rounded_rectangle([ph[0]+18, cta_y, ph[2]-18, cta_y + cta_h],
                            radius=10, fill=(220, 30, 30),
                            outline="black", width=2)
        cbb = d.textbbox((0,0), cta_label, font=mid)
        ctw = cbb[2]-cbb[0]; cth = cbb[3]-cbb[1]
        d.text((ph[0] + (width - ctw)//2 - margin,
                cta_y + (cta_h - cth)//2 - 2),
               cta_label, font=mid, fill="white")

        # Home indicator (bottom)
        hi_y = ph[3] - 18
        d.rounded_rectangle([width//2 - 60, hi_y, width//2 + 60, hi_y + 6],
                            radius=3, fill="black")

        return (_pil2t(im),)
NODE_CLASS_MAPPINGS["MangakaECMockup"] = MangakaECMockup
NODE_DISPLAY_NAME_MAPPINGS["MangakaECMockup"] = "Mangaka EC Mockup (PIL)"


class MangakaPhoneScreenPaste:
    """Paste the EC mockup onto an underlying 'hands holding smartphone'
    image at a rectangular phone-screen region. Supports a small rotation
    so the screen looks like it's being held at a tilt. The screen image
    is resized to fit the (w, h) rectangle then rotated about its center."""
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "base": ("IMAGE",),
            "screen": ("IMAGE",),
            "x": ("INT", {"default": 100, "min": -10000, "max": 10000}),
            "y": ("INT", {"default": 80, "min": -10000, "max": 10000}),
            "w": ("INT", {"default": 220, "min": 32, "max": 10000}),
            "h": ("INT", {"default": 360, "min": 32, "max": 10000}),
            "rotation_deg": ("FLOAT", {"default": 0.0, "min": -45.0, "max": 45.0, "step": 1.0}),
            "shadow": ("BOOLEAN", {"default": True}),
        }}
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "run"
    CATEGORY = "mangaka"

    def run(self, base, screen, x, y, w, h, rotation_deg, shadow):
        from PIL import Image, ImageDraw, ImageFilter
        base_im = _t2pil(base).convert("RGBA")
        scr_im = _t2pil(screen).convert("RGBA").resize((w, h), Image.LANCZOS)

        if rotation_deg != 0:
            scr_im = scr_im.rotate(rotation_deg, resample=Image.BICUBIC,
                                   expand=True, fillcolor=(0,0,0,0))

        if shadow:
            # Soft drop shadow underneath the screen
            sh = Image.new("RGBA", scr_im.size, (0,0,0,0))
            ImageDraw.Draw(sh).rectangle([0, 0, sh.size[0], sh.size[1]],
                                          fill=(0,0,0,140))
            sh = sh.filter(ImageFilter.GaussianBlur(radius=8))
            base_im.paste(sh, (x+6, y+8), sh)

        base_im.paste(scr_im, (x, y), scr_im)
        return (_pil2t(base_im.convert("RGB")),)


NODE_CLASS_MAPPINGS["MangakaPhoneScreenPaste"] = MangakaPhoneScreenPaste
NODE_DISPLAY_NAME_MAPPINGS["MangakaPhoneScreenPaste"] = "Mangaka Phone Screen Paste"

