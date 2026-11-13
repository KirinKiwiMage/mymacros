"""
Startup check: are we on X11? pynput only works properly there.

Zorin OS can silently log you into a Wayland session instead of X11. On
Wayland, pynput can only see and type into old X11 (XWayland) windows, so
hotkeys and key presses don't work in most apps or the game.

Call require_x11() at the very start of every macro.
"""

import os
import subprocess
import sys


def _loginctl_type():
    """Session type according to systemd-logind ('x11', 'wayland', ...) or None."""
    session = os.environ.get("XDG_SESSION_ID")
    if not session:
        return None
    try:
        out = subprocess.run(["loginctl", "show-session", session, "-p", "Type", "--value"],
                             capture_output=True, text=True, timeout=2)
        return out.stdout.strip().lower() or None
    except (OSError, subprocess.SubprocessError):
        return None


def session_type():
    """'x11', 'wayland' or 'unknown'."""
    kinds = {os.environ.get("XDG_SESSION_TYPE", "").lower(), _loginctl_type()}
    if "wayland" in kinds or os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if "x11" in kinds and os.environ.get("DISPLAY"):
        return "x11"
    return "x11" if os.environ.get("DISPLAY") else "unknown"


def require_x11(name="macro"):
    """Print the session type; on Wayland (or no display) explain how to fix
    it and exit, unless the user chooses to continue anyway."""
    kind = session_type()
    if kind == "x11":
        print(f"[{name}] session check: X11 - OK")
        return

    if kind == "wayland":
        print(f"[{name}] WARNING: you are in a WAYLAND session, not X11.\n"
              f"  Zorin sometimes switches to Wayland on its own. On Wayland the\n"
              f"  hotkeys and key presses will NOT work outside this script's window.\n"
              f"  Fix: log out, click your name, click the gear icon in the bottom\n"
              f"  right corner, pick 'Zorin Desktop on Xorg', then log back in.")
    else:
        print(f"[{name}] WARNING: no X11 display found (DISPLAY isn't set).\n"
              f"  Run this from a terminal inside your desktop session.")

    try:
        answer = input("  Continue anyway? [y/N] ") if sys.stdin.isatty() else ""
    except EOFError:
        answer = ""
    if answer.strip().lower() not in ("y", "yes"):
        sys.exit(1)


if __name__ == "__main__":
    print(f"Session type: {session_type()}")
