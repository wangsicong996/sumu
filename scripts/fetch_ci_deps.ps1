# SPDX-FileCopyrightText: sumu Authors
# SPDX-License-Identifier: AGPL-3.0
#
# Fetch CI/native build inputs that are gitignored:
#   - BtbN FFmpeg win64-gpl-shared (master) -> spikes/.../ffmpeg  (native decode)
#   - BtbN FFmpeg n8.1 win64-gpl static     -> spikes/.../ffmpeg-cli (NVENC export, SDK 13.0)
#   - lada HuggingFace weights              -> model_weights/
# Used by .github/workflows/release.yml; also runnable locally.
param(
    [switch]$SkipFfmpeg,
    [switch]$SkipWeights,
    [string]$FfmpegUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip",
    [string]$WeightsBase = "https://huggingface.co/ladaapp/lada/resolve/main"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Get-RemoteFile([string]$Url, [string]$Dest) {
    $dir = Split-Path -Parent $Dest
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    Write-Host "GET $Url -> $Dest"
    $headers = @{ "User-Agent" = "sumu-ci" }
    if ($env:HF_TOKEN) { $headers["Authorization"] = "Bearer $($env:HF_TOKEN)" }
    # curl.exe follows redirects and resumes better than Invoke-WebRequest on large files.
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        $args = @("-L", "--fail", "--retry", "5", "--retry-delay", "3", "-o", $Dest, $Url)
        if ($env:HF_TOKEN) { $args = @("-H", "Authorization: Bearer $($env:HF_TOKEN)") + $args }
        & curl.exe @args
        if ($LASTEXITCODE -ne 0) { throw "curl failed for $Url (exit $LASTEXITCODE)" }
    } else {
        Invoke-WebRequest -Uri $Url -OutFile $Dest -Headers $headers
    }
    if (-not (Test-Path $Dest) -or ((Get-Item $Dest).Length -lt 1024)) {
        throw "download looks empty: $Dest"
    }
}

if (-not $SkipFfmpeg) {
    $ffmpegRoot = Join-Path $RepoRoot "spikes\spike0_d3d11_present\third_party\ffmpeg"
    $hasLib = Test-Path (Join-Path $ffmpegRoot "lib\avcodec.lib")
    $hasDll = Test-Path (Join-Path $ffmpegRoot "bin")
    if ($hasLib -and $hasDll) {
        Write-Host "FFmpeg already present at $ffmpegRoot -- skip download"
    } else {
        $zip = Join-Path $env:TEMP "ffmpeg-btbn-win64-gpl-shared.zip"
        Get-RemoteFile $FfmpegUrl $zip
        $extract = Join-Path $env:TEMP "ffmpeg-btbn-extract"
        if (Test-Path $extract) { Remove-Item -Recurse -Force $extract }
        Expand-Archive -Path $zip -DestinationPath $extract -Force
        $inner = Get-ChildItem $extract -Directory | Select-Object -First 1
        if (-not $inner) { throw "FFmpeg zip had no top-level directory" }
        $parent = Split-Path -Parent $ffmpegRoot
        if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        if (Test-Path $ffmpegRoot) { Remove-Item -Recurse -Force $ffmpegRoot }
        Move-Item -Path $inner.FullName -Destination $ffmpegRoot
        Write-Host "FFmpeg staged at $ffmpegRoot"
    }
}

# Static n8.1 CLI for NVENC export (SDK 13.0). Separate from the shared master tree.
if (-not $SkipFfmpeg) {
    & (Join-Path $PSScriptRoot "stage_encoder_ffmpeg.ps1")
}

if (-not $SkipWeights) {
    $weightsDir = Join-Path $RepoRoot "model_weights"
    if (-not (Test-Path $weightsDir)) { New-Item -ItemType Directory -Path $weightsDir -Force | Out-Null }
    $files = @(
        "lada_mosaic_restoration_model_generic_v1.2.pth",
        "lada_mosaic_detection_model_v4_fast.pt"
    )
    foreach ($f in $files) {
        $dest = Join-Path $weightsDir $f
        if ((Test-Path $dest) -and ((Get-Item $dest).Length -gt 1024)) {
            Write-Host "weight already present: $f"
            continue
        }
        Get-RemoteFile "$WeightsBase/$f" $dest
    }
    Write-Host "weights staged at $weightsDir"
}

Write-Host "fetch_ci_deps OK"
