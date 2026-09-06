# SPDX-FileCopyrightText: Lada Authors
# SPDX-License-Identifier: AGPL-3.0
#
# Ported from jasna (github.com/Kruk2/jasna). The PyInstaller `_frozen`
# patch from the source is dropped — source/dev runs don't need it.
from __future__ import annotations

import io
import logging
import os
import shutil
import tempfile
import time
import warnings

import torch

logger = logging.getLogger(__name__)

_torchtrt_muted = False
_torchtrt_swallow = True

# TensorRT build-time scratch cap. workspace only bounds how much scratch a tactic may
# use during autotuning at BUILD time -- it is NOT runtime engine memory, and a tactic
# needing more than this is simply dropped (TRT always keeps low-workspace fallbacks, so
# a small cap never fails the build).
#
# Why cap at 2 GiB: measured (scripts/measure_trt_build.py, RTX 4080). Two full builds,
# one with workspace=13.88GiB (free*0.95 on a clean GPU) and one pinned down to 4.85GiB,
# came out statistically identical -- 163.9s vs 168.8s total, per-engine within noise --
# and free-VRAM traces showed the hungriest engine (dynamic preprocess) only ever consumed
# ~2.4GiB *at optimization_level=5*. At level 3, peak is lower. Capping at 2 GiB gives
# TRT enough room while leaving more headroom so a busy/fragmented GPU never spills the
# build onto sysmem (PCIe, 10-100x slower). If the cap is too tight for a future engine it
# just drops a tactic, never fails the build — TRT always keeps low-workspace fallbacks.
_WORKSPACE_CAP_BYTES = 2 * 1024**3


def _mute_torch_tensorrt(*, swallow: bool = True) -> None:
    """Keep TensorRT chatter off the hot path. During in-app compile, ``swallow=False``
    so ERROR still reaches stderr (frozen: ``sumu.log``) -- otherwise a failed
    engine build looks like a silent hang then a generic 'compile failed'."""
    global _torchtrt_muted, _torchtrt_swallow
    import tensorrt as trt
    import torch_tensorrt
    torch_tensorrt.logging._LOGGER.setLevel(logging.ERROR)
    torch_tensorrt.logging._LOGGER.handlers.clear()
    if swallow:
        torch_tensorrt.logging._LOGGER.addHandler(logging.NullHandler())
        torch_tensorrt.logging._LOGGER.propagate = False
    else:
        torch_tensorrt.logging._LOGGER.propagate = True
    torch.ops.tensorrt.set_logging_level(int(trt.ILogger.Severity.ERROR))
    _torchtrt_muted = True
    _torchtrt_swallow = swallow


def _is_ascii_path(path: str) -> bool:
    try:
        path.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _ascii_writable_dir() -> str | None:
    """A directory whose full path is ASCII and writable — safe for PyTorchFileWriter."""
    drive = os.environ.get("SystemDrive", "C:")
    local_app = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        tempfile.gettempdir(),
        os.path.join(local_app, "Temp") if local_app else "",
        os.path.join(os.environ.get("ProgramData", os.path.join(drive, "ProgramData")),
                     "sumu", "trt-scratch"),
        os.path.join(drive, "Temp"),
        os.path.join(drive, "Windows", "Temp"),
    ]
    for candidate in candidates:
        if not candidate or not _is_ascii_path(candidate):
            continue
        try:
            os.makedirs(candidate, exist_ok=True)
            probe = os.path.join(candidate, "sumu_trt_probe.tmp")
            with open(probe, "wb") as f:
                f.write(b"ok")
            os.remove(probe)
            return candidate
        except OSError:
            continue
    return None


def _atomic_write_bytes(dest_path: str, data: bytes) -> None:
    tmp_path = dest_path + ".tmp"
    try:
        with open(tmp_path, "wb") as f:
            f.write(data)
        os.replace(tmp_path, dest_path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def _write_archive_to_path(writer, dest_path: str) -> None:
    """Serialize via ``writer(buffer_or_ascii_path)`` then Python-write ``dest_path``.

    ``torch.export.save`` / ``PyTorchFileWriter`` check the parent dir with
    ``std::filesystem`` on a ``std::string``. On Windows that string is the ANSI
    code page, so a Unicode install path (e.g. ``E:\\去马赛克\\...``) is reported
    as "Parent directory does not exist" even after ``os.makedirs``. Load already
    opens the file in Python (``open(..., "rb")`` → ``torch.export.load(f)``);
    save must not hand the Unicode path to C++.
    """
    dest_path = os.path.abspath(dest_path)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    first_err: BaseException | None = None
    buf = io.BytesIO()
    try:
        writer(buf)
        data = buf.getvalue()
        if data:
            _atomic_write_bytes(dest_path, data)
            return
        first_err = RuntimeError(f"TRT archive save produced 0 bytes ({dest_path})")
    except Exception as e:  # noqa: BLE001 — fall back to an ASCII temp path
        first_err = e
    scratch = _ascii_writable_dir()
    if scratch is None:
        if first_err is not None:
            raise first_err
        raise RuntimeError(f"TRT engine save failed ({dest_path})")
    tmp_dir = tempfile.mkdtemp(prefix="sumu-trt-", dir=scratch)
    try:
        tmp_file = os.path.join(tmp_dir, "engine.pt2")
        writer(tmp_file)
        if not os.path.isfile(tmp_file) or os.path.getsize(tmp_file) == 0:
            if first_err is not None:
                raise first_err
            raise RuntimeError(f"TRT archive save produced no file ({dest_path})")
        if os.path.exists(dest_path):
            os.remove(dest_path)
        shutil.move(tmp_file, dest_path)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def get_workspace_size_bytes() -> int:
    # Cap the build-time scratch (see _WORKSPACE_CAP_BYTES): measured to leave build speed
    # unchanged while bounding peak VRAM so compiling under memory pressure can't spill onto
    # sysmem. Still take the min with free*0.9 so a low-VRAM GPU never over-requests.
    free, _total = torch.cuda.mem_get_info()
    return min(int(free * 0.9), _WORKSPACE_CAP_BYTES)


def load_torchtrt_export(*, checkpoint_path: str, device: torch.device) -> torch.nn.Module:
    _mute_torch_tensorrt()

    logger.debug("Loading TensorRT export from %s", checkpoint_path)
    fake_reg_logger = logging.getLogger("torch._library.fake_class_registry")
    prev_level = fake_reg_logger.level
    fake_reg_logger.setLevel(logging.ERROR)
    try:
        export_logger = logging.getLogger("torch.export")
        prev_export_level = export_logger.level
        export_logger.setLevel(logging.ERROR)
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message=".*PytorchStreamReader.*")
                try:
                    with open(checkpoint_path, "rb") as f:
                        trt_module = torch.export.load(f).module()
                except Exception:
                    trt_module = torch.load(checkpoint_path, map_location=device, weights_only=False)
                result = trt_module.to(device)
        finally:
            export_logger.setLevel(prev_export_level)
        return result
    finally:
        fake_reg_logger.setLevel(prev_level)


def compile_and_save_torchtrt_dynamo(
    *,
    module: torch.nn.Module,
    inputs: list,
    output_path: str,
    dtype: torch.dtype,
    workspace_size_bytes: int,
    message: str,
    device: torch.device | None = None,
    optimization_level: int = 3,
) -> str:
    """Compile a module to TensorRT and save the result.

    ``inputs`` may be plain tensors (static shapes) or
    ``torch_tensorrt.Input`` specs (dynamic shapes).  Pass ``device``
    explicitly when using ``torch_tensorrt.Input`` objects.
    """
    import torch_tensorrt  # type: ignore[import-not-found]
    # Don't swallow TRT ERROR during compile: the frozen GUI redirects stderr to sumu.log,
    # which is the diagnostic when GUI-startup compile fails.
    _mute_torch_tensorrt(swallow=False)

    has_dynamic = any(isinstance(inp, torch_tensorrt.Input) for inp in inputs)
    if device is None:
        device = inputs[0].device
    print(message)
    logger.info("%s", message)
    # Also surface per-engine progress ("Compiling sub-engine i/6…") to whatever
    # UI is driving the load (GUI spinner label). No-op if nobody registered.
    try:
        from sumu.ai.restorationpipeline.progress import report_load_progress
        report_load_progress(message)
    except Exception:  # noqa: BLE001 - progress reporting must never break compilation
        pass
    # Build-time instrumentation (I10: measure before optimizing). Kept in place after the
    # workspace investigation: logs the workspace handed to TRT, the compile-vs-save split,
    # and free-VRAM before/after each engine, tagged so a single grep reconstructs a build's
    # per-engine timing and peak-VRAM trace (this is how the 3 GiB cap was validated, and how
    # a future free-VRAM regression would be caught).
    ws_gib = int(workspace_size_bytes) / 1024**3
    free_before, _ = torch.cuda.mem_get_info(device)
    kind = "dynamic" if has_dynamic else "static"
    # Aggressive cache flush before every engine build: PyTorch's caching allocator
    # may hold fragmented blocks from prior engines / model weights, and TRT sees that
    # as "VRAM not free" → spills to sysmem (PCIe, 10-100x slower). Emptying the cache
    # gives TRT the cleanest possible view of free VRAM right before it allocates.
    if device is not None and device.type == "cuda":
        torch.cuda.empty_cache()
    t_compile_start = time.perf_counter()
    with torch.cuda.device(device):
        trt_gm = torch_tensorrt.compile(
            module,
            ir="dynamo",
            inputs=inputs,
            min_block_size=1,
            workspace_size=int(workspace_size_bytes),
            enabled_precisions={dtype},
            use_fp32_acc=False,
            use_explicit_typing=False,
            sparse_weights=False,
            optimization_level=int(optimization_level),
            hardware_compatible=False,
            use_python_runtime=False,
            cache_built_engines=False,
            reuse_cached_engines=False,
            truncate_double=True,
        )
        t_compiled = time.perf_counter()
        fake_reg_logger = logging.getLogger("torch._library.fake_class_registry")
        prev_level = fake_reg_logger.level
        fake_reg_logger.setLevel(logging.ERROR)
        try:
            def _save(target) -> None:
                if has_dynamic:
                    _save_with_dynamic_shapes(trt_gm, target, inputs, device, dtype)
                else:
                    torch_tensorrt.save(trt_gm, target, inputs=inputs)

            _write_archive_to_path(_save, output_path)
        finally:
            fake_reg_logger.setLevel(prev_level)
    t_saved = time.perf_counter()
    free_after, _ = torch.cuda.mem_get_info(device)
    timing_line = (
        f"[trt-timing] {message} | kind={kind} opt={int(optimization_level)} "
        f"workspace={ws_gib:.2f}GiB | compile={t_compiled - t_compile_start:.1f}s "
        f"save={t_saved - t_compiled:.1f}s total={t_saved - t_compile_start:.1f}s | "
        f"free_before={free_before / 1024**3:.2f}GiB free_after={free_after / 1024**3:.2f}GiB"
    )
    print(timing_line)
    logger.info("%s", timing_line)
    del trt_gm
    return output_path


def _save_with_dynamic_shapes(
    trt_gm: torch.nn.Module,
    output_path: str,
    inputs: list,
    device: torch.device,
    dtype: torch.dtype,
) -> None:
    """Save a TRT-compiled GraphModule that uses dynamic shapes.

    Builds sample tensors and a ``dynamic_shapes`` spec from
    ``torch_tensorrt.Input`` objects so that ``torch.export.export``
    records the correct symbolic dimension constraints.
    """
    import torch_tensorrt  # type: ignore[import-not-found]
    from torch.export import Dim

    sample_args: list[torch.Tensor] = []
    dyn_shapes: list[dict[int, Dim] | None] = []

    for inp in inputs:
        if isinstance(inp, torch_tensorrt.Input):
            shape_dict = inp.shape
            opt = shape_dict["opt_shape"]
            sample_args.append(torch.randn(*opt, dtype=dtype, device=device))

            min_s, max_s = shape_dict["min_shape"], shape_dict["max_shape"]
            dim_map: dict[int, Dim] = {}
            for d in range(len(opt)):
                if min_s[d] != max_s[d]:
                    dim_map[d] = Dim(f"d{d}", min=int(min_s[d]), max=int(max_s[d]))
            dyn_shapes.append(dim_map if dim_map else None)
        else:
            sample_args.append(inp)
            dyn_shapes.append(None)

    try:
        ep = torch.export.export(
            trt_gm,
            tuple(sample_args),
            dynamic_shapes=tuple(dyn_shapes),
            strict=False,
        )
        torch.export.save(ep, output_path)
    except RuntimeError:
        logger.debug(
            "torch.export.export failed (multi-subgraph dynamic shapes); "
            "falling back to torch.save for %s", output_path,
        )
        torch.save(trt_gm, output_path)
