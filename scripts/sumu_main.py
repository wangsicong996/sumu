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
# disabled (SC_CLOSE removed) so an accidental X does not kill the player; the console
# hides with the player window on close-park (see sumu.win_console).
import sys

if __name__ == "__main__":
    if getattr(sys, "frozen", False):
        from sumu.win_console import redirect_frozen_output

        redirect_frozen_output()
    from sumu.app import main

    main()
