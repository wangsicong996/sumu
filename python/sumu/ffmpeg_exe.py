# SPDX-FileCopyrightText: sumu Authors
# SPDX-License-Identifier: AGPL-3.0
"""Resolve ``ffmpeg.exe`` / ``ffprobe.exe`` for export, webstream, and thumbnails.

Frozen onedir stages a **static** BtbN n8.1 ``ffmpeg.exe`` under ``_internal/ffmpeg-cli/``
(NVENC SDK 13.0). That is a different tree from the master gpl-*shared* DLLs native
decode uses -- master is built against NVENC 13.1 and fails on 13.0 drivers.
Dev prefers ``spikes/.../ffmpeg-cli``, then PATH, then the shared spike0 ``ffmpeg/bin``.
"""
from __future__ import annotations

import os
import shutil
import sys

_cached: dict[str, str] = {}


def _exe_names(stem: str) -> tuple[str, ...]:
    if sys.platform == "win32":
        return (f"{stem}.exe", stem)
    return (stem,)


def _repo_cli_bin() -> str | None:
    here = os.path.dirname(os.path.abspath(__file__))
    cli = os.path.normpath(os.path.join(
        here, "..", "..",
        "spikes", "spike0_d3d11_present", "third_party", "ffmpeg-cli", "bin",
    ))
    return cli if os.path.isdir(cli) else None


def _repo_shared_bin() -> str | None:
    here = os.path.dirname(os.path.abspath(__file__))
    spike = os.path.normpath(os.path.join(
        here, "..", "..",
        "spikes", "spike0_d3d11_present", "third_party", "ffmpeg", "bin",
    ))
    return spike if os.path.isdir(spike) else None


def _frozen_dirs() -> list[str]:
    if not getattr(sys, "frozen", False):
        return []
    dirs: list[str] = []
    meipass = getattr(sys, "_MEIPASS", None)
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    # Dedicated static CLI first -- must not pick up master-shared ffmpeg.exe
    # sitting next to avcodec DLLs (NVENC 13.1).
    if meipass:
        dirs.append(os.path.join(meipass, "ffmpeg-cli"))
        dirs.append(meipass)
    dirs.append(os.path.join(exe_dir, "_internal", "ffmpeg-cli"))
    dirs.append(os.path.join(exe_dir, "_internal"))
    dirs.append(os.path.join(exe_dir, "ffmpeg-cli"))
    dirs.append(exe_dir)
    return dirs


def _env_override(stem: str) -> str | None:
    key = "SUMU_FFMPEG" if stem == "ffmpeg" else "SUMU_FFPROBE"
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return None
    if os.path.isfile(raw):
        return raw
    if os.path.isdir(raw):
        for name in _exe_names(stem):
            cand = os.path.join(raw, name)
            if os.path.isfile(cand):
                return cand
    return None


def _find(stem: str) -> str:
    override = _env_override(stem)
    if override:
        return override
    # Prefer the SDK-13.0 static CLI (frozen bundle / repo ffmpeg-cli) over PATH.
    # A PATH ffmpeg built against NVENC 13.1 fails on 13.0 drivers.
    seen: set[str] = set()
    preferred: list[str] = []
    preferred.extend(_frozen_dirs())
    cli = _repo_cli_bin()
    if cli:
        preferred.append(cli)
    for folder in preferred:
        norm = os.path.normcase(os.path.abspath(folder))
        if norm in seen:
            continue
        seen.add(norm)
        for name in _exe_names(stem):
            cand = os.path.join(folder, name)
            if os.path.isfile(cand):
                return cand
    which = shutil.which(stem)
    if which:
        return which
    shared = _repo_shared_bin()
    if shared:
        for name in _exe_names(stem):
            cand = os.path.join(shared, name)
            if os.path.isfile(cand):
                return cand
    return _exe_names(stem)[0]


def ffmpeg_bin() -> str:
    hit = _cached.get("ffmpeg")
    if hit is None:
        hit = _find("ffmpeg")
        _cached["ffmpeg"] = hit
    return hit


def ffprobe_bin() -> str:
    hit = _cached.get("ffprobe")
    if hit is None:
        hit = _find("ffprobe")
        _cached["ffprobe"] = hit
    return hit


def ffmpeg_exists() -> bool:
    path = ffmpeg_bin()
    if os.path.isfile(path):
        return True
    return shutil.which(path) is not None


def ffmpeg_subprocess_env() -> dict[str, str]:
    """PATH prefix: the directory of the resolved ffmpeg.exe only.

    The static n8.1 CLI must not inherit ``_internal`` (master avcodec DLLs).
    A shared ffmpeg.exe already sits next to its own DLLs.
    """
    env = os.environ.copy()
    exe = ffmpeg_bin()
    folder = os.path.dirname(os.path.abspath(exe))
    if folder:
        env["PATH"] = folder + os.pathsep + env.get("PATH", "")
    return env
