# SPDX-FileCopyrightText: sumu Authors
# SPDX-License-Identifier: AGPL-3.0
#
# Copy TensorRT *compile-time* runtime into the frozen onedir after PyInstaller
# COLLECT. Inference-only collection often drops:
#   - nvinfer_builder_resource_*.dll (LoadLibrary at engine-build time, not a PE import)
#   - nvrtc64_120_0.dll (ctypes lookup uses the CUDA 12.0 basename; torch cu128
#     ships nvrtc64_128_0.dll)
# Without these, GUI-startup TRT compile fails on a machine with no CUDA toolkit
# and no network -- which is every end-user install.
from __future__ import annotations

import glob
import os
import shutil
import sys


def _site_packages() -> str:
    # Prefer the venv that is running us.
    sp = os.path.join(sys.prefix, "Lib", "site-packages")
    if os.path.isdir(sp):
        return sp
    import site

    for p in site.getsitepackages():
        if os.path.isdir(p):
            return p
    raise SystemExit("site-packages not found")


def _copy_file(src: str, dest: str) -> bool:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.isfile(dest) and os.path.getsize(dest) == os.path.getsize(src):
        return False
    shutil.copy2(src, dest)
    print(f"  staged {os.path.basename(src)} -> {dest}")
    return True


def _pkg_dir(sp: str, *parts: str) -> str | None:
    d = os.path.join(sp, *parts)
    return d if os.path.isdir(d) else None


def stage(internal_dir: str) -> int:
    if not os.path.isdir(internal_dir):
        raise SystemExit(f"frozen _internal not found: {internal_dir}")
    sp = _site_packages()
    n = 0

    # TensorRT libs: keep package dir AND copy basename into _internal root so
    # LoadLibrary("nvinfer_builder_resource_10.dll") resolves via PATH.
    trt_src = _pkg_dir(sp, "tensorrt_libs")
    if trt_src:
        for src in glob.glob(os.path.join(trt_src, "*.dll")):
            name = os.path.basename(src)
            if _copy_file(src, os.path.join(internal_dir, "tensorrt_libs", name)):
                n += 1
            if _copy_file(src, os.path.join(internal_dir, name)):
                n += 1
    else:
        print("WARN: tensorrt_libs package dir missing in site-packages")

    # torch_tensorrt native libs (libtorchtrt etc.)
    for rel in (
        ("torch_tensorrt", "lib"),
        ("torch_tensorrt", "bin"),
        ("torch_tensorrt",),
    ):
        d = _pkg_dir(sp, *rel)
        if not d:
            continue
        dest_sub = os.path.join(internal_dir, *rel)
        for src in glob.glob(os.path.join(d, "*.dll")):
            name = os.path.basename(src)
            if _copy_file(src, os.path.join(dest_sub, name)):
                n += 1
            if _copy_file(src, os.path.join(internal_dir, name)):
                n += 1

    # NVRTC + nvJitLink from torch/lib. ctypes in TensorRT looks for the CUDA 12.0
    # basename nvrtc64_120_0.dll even when torch cu128 ships nvrtc64_128_0.dll.
    torch_lib = _pkg_dir(sp, "torch", "lib")
    dest_torch_lib = os.path.join(internal_dir, "torch", "lib")
    nvrtc_128 = None
    builtins_128 = None
    if torch_lib:
        os.makedirs(dest_torch_lib, exist_ok=True)
        for src in glob.glob(os.path.join(torch_lib, "*.dll")):
            name = os.path.basename(src).lower()
            if not (
                name.startswith("nvrtc")
                or name.startswith("nvjitlink")
                or name.startswith("cudart")
            ):
                continue
            dest = os.path.join(dest_torch_lib, os.path.basename(src))
            if _copy_file(src, dest):
                n += 1
            # Also drop NVRTC next to the exe search root.
            if name.startswith("nvrtc"):
                if _copy_file(src, os.path.join(internal_dir, os.path.basename(src))):
                    n += 1
            if name.startswith("nvrtc64_128"):
                nvrtc_128 = src
            if name.startswith("nvrtc-builtins64_128"):
                builtins_128 = src

    def _alias(src: str | None, alias_name: str) -> None:
        nonlocal n
        if src is None or not os.path.isfile(src):
            return
        for dest_dir in (internal_dir, dest_torch_lib):
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, alias_name)
            if os.path.isfile(dest):
                continue
            shutil.copy2(src, dest)
            print(f"  aliased {os.path.basename(src)} -> {dest}")
            n += 1

    _alias(nvrtc_128, "nvrtc64_120_0.dll")
    _alias(builtins_128, "nvrtc-builtins64_120.dll")

    builder = glob.glob(os.path.join(internal_dir, "*nvinfer_builder_resource*.dll"))
    nvrtc120 = os.path.isfile(os.path.join(internal_dir, "nvrtc64_120_0.dll")) or os.path.isfile(
        os.path.join(dest_torch_lib, "nvrtc64_120_0.dll")
    )
    print(f"TRT compile runtime staged ({n} copies). "
          f"builder_resource={'yes' if builder else 'MISSING'} "
          f"nvrtc64_120_0={'yes' if nvrtc120 else 'MISSING'}")
    if not builder:
        print("WARN: nvinfer_builder_resource*.dll not found -- in-app TRT compile will fail")
        return 1
    return 0


def main() -> int:
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    internal = os.path.join(repo, "dist", "sumu", "_internal")
    if len(sys.argv) > 1:
        internal = sys.argv[1]
    return stage(internal)


if __name__ == "__main__":
    raise SystemExit(main())
