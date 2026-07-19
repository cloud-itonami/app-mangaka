$dest = "C:\Users\gad\ComfyUI\ComfyUI_windows_portable\ComfyUI\custom_nodes\ManagaTextOverlay"

# Read current nodes.py + patch font + replace EC mockup with portrait phone
$f = "$dest\nodes.py"
$c = Get-Content $f -Raw

# 1. Default font for bubbles -> YuGothB.ttc (BOLD) for manga punch
$c = $c -replace '"C:/Windows/Fonts/YuGothM\.ttc"', '"C:/Windows/Fonts/YuGothB.ttc"'

# 2. Replace MangakaECMockup body with portrait phone version
$old = 'class MangakaECMockup:'
$new_class = @'
class MangakaECMockup:
    """PORTRAIT smartphone screen mockup — manga-style sneaker shop UI
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
            "product_title": ("STRING", {"default": "限定 Pure White Edition"}),
            "price_now": ("STRING", {"default": "12,800"}),
            "price_was": ("STRING", {"default": "25,000"}),
            "countdown": ("STRING", {"default": "残り 04:32:11"}),
            "cta_label": ("STRING", {"default": "今すぐ購入"}),
            "review_text": ("STRING", {"default": "(2,847 件)"}),
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

        # Phone outer frame — large rounded rect
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

        # Hero — sneaker shoe centered
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
        was_str = f"¥{price_was}"
        d.text((ph[0]+24, wp_y), was_str, font=small, fill=(140,140,140))
        wbb = d.textbbox((ph[0]+24, wp_y), was_str, font=small)
        d.line([(wbb[0], (wbb[1]+wbb[3])//2),
                (wbb[2], (wbb[1]+wbb[3])//2)],
               fill=(140,140,140), width=2)

        # Now-price (LEFT BIG)
        np_y = wp_y + int(height * 0.03)
        now_str = f"¥{price_now}"
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

'@

# Find and replace the old MangakaECMockup class up to "NODE_CLASS_MAPPINGS[\"MangakaECMockup\"]"
$pattern = '(?s)class MangakaECMockup:.*?(?=NODE_CLASS_MAPPINGS\["MangakaECMockup"\])'
$c = $c -replace $pattern, $new_class

Set-Content -Path $f -Value $c -NoNewline
Write-Host "patched: font + EC mockup -> portrait phone"

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
