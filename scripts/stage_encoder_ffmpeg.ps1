# SPDX-FileCopyrightText: sumu Authors
# SPDX-License-Identifier: AGPL-3.0
#
# Stage a *static* BtbN FFmpeg CLI for NVENC export/webstream.
#
# Native decode keeps the spike0 win64-gpl-*shared* tree (av*.dll next to the pyd).
# BtbN *master* shared is built against NVENC SDK 13.1 and refuses to encode on
# drivers that only expose API 13.0 ("Required: 13.1 Found: 13.0").
# n8.1 (FFVER 801) is pinned to nv-codec-headers sdk/13.0, which matches those drivers.
# n9.0 / master use SDK 13.1 and fail on 13.0 drivers.
# The gpl (non-shared) zip is a self-contained ffmpeg.exe so it does not load
# the newer avcodec DLL from _internal.
param(
    [string]$DestDir = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$CliRoot = Join-Path $RepoRoot "spikes\spike0_d3d11_present\third_party\ffmpeg-cli"
$CliBin = Join-Path $CliRoot "bin"
$Url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-win64-gpl-8.1.zip"

function Get-RemoteFile([string]$From, [string]$To) {
    $dir = Split-Path -Parent $To
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    Write-Host "GET $From -> $To"
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        & curl.exe -L --fail --retry 5 --retry-delay 3 -o $To $From
        if ($LASTEXITCODE -ne 0) { throw "curl failed for $From (exit $LASTEXITCODE)" }
    } else {
        Invoke-WebRequest -Uri $From -OutFile $To -Headers @{ "User-Agent" = "sumu-ci" }
    }
    if (-not (Test-Path $To) -or ((Get-Item $To).Length -lt 1024)) {
        throw "download looks empty: $To"
    }
}

$needFetch = -not (Test-Path (Join-Path $CliBin "ffmpeg.exe"))
if ($needFetch) {
    Write-Host "fetching BtbN n8.1 static ffmpeg (NVENC SDK 13.0) -> $CliRoot"
    $zip = Join-Path $env:TEMP "ffmpeg-btbn-n8.1-win64-gpl.zip"
    Get-RemoteFile $Url $zip
    $extract = Join-Path $env:TEMP "ffmpeg-btbn-n8.1-extract"
    if (Test-Path $extract) { Remove-Item -Recurse -Force $extract }
    Expand-Archive -Path $zip -DestinationPath $extract -Force
    $inner = Get-ChildItem $extract -Directory | Select-Object -First 1
    if (-not $inner) { throw "FFmpeg n8.1 zip had no top-level directory" }
    $parent = Split-Path -Parent $CliRoot
    if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    if (Test-Path $CliRoot) { Remove-Item -Recurse -Force $CliRoot }
    Move-Item -Path $inner.FullName -Destination $CliRoot
    if (-not (Test-Path (Join-Path $CliBin "ffmpeg.exe"))) {
        throw "expected $CliBin\ffmpeg.exe after extract"
    }
    Write-Host "encoder ffmpeg staged at $CliBin"
} else {
    Write-Host "encoder ffmpeg already present at $CliBin -- skip download"
}

if ($DestDir) {
    if (-not (Test-Path $DestDir)) { New-Item -ItemType Directory -Path $DestDir -Force | Out-Null }
    Copy-Item -Path (Join-Path $CliBin "ffmpeg.exe") -Destination (Join-Path $DestDir "ffmpeg.exe") -Force
    $ffprobe = Join-Path $CliBin "ffprobe.exe"
    if (Test-Path $ffprobe) {
        Copy-Item -Path $ffprobe -Destination (Join-Path $DestDir "ffprobe.exe") -Force
    }
    Write-Host "encoder ffmpeg copied to $DestDir"
}
