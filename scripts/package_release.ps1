# SPDX-FileCopyrightText: sumu Authors
# SPDX-License-Identifier: AGPL-3.0
#
# Split dist\sumu into 7-Zip volumes under 2 GiB (GitHub release asset limit)
# named part1 / part2 / ... so a ~7GB onedir can be uploaded as a Release.
#
# Usage (after scripts/build_dist.ps1):
#   powershell -ExecutionPolicy Bypass -File scripts/package_release.ps1
#   powershell -File scripts/package_release.ps1 -Version 0.3.4 -VolumeMB 1900
#
# Consumers: download EVERY volume into one folder, then open the .001 with 7-Zip.
param(
    [string]$DistDir = "",
    [string]$OutDir = "",
    [string]$Version = "",
    [int]$VolumeMB = 1900
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

if (-not $DistDir) { $DistDir = Join-Path $RepoRoot "dist\sumu" }
if (-not $OutDir)  { $OutDir  = Join-Path $RepoRoot "dist\release" }

function Fail($msg) {
    Write-Host "PACKAGE FAILED: $msg" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path (Join-Path $DistDir "sumu.exe"))) {
    Fail "dist folder missing sumu.exe: $DistDir -- run scripts/build_dist.ps1 first"
}

if (-not $Version) {
    if ($env:GITHUB_REF_NAME -match '^v(.+)$') {
        $Version = $Matches[1]
    } else {
        $toml = Get-Content (Join-Path $RepoRoot "pyproject.toml") -Raw
        if ($toml -match '(?m)^version\s*=\s*"([^"]+)"') {
            $Version = $Matches[1]
        } else {
            $Version = "0.0.0"
        }
    }
}

$seven = $null
foreach ($c in @(
    "$env:ProgramFiles\7-Zip\7z.exe",
    "$env:ProgramFiles(x86)\7-Zip\7z.exe"
)) {
    if (Test-Path $c) { $seven = $c; break }
}
if (-not $seven) {
    $cmd = Get-Command 7z -ErrorAction SilentlyContinue
    if ($cmd) { $seven = $cmd.Source }
}
if (-not $seven) { Fail "7z.exe not found. Install 7-Zip (https://www.7-zip.org/)." }

if (Test-Path $OutDir) { Remove-Item -Recurse -Force $OutDir }
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null

# Same basename for every volume: 7-Zip looks for name.7z.002 next to name.7z.001.
# Asset display names use -partN so GitHub Releases reads as part1 / part2.
$archiveBase = Join-Path $OutDir "sumu-$Version-windows-x64.7z"
Write-Host "packing $DistDir -> $archiveBase (volumes ${VolumeMB}m)" -ForegroundColor Cyan

# -mx=1: CUDA/TRT DLLs barely compress; store-ish is much faster in CI.
# -mmt=on: use all cores. -v1900m stays under GitHub's 2 GiB/file cap.
$packArgs = @(
    "a", "-t7z", "-mx=1", "-mmt=on", "-v${VolumeMB}m",
    "-bsp1",
    $archiveBase,
    (Join-Path $DistDir "*")
)
& $seven @packArgs
if ($LASTEXITCODE -ne 0) { Fail "7z exited $LASTEXITCODE" }

# 7z names a single-volume archive `name.7z` (no .001). Always normalize to
# `name.7z.001` so the release always has a stable first-part name. These .001 /
# .002 files ARE part1 / part2 -- 7-Zip requires the shared basename.
$volumes = @(Get-ChildItem $OutDir -File | Where-Object {
    $_.Name -like "sumu-$Version-windows-x64.7z*"
} | Sort-Object Name)
if ($volumes.Count -eq 1 -and $volumes[0].Name -eq "sumu-$Version-windows-x64.7z") {
    $dest001 = Join-Path $OutDir "sumu-$Version-windows-x64.7z.001"
    Rename-Item -Path $volumes[0].FullName -NewName "sumu-$Version-windows-x64.7z.001"
    $volumes = @(Get-Item $dest001)
}

$i = 1
$sums = Join-Path $OutDir "SHA256SUMS.txt"
if (Test-Path $sums) { Remove-Item $sums }
foreach ($v in $volumes) {
    $hash = (Get-FileHash -Algorithm SHA256 $v.FullName).Hash.ToLower()
    Add-Content -Path $sums -Value "$hash  $($v.Name)"
    Write-Host ("part{0}: {1} ({2:N1} MB)" -f $i, $v.Name, ($v.Length / 1MB))
    $i++
}

$readme = Join-Path $OutDir "README-UNPACK.txt"
@"
sumu $Version  Windows x64  分卷说明 / How to unpack
================================================

请下载全部 part1 / part2 / ... 分卷到同一目录，然后用 7-Zip 打开
  sumu-$Version-windows-x64.7z.001
即可自动读取后续分卷并解压出 sumu\ 目录。双击 sumu.exe 运行。

Download EVERY volume (part1, part2, ...) into the same folder, then open
  sumu-$Version-windows-x64.7z.001
with 7-Zip (https://www.7-zip.org/). Run sumu.exe from the extracted folder.

首次启动会为当前显卡在本地编译 TensorRT 加速引擎（数分钟，全程离线，不访问网络）。
编译产物缓存在 model_weights\ 下，下次启动直接命中。

The first launch compiles TensorRT engines for THIS GPU locally (a few minutes,
fully offline). Engines are cached under model_weights\ for the next start.
"@ | Set-Content -Path $readme -Encoding UTF8

Write-Host "release artifacts in $OutDir" -ForegroundColor Green
Get-ChildItem $OutDir | Format-Table Name, @{N="MB";E={[math]::Round($_.Length/1MB,1)}}
