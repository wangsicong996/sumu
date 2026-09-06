# SPDX-FileCopyrightText: sumu Authors
# SPDX-License-Identifier: AGPL-3.0
"""Resolve ``ffmpeg.exe`` / ``ffprobe.exe`` for export, webstream, and thumbnails.

Frozen onedir stages both next to the FFmpeg shared DLLs in ``_internal``
(``scripts/build_dist.ps1``). Daily playback does not need them. Dev falls back
to PATH, then the spike0 BtbN tree used by the native build.
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


def _frozen_dirs() -> list[str]:
    if not getattr(sys, "frozen", False):
        return []
    dirs: list[str] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(meipass)
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    dirs.append(os.path.join(exe_dir, "_internal"))
    dirs.append(exe_dir)
    return dirs


def _dev_ffmpeg_bin() -> str | None:
    here = os.path.dirname(os.path.abspath(__file__))
    spike = os.path.normpath(os.path.join(
        here, "..", "..",
        "spikes", "spike0_d3d11_present", "third_party", "ffmpeg", "bin",
    ))
    return spike if os.path.isdir(spike) else None


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


def _search_folders(stem: str) -> list[str]:
    folders: list[str] = []
    folders.extend(_frozen_dirs())
    sibling_stem = "ffprobe" if stem == "ffmpeg" else "ffmpeg"
    sibling = shutil.which(sibling_stem)
    if sibling:
        folders.append(os.path.dirname(sibling))
    dev = _dev_ffmpeg_bin()
    if dev:
        folders.append(dev)
    return folders


def _find(stem: str) -> str:
    override = _env_override(stem)
    if override:
        return override
    # Frozen: prefer the bundled copy so a PATH ffmpeg without NVENC cannot win.
    if getattr(sys, "frozen", False):
        for folder in _frozen_dirs():
            for name in _exe_names(stem):
                cand = os.path.join(folder, name)
                if os.path.isfile(cand):
                    return cand
    which = shutil.which(stem)
    if which:
        return which
    seen: set[str] = set()
    for folder in _search_folders(stem):
        norm = os.path.normcase(os.path.abspath(folder))
        if norm in seen:
            continue
        seen.add(norm)
        for name in _exe_names(stem):
            cand = os.path.join(folder, name)
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
    """PATH prefix so a spawned ffmpeg.exe finds the matching shared DLLs."""
    env = os.environ.copy()
    extra: list[str] = []
    exe = ffmpeg_bin()
    folder = os.path.dirname(os.path.abspath(exe))
    if folder:
        extra.append(folder)
    for folder in _frozen_dirs():
        extra.append(folder)
    seen: set[str] = set()
    prefix: list[str] = []
    for folder in extra:
        if not folder:
            continue
        norm = os.path.normcase(os.path.abspath(folder))
        if norm in seen:
            continue
        seen.add(norm)
        prefix.append(folder)
    if prefix:
        env["PATH"] = os.pathsep.join(prefix) + os.pathsep + env.get("PATH", "")
    return env
