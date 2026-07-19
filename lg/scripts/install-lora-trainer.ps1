<#
install-lora-trainer.ps1
LoRA training pack for ComfyUI: LJRE/LoRA-Training-in-Comfy custom node
+ Python deps (peft, bitsandbytes, accelerate, transformers, diffusers).

Note: LoRA training on AMD ROCm Windows is partially supported. bitsandbytes
historically has ROCm wheels only on Linux; on Windows it falls back to
non-quantized optimizers (Adam fp32) which is slower but works. Expect
20-40 min per 8-12 image LoRA train at 1024 res on this hardware.
#>

param(
  [string] $ComfyRoot = "C:\Users\gad\ComfyUI\ComfyUI_windows_portable\ComfyUI"
)

$ProgressPreference = 'SilentlyContinue'
$py = "C:\Users\gad\ComfyUI\ComfyUI_windows_portable\python_embeded\python.exe"

Write-Host "=== Install LoRA trainer custom node ===" -ForegroundColor Yellow
$dest = "$ComfyRoot\custom_nodes\LoRA-Training-in-Comfy"
if (Test-Path -LiteralPath $dest) {
    Write-Host "  skip (exists)"
} else {
    $zip = "$env:TEMP\lora-trainer.zip"
    Invoke-WebRequest -Uri "https://github.com/LarryJane491/Lora-Training-in-Comfy/archive/refs/heads/main.zip" -OutFile $zip -UseBasicParsing
    Expand-Archive -Path $zip -DestinationPath "$ComfyRoot\custom_nodes\" -Force
    $ex = Get-ChildItem "$ComfyRoot\custom_nodes" -Directory |
        Where-Object { $_.Name -like "Lora-Training-in-Comfy*" -and $_.Name -ne "LoRA-Training-in-Comfy" } |
        Select-Object -First 1
    if ($ex) { Move-Item $ex.FullName $dest }
    Remove-Item $zip -Force -EA SilentlyContinue
    Write-Host "  installed -> $dest"
}

Write-Host "`n=== Install training Python deps ===" -ForegroundColor Yellow
$deps = @(
    "peft",            # LoRA adapter library
    "accelerate",      # distributed / mixed-precision
    "transformers",    # already there, but ensure
    "diffusers",       # SDXL training scripts depend on this
    "safetensors",     # already there
    "lion-pytorch",    # better optimizer than Adam for LoRA
    "wandb"            # optional logging
)
foreach ($d in $deps) {
    & $py -m pip install --no-deps --upgrade $d 2>&1 |
        Select-String -Pattern "Successfully|already satisfied" |
        Select-Object -First 1 | ForEach-Object { Write-Host ("  " + $_.Line) }
}

Write-Host "`n=== verify ===" -ForegroundColor Green
& $py -c "
mods = ['peft', 'accelerate', 'transformers', 'diffusers', 'lion_pytorch']
for m in mods:
    try:
        mod = __import__(m)
        print(f'  {m}: OK ({getattr(mod, \"__version__\", \"?\")})')
    except Exception as e:
        print(f'  {m}: FAIL {type(e).__name__}')
"
Write-Host "`nDone. Restart ComfyUI to register the LoRA trainer node."
