$dest = "C:\Users\gad\ComfyUI\ComfyUI_windows_portable\ComfyUI\custom_nodes\ManagaTextOverlay"
$f = "$dest\nodes.py"
$c = Get-Content $f -Raw

# Add MangakaPhoneScreenPaste — paste a portrait phone EC mockup onto a
# generated "hands holding phone" panel at a specified screen rectangle.
$add = @'


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
'@

# Append if not present
if ($c -notmatch "MangakaPhoneScreenPaste") {
    Add-Content -Path $f -Value $add
    Write-Host "MangakaPhoneScreenPaste added"
} else {
    Write-Host "already present"
}

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
