# SPDX-FileCopyrightText: sumu Authors
# SPDX-License-Identifier: AGPL-3.0
#
# PyInstaller spec for the daily-use frozen player (onedir bundle). Build with:
#   .venv/Scripts/python.exe -m PyInstaller packaging/sumu.spec --noconfirm
# NEVER run pyinstaller against scripts/sumu_main.py directly -- that would
# generate/clobber a fresh (unconfigured) spec instead of using this one.
#
# This is a torch+CUDA+TensorRT app: expect a large (~7GB after layer-1 strip)
# onedir bundle and a slow first analysis pass. Weights are NOT bundled here --
# they're staged next to the exe by scripts/build_dist.ps1
# (sumu.ai._default_model_weights_dir() resolves
# <dir of sys.executable>/model_weights at runtime when sys.frozen).
import os
import sys

sys.setrecursionlimit(sys.getrecursionlimit() * 5)

from PyInstaller.utils.hooks import (
    collect_all,
    collect_dynamic_libs,
    collect_data_files,
    collect_submodules,
    copy_metadata,
)

# Runtime-unused payload that collect_all(torch/ultralytics/...) otherwise ships.
# Verified by quarantine smoke on the target machine (docs/packaging.md):
#   - torch/lib/*.lib + include/testing/share (~2.7GB): link/dev artifacts
#   - polars / scipy / matplotlib (~0.26GB): ultralytics/mmengine optional deps;
#     YOLO predict + BasicVSR++ load path never import them
# KEEP: PIL (ultralytics imports it eagerly) and tcl/tk (pyi_rth__tkinter hard-requires
# _tcl_data even though the player never uses tkinter).
_LAYER1_MOD_PREFIXES = ("polars", "scipy", "matplotlib", "mpl_toolkits")
_LAYER1_PATH_PREFIXES = (
    "torch/include/",
    "torch/testing/",
    "torch/share/",
    "polars/",
    "_polars_runtime_32/",
    "scipy/",
    "scipy.libs/",
    "matplotlib/",
    "mpl_toolkits/",
)


def _is_layer1_bloat(dest_name: str, src_path: str = "") -> bool:
    n = dest_name.replace("\\", "/")
    nl = n.lower()
    src = (src_path or "").replace("\\", "/").lower()
    # torch ships MSVC import/static libs next to CUDA DLLs. dest may be
    # torch/lib/foo.lib or bare foo.lib depending on the collector; use src
    # as a fallback so we don't keep ~2.7GB of link-time artifacts.
    if nl.endswith(".lib") and (
        nl.startswith("torch/")
        or "/torch/" in f"/{nl}"
        or "site-packages/torch/" in src
        or "/torch/lib/" in f"/{src}"
    ):
        return True
    for p in _LAYER1_PATH_PREFIXES:
        if nl == p.rstrip("/") or nl.startswith(p):
            return True
    for m in _LAYER1_MOD_PREFIXES:
        if nl == m or nl.startswith(m + ".") or nl.startswith(m + "/"):
            return True
    return False

# SPECPATH is injected by PyInstaller into this file's globals -- it's the
# directory containing THIS .spec file (packaging/), not the invocation cwd.
# All repo-relative paths below are resolved off it so `pyinstaller
# packaging/sumu.spec` works regardless of the caller's cwd.
ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821

block_cipher = None

datas = []
binaries = []
hiddenimports = []

# --- collect_all for the heavy packages with nontrivial data/binary/hidden-import needs ---
for pkg in ("torch", "torchvision", "ultralytics", "cv2"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# --- TensorRT trio: no PyInstaller hook ships for these, collect explicitly ---
# collect_all (not just dynamic_libs): engine *compile* needs data files + the
# builder-resource DLL that TensorRT LoadLibrary's at build time (not a PE import,
# so collect_dynamic_libs often drops it). That is why a frozen "compile
# acceleration engines" click used to fail without a CUDA toolkit / network.
for pkg in ("torch_tensorrt", "tensorrt", "tensorrt_libs"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as e:  # noqa: BLE001 -- optional if a pkg layout shifts
        print(f"[sumu.spec] collect_all({pkg}) skipped: {e}")
binaries += collect_dynamic_libs("torch_tensorrt")
binaries += collect_dynamic_libs("tensorrt")
binaries += collect_dynamic_libs("tensorrt_libs")
hiddenimports += collect_submodules("torch_tensorrt")
hiddenimports += collect_submodules("tensorrt")
datas += collect_data_files("torch_tensorrt")
datas += collect_data_files("tensorrt_libs")

# Walk tensorrt_libs / torch_tensorrt for every DLL (builder resource, plugins).
def _collect_pkg_dlls(pkg_name, dest_root):
    import importlib.util
    spec = importlib.util.find_spec(pkg_name)
    if spec is None or not spec.submodule_search_locations:
        return []
    out = []
    for loc in spec.submodule_search_locations:
        for root, _dirs, files in os.walk(loc):
            rel = os.path.relpath(root, loc)
            dest = dest_root if rel == "." else os.path.join(dest_root, rel)
            for f in files:
                if f.lower().endswith(".dll"):
                    out.append((os.path.join(root, f), dest))
                    # Also drop a basename copy at bundle root so LoadLibrary(basename) works.
                    out.append((os.path.join(root, f), "."))
    return out


binaries += _collect_pkg_dlls("tensorrt_libs", "tensorrt_libs")
binaries += _collect_pkg_dlls("torch_tensorrt", "torch_tensorrt")

# --- belt-and-suspenders: torch\lib CUDA DLLs (collect_all above may already grab these,
# but collect_dynamic_libs is cheap and idempotent-ish here -- duplicates are harmless) ---
binaries += collect_dynamic_libs("torch")

# --- mmengine: submodules + data files (configs etc. read at runtime) ---
hiddenimports += collect_submodules("mmengine")
datas += collect_data_files("mmengine")

# --- metadata read via importlib.metadata at runtime by these packages ---
for pkg in ("torch", "torchvision", "numpy", "ultralytics", "mmengine",
            "torch_tensorrt", "tensorrt"):
    try:
        datas += copy_metadata(pkg)
    except Exception as e:  # noqa: BLE001 -- tensorrt metadata name varies by wheel
        print(f"[sumu.spec] copy_metadata({pkg}) skipped: {e}")

# --- torch dynamic/native submodules not picked up by static analysis ---
hiddenimports += ["torch._C", "torch._C._jit", "torch._C._nvrtc", "torch._C._dynamo"]
# torch_tensorrt.compile(ir="dynamo") uses torch.export (NOT inductor). Keep dynamo
# / export importable in the frozen app so in-app engine compile works offline.
hiddenimports += collect_submodules("torch.export")
hiddenimports += collect_submodules("torch._export")
hiddenimports += collect_submodules("torch.fx")
hiddenimports += ["sympy", "torch._dynamo", "torch._C._dynamo.guards"]

# --- sumu's own package + native extension ---
hiddenimports += [
    "sumu_core",
    "sumu.app",
    "sumu.pipeline",
    "sumu.scheduler",
    "sumu.settings",
    "sumu.i18n",
    "sumu.offline_env",
] + collect_submodules("sumu")

# UI message catalogs (JSON). Embedded fallbacks in i18n.py cover a missing tree, but
# shipping the files keeps translations editable without a rebuild.
datas += [(os.path.join(ROOT, "python", "sumu", "locales"), "sumu/locales")]

# Vendored hls.js (Apache-2.0) for the web-streaming player: desktop browsers get HLS playback
# without a CDN. webstream.server reads it from the same dir as its module at runtime.
datas += [(os.path.join(ROOT, "python", "sumu", "webstream", "hls.min.js"), "sumu/webstream")]

# native extension + its co-located ffmpeg DLLs (loaded by sumu_core via
# load-time import / LOAD_WITH_ALTERED_SEARCH_PATH -- must stay next to it).
# Glob the av*/sw*.dll names instead of pinning sonames -- BtbN FFmpeg bumps
# avcodec-63 → avcodec-64 across master builds, and CI fetches "latest".
_native_sumu = os.path.join(ROOT, "python", "sumu")
_pyd = os.path.join(_native_sumu, "sumu_core.cp313-win_amd64.pyd")
if os.path.isfile(_pyd):
    binaries += [(_pyd, ".")]
else:
    print(f"[sumu.spec] WARN missing {_pyd}")
if os.path.isdir(_native_sumu):
    for name in sorted(os.listdir(_native_sumu)):
        lower = name.lower()
        if lower.endswith(".dll") and (lower.startswith("av") or lower.startswith("sw")):
            binaries += [(os.path.join(_native_sumu, name), ".")]

a = Analysis(
    [os.path.join(ROOT, "scripts", "sumu_main.py")],
    pathex=[os.path.join(ROOT, "python"), os.path.join(ROOT, "python", "sumu")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[os.path.join(ROOT, "packaging", "rthook_dll_path.py")],
    # av: daily entry never uses PyAV (only run_player --correctness).
    # polars/scipy/matplotlib: ultralytics/mmengine optional deps; layer-1 strip
    # also filters whatever collect_all still injects into binaries/datas/pure.
    excludes=["av", "polars", "scipy", "matplotlib", "mpl_toolkits"],
    noarchive=False,
    cipher=block_cipher,
)

# Layer-1 size strip (post-Analysis). collect_all / collect_dynamic_libs ignore
# excludes= for binary/data payloads, so filter TOC entries here. See
# _is_layer1_bloat above and docs/packaging.md for the measured savings.
def _strip_layer1(toc, label):
    kept, dropped = [], []
    for entry in toc:
        dest = entry[0]
        src = entry[1] if len(entry) > 1 else ""
        if _is_layer1_bloat(dest, src):
            dropped.append(dest)
        else:
            kept.append(entry)
    if dropped:
        print(f"[sumu.spec] layer1 strip {label}: dropped {len(dropped)} entries "
              f"(e.g. {dropped[:3]})")
    return kept


a.binaries = _strip_layer1(a.binaries, "binaries")
a.datas = _strip_layer1(a.datas, "datas")
a.pure = _strip_layer1(a.pure, "pure")

# ctypes in TensorRT looks up nvrtc64_120_0.dll (CUDA 12.0 name). torch 2.8+cu128
# ships nvrtc64_128_0.dll. Alias the 128 file under the 120 basename so a machine
# with no CUDA toolkit can still compile engines offline.
def _alias_nvrtc_120(toc):
    src_128 = None
    src_builtins = None
    names = {entry[0].replace("\\", "/").lower() for entry in toc}
    for dest, src, *rest in toc:
        d = dest.replace("\\", "/").lower()
        base = os.path.basename(d)
        if base.startswith("nvrtc64_128") and d.endswith(".dll"):
            src_128 = src
        if base.startswith("nvrtc-builtins64_128") and d.endswith(".dll"):
            src_builtins = src
    extra = []
    if src_128 and "nvrtc64_120_0.dll" not in names:
        extra.append(("nvrtc64_120_0.dll", src_128, "BINARY"))
        print("[sumu.spec] aliased nvrtc64_128 -> nvrtc64_120_0.dll")
    if src_builtins and "nvrtc-builtins64_120.dll" not in names:
        extra.append(("nvrtc-builtins64_120.dll", src_builtins, "BINARY"))
        print("[sumu.spec] aliased nvrtc-builtins64_128 -> nvrtc-builtins64_120.dll")
    return extra


a.binaries += _alias_nvrtc_120(a.binaries)

# SUMU_FAST_FREEZE (set by scripts/build_dist.ps1 -FastFreeze): skip COLLECT.
# COLLECT.assemble() unconditionally _make_clean_directory()s the whole onedir
# tree and re-copies every binary/data file every run (PyInstaller has no
# incremental copy there -- see PyInstaller/building/api.py COLLECT._check_guts,
# which always returns True "in order to clean the output directory"). That's
# a full re-copy of ~7GB of torch/cv2/tensorrt payload that never changes
# between ordinary dev iterations. EXE(exclude_binaries=True) itself only
# writes the thin bootloader+PYZ exe (our own compiled Python) to
# build/sumu/sumu.exe and does NOT touch dist/ -- so when only sumu's own
# source or the native extension changed, build_dist.ps1's fast path runs the
# spec with COLLECT skipped, then hand-copies just that exe (and the native
# pyd/ffmpeg DLLs) over the existing dist/sumu tree. This is UNSAFE if the
# dependency set itself changed (new/updated torch/cv2/tensorrt/mmengine
# binaries or data files) -- those require a real COLLECT to land in dist/.
_fast_freeze = bool(os.environ.get("SUMU_FAST_FREEZE"))

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="sumu",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # GUI subsystem (like blender.exe). scripts/sumu_main.py AllocConsole()
                    # at startup so a system console sits beside the player; stdout/stderr
                    # are teed to that console AND <exe dir>/sumu.log.
    disable_windowed_traceback=False,
    icon=os.path.join(ROOT, "assets", "generated", "sumu.ico"),
)


if not _fast_freeze:
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="sumu",
    )
