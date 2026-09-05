# SPDX-FileCopyrightText: sumu Authors
# SPDX-License-Identifier: AGPL-3.0
#
# PyInstaller runtime hook -- runs at frozen-app startup, before any of our own
# modules import. Makes sure the onedir bundle dir (where the ffmpeg DLLs,
# sumu_core.pyd's DLL deps, torch/TensorRT/CUDA DLLs all land -- see
# packaging/sumu.spec) is on the DLL search path, so LoadLibrary calls made by
# sumu_core, torch, torch_tensorrt, tensorrt resolve without needing PATH set
# externally.
#
# Also freezes hub/telemetry env vars so TensorRT compile never waits on the
# network (the in-app compile button must work fully offline).
import os
import sys

base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))

try:
    from sumu.offline_env import apply_dll_search_path, apply_offline_runtime_env

    apply_offline_runtime_env(base)
    apply_dll_search_path(base)
except Exception:
    # Bootstrap fallback if sumu.offline_env is not yet importable.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_DATASETS_OFFLINE", "1")
    os.environ.setdefault("ULTRALYTICS_OFFLINE", "1")
    os.environ.setdefault("YOLO_VERBOSE", "false")
    os.environ.setdefault("ALBUMENTATIONS_OFFLINE", "1")
    os.environ.setdefault("ALBUMENTATIONS_NO_TELEMETRY", "1")
    os.environ.setdefault("NO_ALBUMENTATIONS_UPDATE", "1")
    os.environ["PATH"] = base + os.pathsep + os.environ.get("PATH", "")
    try:
        os.add_dll_directory(base)
    except Exception:
        pass
    torch_lib = os.path.join(base, "torch", "lib")
    if os.path.isdir(torch_lib):
        os.environ["PATH"] = torch_lib + os.pathsep + os.environ["PATH"]
        try:
            os.add_dll_directory(torch_lib)
        except Exception:
            pass
