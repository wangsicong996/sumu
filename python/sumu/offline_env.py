# SPDX-FileCopyrightText: sumu Authors
# SPDX-License-Identifier: AGPL-3.0
#
# Freeze TensorRT compile + model load onto local files. GUI-startup compile
# must not phone HuggingFace / PyPI / Ultralytics / Torch Hub: engines are built
# from the bundled torch-tensorrt + TensorRT builder DLLs + staged .pth/.pt.
from __future__ import annotations

import os
from collections.abc import Iterable


def _setdefault(key: str, value: str) -> None:
    os.environ.setdefault(key, value)


def apply_offline_runtime_env(bundle_dir: str | None = None) -> None:
    """Mark the process offline for every hub the AI stack might ping.

    Safe to call more than once (setdefault). ``bundle_dir`` is the PyInstaller
    ``_MEIPASS`` / ``_internal`` folder when frozen; omitted in dev.
    """
    _setdefault("HF_HUB_OFFLINE", "1")
    _setdefault("TRANSFORMERS_OFFLINE", "1")
    _setdefault("HF_DATASETS_OFFLINE", "1")
    _setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    _setdefault("ULTRALYTICS_OFFLINE", "1")
    _setdefault("YOLO_VERBOSE", "false")
    _setdefault("ALBUMENTATIONS_OFFLINE", "1")
    _setdefault("ALBUMENTATIONS_NO_TELEMETRY", "1")
    _setdefault("NO_ALBUMENTATIONS_UPDATE", "1")
    # Stop pip/version pings some deps still do even with telemetry patched.
    _setdefault("DISABLE_TELEMETRY", "1")
    if bundle_dir:
        cache = os.path.join(bundle_dir, "cache")
        _setdefault("TORCH_HOME", os.path.join(cache, "torch"))
        _setdefault("XDG_CACHE_HOME", cache)
        _setdefault("CUDA_PATH", bundle_dir)
        _setdefault("CUDA_HOME", bundle_dir)
        _setdefault("CUDA_PATH_V12_8", bundle_dir)


def dll_search_dirs(bundle_dir: str) -> list[str]:
    """Directories that must be on PATH / os.add_dll_directory for TRT compile.

    TensorRT engine *build* LoadLibrary's ``nvinfer_builder_resource_*.dll`` by
    basename (not a PE import), and ctypes looks up ``nvrtc64_120_0.dll`` on
    PATH. Both live under torch/lib or tensorrt_libs, not the bundle root.
    """
    candidates = [
        bundle_dir,
        os.path.join(bundle_dir, "torch", "lib"),
        os.path.join(bundle_dir, "tensorrt_libs"),
        os.path.join(bundle_dir, "torch_tensorrt"),
        os.path.join(bundle_dir, "torch_tensorrt", "lib"),
        os.path.join(bundle_dir, "torch_tensorrt", "bin"),
        os.path.join(bundle_dir, "nvidia", "cuda_nvrtc", "bin"),
        os.path.join(bundle_dir, "nvidia", "cuda_runtime", "bin"),
        os.path.join(bundle_dir, "nvidia", "cublas", "bin"),
        os.path.join(bundle_dir, "nvidia", "cudnn", "bin"),
        os.path.join(bundle_dir, "cv2"),
    ]
    extra: list[str] = []
    nvidia = os.path.join(bundle_dir, "nvidia")
    if os.path.isdir(nvidia):
        for root, dirs, _files in os.walk(nvidia):
            name = os.path.basename(root).lower()
            if name in ("bin", "lib", "lib64") or root.endswith(os.path.join("nvrtc", "bin")):
                extra.append(root)
            # Don't descend forever; nvidia wheels are shallow.
            if root.count(os.sep) - nvidia.count(os.sep) >= 3:
                dirs.clear()
    seen: set[str] = set()
    out: list[str] = []
    for d in _unique(candidates + extra):
        if d not in seen and os.path.isdir(d):
            seen.add(d)
            out.append(d)
    return out


def _unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = os.path.normcase(os.path.normpath(item))
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def apply_dll_search_path(bundle_dir: str) -> None:
    dirs = dll_search_dirs(bundle_dir)
    os.environ["PATH"] = os.pathsep.join(dirs + [os.environ.get("PATH", "")])
    adder = getattr(os, "add_dll_directory", None)
    if adder is None:
        return
    for d in dirs:
        try:
            adder(d)
        except OSError:
            pass
