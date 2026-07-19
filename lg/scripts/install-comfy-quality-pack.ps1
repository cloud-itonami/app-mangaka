<#
install-comfy-quality-pack.ps1
==============================
Install higher-quality models + custom nodes onto a Windows ComfyUI host.

Runs on the ComfyUI host itself (192.168.1.70). Drops model files into
the right `models/` subdirectories and git-clones the custom-node packs
needed for IPAdapter / Hunyuan3D quality jump described in the studio
2026-05-20 review.

Usage (PowerShell on the ComfyUI Windows host):

    # all tiers
    .\install-comfy-quality-pack.ps1

    # specific tiers (1=ControlNet, 2=IPAdapter, 3=Better SDXL ckpts, 4=Hy3D nodes)
    .\install-comfy-quality-pack.ps1 -Tier 1
    .\install-comfy-quality-pack.ps1 -Tier 1,2,3

    # custom ComfyUI install root
    .\install-comfy-quality-pack.ps1 -ComfyRoot "C:\Users\gad\ComfyUI\ComfyUI_windows_portable\ComfyUI"

After install, restart ComfyUI (close the launcher window, re-open it).
Files are skipped if already present (idempotent).
#>

param(
    [int[]] $Tier = @(1, 2, 3, 4),
    [string] $ComfyRoot = "C:\Users\gad\ComfyUI\ComfyUI_windows_portable\ComfyUI",
    [string] $HfMirror = "https://huggingface.co"
)

$ErrorActionPreference = "Continue"

function Ensure-Dir([string] $p) {
    if (-not (Test-Path -LiteralPath $p)) {
        New-Item -ItemType Directory -Force -Path $p | Out-Null
    }
}

function DL([string] $url, [string] $dest) {
    if (Test-Path -LiteralPath $dest) {
        $size = (Get-Item -LiteralPath $dest).Length
        Write-Host "  skip (exists, $($size) B)  $dest" -ForegroundColor DarkGray
        return
    }
    Ensure-Dir (Split-Path -LiteralPath $dest)
    Write-Host "  GET $url" -ForegroundColor Cyan
    Write-Host "    -> $dest"
    try {
        # ProgressPreference=SilentlyContinue makes IWR fast (the progress bar is the bottleneck on PS5).
        $progressBackup = $ProgressPreference
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
        $ProgressPreference = $progressBackup
        $size = (Get-Item -LiteralPath $dest).Length
        Write-Host "    OK ($size B)" -ForegroundColor Green
    } catch {
        Write-Host "    FAIL: $_" -ForegroundColor Red
    }
}

function GitClone([string] $repo, [string] $dest) {
    if (Test-Path -LiteralPath $dest) {
        Write-Host "  skip (exists)  $dest" -ForegroundColor DarkGray
        return
    }
    Write-Host "  git clone $repo" -ForegroundColor Cyan
    Write-Host "    -> $dest"
    & git clone --depth 1 $repo $dest
    if ($LASTEXITCODE -eq 0) {
        Write-Host "    OK" -ForegroundColor Green
    } else {
        Write-Host "    FAIL ($LASTEXITCODE)" -ForegroundColor Red
    }
}

Write-Host "ComfyUI root: $ComfyRoot" -ForegroundColor Yellow
Write-Host "Tiers requested: $($Tier -join ', ')" -ForegroundColor Yellow

if (-not (Test-Path -LiteralPath $ComfyRoot)) {
    Write-Host "ComfyRoot not found. Pass -ComfyRoot <path-to-ComfyUI>" -ForegroundColor Red
    exit 1
}

# -- TIER 1 -- ControlNet Union (one file, all conditioning types) ---------
if ($Tier -contains 1) {
    Write-Host "`n=== Tier 1: ControlNet Union SDXL ===" -ForegroundColor Yellow
    DL "$HfMirror/xinsir/controlnet-union-sdxl-1.0/resolve/main/diffusion_pytorch_model_promax.safetensors" `
       "$ComfyRoot\models\controlnet\controlnet-union-sdxl-promax.safetensors"
}

# -- TIER 2 -- IPAdapter Plus + FaceID v2 (character identity) -------------
if ($Tier -contains 2) {
    Write-Host "`n=== Tier 2: IPAdapter Plus + FaceID v2 ===" -ForegroundColor Yellow

    # Custom node pack (provides IPAdapterUnifiedLoader, IPAdapterFaceID, etc.)
    GitClone "https://github.com/cubiq/ComfyUI_IPAdapter_plus" `
             "$ComfyRoot\custom_nodes\ComfyUI_IPAdapter_plus"

    # CLIP-ViT-bigG image encoder (used by SDXL IPAdapter)
    DL "$HfMirror/laion/CLIP-ViT-bigG-14-laion2B-39B-b160k/resolve/main/open_clip_pytorch_model.bin" `
       "$ComfyRoot\models\clip_vision\CLIP-ViT-bigG-14-laion2B-39B-b160k.bin"

    # IPAdapter SDXL Plus base
    DL "$HfMirror/h94/IP-Adapter/resolve/main/sdxl_models/ip-adapter-plus_sdxl_vit-h.safetensors" `
       "$ComfyRoot\models\ipadapter\ip-adapter-plus_sdxl_vit-h.safetensors"

    # IPAdapter FaceID Plus v2 (the character-identity workhorse)
    DL "$HfMirror/h94/IP-Adapter-FaceID/resolve/main/ip-adapter-faceid-plusv2_sdxl.bin" `
       "$ComfyRoot\models\ipadapter\ip-adapter-faceid-plusv2_sdxl.bin"
    DL "$HfMirror/h94/IP-Adapter-FaceID/resolve/main/ip-adapter-faceid-plusv2_sdxl_lora.safetensors" `
       "$ComfyRoot\models\loras\ip-adapter-faceid-plusv2_sdxl_lora.safetensors"

    # InsightFace (antelopev2 model -- required by FaceID for face landmark extraction)
    $insightDir = "$ComfyRoot\models\insightface\models\antelopev2"
    Ensure-Dir $insightDir
    foreach ($f in @(
        "1k3d68.onnx", "2d106det.onnx", "genderage.onnx",
        "glintr100.onnx", "scrfd_10g_bnkps.onnx"
    )) {
        DL "$HfMirror/MonsterMMORPG/tools/resolve/main/antelopev2/$f" "$insightDir\$f"
    }

    # insightface python package (pip install). The IPAdapter custom node
    # imports it directly; without it FaceID nodes silently degrade.
    $pythonExe = Join-Path -Path (Split-Path -Parent $ComfyRoot) -ChildPath "python_embeded\python.exe"
    if (Test-Path -LiteralPath $pythonExe) {
        Write-Host "  pip install insightface onnxruntime"
        & $pythonExe -m pip install --upgrade insightface onnxruntime 2>&1 | Tee-Object -FilePath "$env:TEMP\pip-insightface.log"
    } else {
        Write-Host "  WARN: python_embeded\python.exe not at $pythonExe -- pip install insightface yourself" -ForegroundColor Yellow
    }
}

# -- TIER 3 -- Better SDXL base checkpoints (manga / illustration) ---------
if ($Tier -contains 3) {
    Write-Host "`n=== Tier 3: Better SDXL base checkpoints ===" -ForegroundColor Yellow
    # Illustrious XL v1.0 (or v1.1 if released) -- current best for manga line work
    DL "$HfMirror/OnomaAIResearch/Illustrious-xl-early-release-v0/resolve/main/Illustrious-XL-v0.1.safetensors" `
       "$ComfyRoot\models\checkpoints\illustriousXL_v01.safetensors"
    # NoobAI XL Eps-Pred 1.1 (Illustrious-derived, even sharper shounen ink)
    DL "$HfMirror/Laxhar/noobai-XL-1.1/resolve/main/NoobAI-XL-v1.1.safetensors" `
       "$ComfyRoot\models\checkpoints\noobaiXL_v11.safetensors"
}

# -- TIER 4 -- Hunyuan3D-2 model files (auto-DL via Hy3D nodes) ------------
if ($Tier -contains 4) {
    Write-Host "`n=== Tier 4: Hunyuan3D-2 (notes only -- auto-DL on first use) ===" -ForegroundColor Yellow
    Write-Host "  Hy3D nodes (48+) are already installed in this ComfyUI."
    Write-Host "  First run of DownloadAndLoadHy3DDelightModel / DownloadAndLoadHy3DPaintModel"
    Write-Host "  will pull ~6-8 GB from HuggingFace (tencent/Hunyuan3D-2)."
    Write-Host "  No action needed here -- just queue a Hy3D workflow once."
    # Pre-create the dest dir so the auto-DL doesn't fail on missing folder
    Ensure-Dir "$ComfyRoot\models\diffusion_models\hunyuan3d"
    Ensure-Dir "$ComfyRoot\models\diffusion_models\hunyuan3d-paint"
    Ensure-Dir "$ComfyRoot\models\diffusion_models\hunyuan3d-delight"
}

Write-Host "`nDone. RESTART ComfyUI (close + re-open the launcher) so new custom nodes + models register." -ForegroundColor Green
Write-Host "Then check /object_info for: IPAdapter*, ControlNetUnion presence, ControlNetLoader enum populated." -ForegroundColor Green
