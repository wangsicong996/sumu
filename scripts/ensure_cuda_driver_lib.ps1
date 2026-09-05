# SPDX-FileCopyrightText: sumu Authors
# SPDX-License-Identifier: AGPL-3.0
#
# Make CUDA Driver-API link inputs exist for native/build.bat:
#   %CUDA_PATH%\include\cuda.h
#   %CUDA_PATH%\include\cudaD3D11.h
#   %CUDA_PATH%\lib\x64\cuda.lib
#
# GitHub-hosted CUDA (Jimver, sub-package nvcc/cudart) often installs the
# compiler and runtime headers but NOT lib\x64\cuda.lib (the nvcuda.dll import
# lib). This script:
#   1. Downloads NVIDIA cuda_cudart windows redist if headers are missing
#   2. Builds cuda.lib from native/third_party/cuda_driver_stub/nvcuda.def
#      via MSVC lib.exe if the import lib is missing
param(
    [string]$CudaPath = $env:CUDA_PATH
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

function Get-RemoteFile([string]$Url, [string]$Dest) {
    $dir = Split-Path -Parent $Dest
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    Write-Host "GET $Url"
    & curl.exe -L --fail --retry 5 --retry-delay 3 -o $Dest $Url
    if ($LASTEXITCODE -ne 0) { throw "curl failed for $Url (exit $LASTEXITCODE)" }
}

function Get-LibExe {
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path $vswhere)) { throw "vswhere.exe not found" }
    $root = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if (-not $root) { throw "VS2022 with MSVC x64 tools not found" }
    $msvc = Get-ChildItem (Join-Path $root "VC\Tools\MSVC") -Directory | Sort-Object Name -Descending | Select-Object -First 1
    $lib = Join-Path $msvc.FullName "bin\Hostx64\x64\lib.exe"
    if (-not (Test-Path $lib)) { throw "lib.exe not found at $lib" }
    return $lib
}

if (-not $CudaPath) {
    foreach ($ver in @("v12.8", "v13.3", "v12.9", "v12.6", "v12.4")) {
        $cand = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\$ver"
        if (Test-Path (Join-Path $cand "include")) { $CudaPath = $cand; break }
    }
}
if (-not $CudaPath) {
    $CudaPath = Join-Path $RepoRoot "native\third_party\cuda_driver_api"
}
Write-Host "CUDA_PATH candidate: $CudaPath"

$includeDir = Join-Path $CudaPath "include"
$libDir = Join-Path $CudaPath "lib\x64"
$header = Join-Path $includeDir "cuda.h"
$d3dHeader = Join-Path $includeDir "cudaD3D11.h"
$cudaLib = Join-Path $libDir "cuda.lib"

if (-not (Test-Path $header) -or -not (Test-Path $d3dHeader)) {
    Write-Host "CUDA headers missing -- fetching NVIDIA cuda_cudart windows redist"
    $urls = @(
        "https://developer.download.nvidia.com/compute/cuda/redist/cuda_cudart/windows-x86_64/cuda_cudart-windows-x86_64-12.8.90-archive.zip",
        "https://developer.download.nvidia.com/compute/cuda/redist/cuda_cudart/windows-x86_64/cuda_cudart-windows-x86_64-12.8.57-archive.zip"
    )
    $zip = Join-Path $env:TEMP "cuda_cudart-windows.zip"
    $ok = $false
    foreach ($u in $urls) {
        try {
            Get-RemoteFile $u $zip
            $ok = $true
            break
        } catch {
            Write-Host "skip $u : $_"
        }
    }
    if (-not $ok) { throw "could not download cuda_cudart windows redist (need cuda.h / cudaD3D11.h)" }
    $extract = Join-Path $env:TEMP "cuda_cudart_extract"
    if (Test-Path $extract) { Remove-Item -Recurse -Force $extract }
    Expand-Archive -Path $zip -DestinationPath $extract -Force
    # Archive layout: <stem>/include/cuda.h and optionally <stem>/lib/x64/*.lib
    $fromInclude = Get-ChildItem $extract -Recurse -Filter "cuda.h" |
        Select-Object -First 1 |
        ForEach-Object { $_.DirectoryName }
    if (-not $fromInclude) { throw "cuda.h not found inside cuda_cudart archive" }
    if (-not (Test-Path $includeDir)) { New-Item -ItemType Directory -Path $includeDir -Force | Out-Null }
    Copy-Item -Path (Join-Path $fromInclude "*") -Destination $includeDir -Recurse -Force
    $fromLib = Join-Path (Split-Path $fromInclude -Parent) "lib\x64"
    if (Test-Path $fromLib) {
        if (-not (Test-Path $libDir)) { New-Item -ItemType Directory -Path $libDir -Force | Out-Null }
        Copy-Item -Path (Join-Path $fromLib "*") -Destination $libDir -Force
    }
    Write-Host "headers staged at $includeDir"
}

if (-not (Test-Path $header)) { throw "cuda.h still missing at $header" }
if (-not (Test-Path $d3dHeader)) { throw "cudaD3D11.h still missing at $d3dHeader" }

if (-not (Test-Path $cudaLib)) {
    Write-Host "cuda.lib missing -- building nvcuda import lib from nvcuda.def"
    $def = Join-Path $RepoRoot "native\third_party\cuda_driver_stub\nvcuda.def"
    if (-not (Test-Path $def)) { throw "missing $def" }
    $libExe = Get-LibExe
    $writeDir = $libDir
    try {
        if (-not (Test-Path $writeDir)) { New-Item -ItemType Directory -Path $writeDir -Force | Out-Null }
        # Probe writability (Program Files may be locked down).
        $probe = Join-Path $writeDir ".sumu_write_probe"
        New-Item -ItemType File -Path $probe -Force | Out-Null
        Remove-Item $probe -Force
    } catch {
        $CudaPath = Join-Path $RepoRoot "native\third_party\cuda_driver_api"
        $includeDir = Join-Path $CudaPath "include"
        $libDir = Join-Path $CudaPath "lib\x64"
        $header = Join-Path $includeDir "cuda.h"
        $d3dHeader = Join-Path $includeDir "cudaD3D11.h"
        $cudaLib = Join-Path $libDir "cuda.lib"
        $writeDir = $libDir
        Write-Host "CUDA_PATH not writable; falling back to $CudaPath"
        if (-not (Test-Path $includeDir)) { New-Item -ItemType Directory -Path $includeDir -Force | Out-Null }
        $origInclude = Join-Path $env:CUDA_PATH "include"
        if ($env:CUDA_PATH -and (Test-Path $origInclude)) {
            Copy-Item -Path (Join-Path $origInclude "*") -Destination $includeDir -Recurse -Force
        }
        if (-not (Test-Path $writeDir)) { New-Item -ItemType Directory -Path $writeDir -Force | Out-Null }
    }
    $exp = Join-Path $writeDir "nvcuda.exp"
    & $libExe "/def:$def" "/out:$cudaLib" "/machine:x64"
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $cudaLib)) {
        throw "lib.exe failed to produce $cudaLib"
    }
    if (Test-Path $exp) { Remove-Item $exp -Force }
    Write-Host "wrote $cudaLib"
}

Write-Host "CUDA driver-API link inputs OK:"
Write-Host "  $header"
Write-Host "  $d3dHeader"
Write-Host "  $cudaLib"

# Persist for later steps (GitHub Actions) and for native/build.bat.
if ($env:GITHUB_ENV) {
    Add-Content -Path $env:GITHUB_ENV -Value "CUDA_PATH=$CudaPath"
}
$env:CUDA_PATH = $CudaPath
