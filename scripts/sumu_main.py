# SPDX-FileCopyrightText: sumu Authors
# SPDX-License-Identifier: AGPL-3.0
#
# PyInstaller entry point for the daily-use player. No sys.path hacks: the frozen build's
# spec is responsible for making `sumu` and `sumu_core` importable.
#
# The frozen bundle stays windowed (console=False in packaging/sumu.spec) so Explorer
# double-click does not spawn a *parent* cmd. Like Blender, we AllocConsole() ourselves
# at startup so a system console sits beside the player and prints warmup / TRT compile
# errors live. The same stream is teed to <exe dir>/sumu.log. Closing the console is
# disabled (SC_CLOSE removed) so an accidental X does not kill the player.
import sys


class _Tee:
    """Write to several text streams (console + log file). Never raises."""

    encoding = "utf-8"
    errors = "replace"

    def __init__(self, *streams):
        self._streams = [s for s in streams if s is not None]

    def write(self, data):
        for s in self._streams:
            try:
                s.write(data)
                s.flush()
            except Exception:
                pass
        return len(data) if data else 0

    def flush(self):
        for s in self._streams:
            try:
                s.flush()
            except Exception:
                pass

    def isatty(self):
        return True

    def fileno(self):
        for s in self._streams:
            fn = getattr(s, "fileno", None)
            if fn is None:
                continue
            try:
                return fn()
            except Exception:
                continue
        raise OSError("tee has no fileno")


_CONSOLE_CTRL_HANDLER = None  # keep a ctypes callback alive


def _alloc_windows_console():
    """Create a visible console for this GUI-subsystem process. Returns CONOUT$ or None."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    kernel32.AllocConsole.restype = wintypes.BOOL
    kernel32.GetConsoleWindow.restype = wintypes.HWND
    kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel32.GetStdHandle.restype = wintypes.HANDLE
    kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetConsoleMode.restype = wintypes.BOOL
    kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.SetConsoleMode.restype = wintypes.BOOL
    kernel32.SetConsoleTitleW.argtypes = [wintypes.LPCWSTR]
    kernel32.SetConsoleTitleW.restype = wintypes.BOOL
    kernel32.SetConsoleOutputCP.argtypes = [wintypes.UINT]
    kernel32.SetConsoleOutputCP.restype = wintypes.BOOL
    kernel32.SetConsoleCP.argtypes = [wintypes.UINT]
    kernel32.SetConsoleCP.restype = wintypes.BOOL

    user32.GetSystemMenu.argtypes = [wintypes.HWND, wintypes.BOOL]
    user32.GetSystemMenu.restype = wintypes.HMENU
    user32.DeleteMenu.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.UINT]
    user32.DeleteMenu.restype = wintypes.BOOL
    user32.DrawMenuBar.argtypes = [wintypes.HWND]
    user32.DrawMenuBar.restype = wintypes.BOOL

    if not kernel32.AllocConsole():
        # Launched from an existing console, or AllocConsole failed. Still try CONOUT$.
        pass

    kernel32.SetConsoleTitleW("sumu")
    kernel32.SetConsoleOutputCP(65001)  # UTF-8 so Chinese TRT/UI messages render
    kernel32.SetConsoleCP(65001)

    # Quick Edit: a click in the console pauses the process until Enter -- looks like a hang
    # during TRT compile. Keep ENABLE_EXTENDED_FLAGS so clearing Quick Edit sticks.
    STD_INPUT_HANDLE = wintypes.DWORD(-10 & 0xFFFFFFFF)
    ENABLE_QUICK_EDIT = 0x0040
    ENABLE_EXTENDED_FLAGS = 0x0080
    hin = kernel32.GetStdHandle(STD_INPUT_HANDLE)
    mode = wintypes.DWORD(0)
    if hin and kernel32.GetConsoleMode(hin, ctypes.byref(mode)):
        kernel32.SetConsoleMode(hin, (mode.value | ENABLE_EXTENDED_FLAGS) & ~ENABLE_QUICK_EDIT)

    hwnd = kernel32.GetConsoleWindow()
    if hwnd:
        MF_BYCOMMAND = 0x00000000
        SC_CLOSE = 0xF060
        menu = user32.GetSystemMenu(hwnd, False)
        if menu:
            user32.DeleteMenu(menu, SC_CLOSE, MF_BYCOMMAND)
            user32.DrawMenuBar(hwnd)

    # Ctrl+C / console-close must not terminate the player (close button already removed;
    # this covers Ctrl+C and any remaining close path).
    CTRL_C_EVENT = 0
    CTRL_BREAK_EVENT = 1
    CTRL_CLOSE_EVENT = 2
    HandlerRoutine = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

    def _on_ctrl(event):
        if event in (CTRL_C_EVENT, CTRL_BREAK_EVENT, CTRL_CLOSE_EVENT):
            return True
        return False

    global _CONSOLE_CTRL_HANDLER
    _CONSOLE_CTRL_HANDLER = HandlerRoutine(_on_ctrl)
    kernel32.SetConsoleCtrlHandler(_CONSOLE_CTRL_HANDLER, True)

    # CONOUT$ is the console's output buffer; encoding=utf-8 matches SetConsoleOutputCP.
    return open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)


def _redirect_frozen_output():
    if not getattr(sys, "frozen", False):
        return  # dev runs keep the real console
    try:
        import os

        log_dir = os.path.dirname(sys.executable)
        log_path = os.path.join(log_dir, "sumu.log")
        log_file = open(log_path, "w", buffering=1, encoding="utf-8", errors="replace")
    except Exception:
        log_file = None

    console = None
    try:
        console = _alloc_windows_console()
    except Exception:
        console = None

    tee = _Tee(console, log_file)
    sys.stdout = tee
    sys.stderr = tee
    try:
        print("sumu console  (also writing sumu.log next to sumu.exe)", flush=True)
        print("Close button disabled — closing this window would otherwise kill the player.",
              flush=True)
        print("-" * 60, flush=True)
    except Exception:
        pass


if __name__ == "__main__":
    _redirect_frozen_output()
    from sumu.app import main

    main()
