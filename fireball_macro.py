#!/usr/bin/env python3
"""
Fireball / Fire Pillar / charge loop macro (pynput, X11).

Cycle (timed from the recordings, ~2.2s per loop):
    1. Cast           -> R (Fire Pillar) if its cooldown is up, otherwise
                         Q (Fireball); the cast animation plays, mana drops
    2. Wait           -> ~1.1s after the cast, mana is ready to recharge
    3. Hold Shift     -> charge, mana bar refills almost instantly
    4. Wait           -> ~1.0s, then loop back to 1

Cooldowns (read off the spell list greying out in the recording):
    Fireball ~1.75s, Fire Pillar ~6.1s, each starting ~0.6s after the key
    press once the cast animation finishes. In game R stays unusable a bit
    longer than the spell list shows, so PILLAR_COOLDOWN has extra margin,
    and the loop waits briefly for Fire Pillar when it's almost ready.

Every wait has a small LENIENCY added so a lag spike doesn't make an
input land before the game is ready for it.

Hotkeys:
    Numpad 7 - start
    Numpad 8 - stop
    Ctrl+C in the terminal - quit

Background mode (keys go to one window, so you can use other windows):
    python3 fireball_macro.py --list-windows      # find the game's title
    python3 fireball_macro.py --window Sober      # any part of the title

Separate-display mode (Roblox in its own nested X server, real key presses):
    ./sober_display.sh                            # starts Xephyr + Sober, prints the display
    python3 fireball_macro.py --display :2 --window Sober

Requires: pip install pynput   (X11 session, which Zorin uses by default)
"""

import argparse
import os
import sys
import threading
import time

from pynput import keyboard
from Xlib.display import Display

from numpad_hotkeys import is_start, is_stop
from window_input import DisplayKeyboard, WindowKeyboard, find_window, list_windows

# ---- tuning -------------------------------------------------------------
FIRE_KEY = "q"
PILLAR_KEY = "r"
CHARGE_KEY = keyboard.Key.shift

FIRE_COOLDOWN = 1.75     # Fireball cooldown (from the spell list greying out)
PILLAR_COOLDOWN = 7.50   # Fire Pillar cooldown. The spell list greys out
                         # for ~6.1s, but the game ignores R for longer than
                         # that; raise this if R still gets pressed too early
PILLAR_WAIT = 1.00       # wait up to this long for Fire Pillar to come off
                         # cooldown rather than casting a Fireball instead
CAST_DELAY = 0.70        # key press -> cooldown starts (cast animation)

TAP_HOLD = 0.05          # how long Q / R is held down for a "tap"
CAST_TO_CHARGE = 1.10    # Q/R press -> Shift press (cast anim + mana drop)
CHARGE_HOLD = 0.20       # how long Shift is held while charging
CHARGE_TO_CAST = 0.85    # Shift release -> next Q/R press

LENIENCY = 0.12          # extra seconds added to each wait for lag spikes
# -------------------------------------------------------------------------

kb = keyboard.Controller()
running = threading.Event()
quitting = threading.Event()

COOLDOWNS = {FIRE_KEY: FIRE_COOLDOWN, PILLAR_KEY: PILLAR_COOLDOWN}
last_cast = {}           # key -> time.monotonic() of its last press; kept
                         # across stop/start since the game's cooldowns are


def ready_in(key):
    """Seconds until `key`'s spell is off cooldown (0 if ready now)."""
    if key not in last_cast:
        return 0.0
    ready_at = last_cast[key] + CAST_DELAY + COOLDOWNS[key] + LENIENCY
    return max(0.0, ready_at - time.monotonic())


def wait(seconds):
    """Sleep in small steps so pausing/quitting is responsive.
    Returns False if the macro was paused or quit during the wait."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if quitting.is_set() or not running.is_set():
            return False
        time.sleep(0.01)
    return True


def press_for(key, hold):
    kb.press(key)
    try:
        time.sleep(hold)
    finally:
        kb.release(key)


def release_all():
    for key in (FIRE_KEY, PILLAR_KEY, CHARGE_KEY):
        try:
            kb.release(key)
        except Exception:
            pass


def macro_loop():
    while not quitting.is_set():
        if not running.wait(timeout=0.1):
            continue

        # 1. fire pillar when it's up, fireball otherwise
        key = PILLAR_KEY if ready_in(PILLAR_KEY) <= PILLAR_WAIT else FIRE_KEY
        if not wait(ready_in(key)):
            continue
        press_for(key, TAP_HOLD)
        last_cast[key] = time.monotonic()
        if not wait(CAST_TO_CHARGE + LENIENCY):
            continue

        # 2. charge
        press_for(CHARGE_KEY, CHARGE_HOLD + LENIENCY)
        if not wait(CHARGE_TO_CAST + LENIENCY):
            continue

    release_all()


def on_press(key):
    if is_start(key) and not running.is_set():
        if isinstance(kb, DisplayKeyboard):
            title = kb.focus()
            print(f"[macro] focused {title!r}" if title
                  else f"[macro] no {kb.window_name!r} window on that display")
        running.set()
        print("[macro] started")
    elif is_stop(key) and running.is_set():
        running.clear()
        release_all()
        print("[macro] stopped")


def main():
    global kb
    parser = argparse.ArgumentParser(description="Fireball / charge loop macro")
    parser.add_argument("--window", metavar="TITLE",
                        help="send keys to the window whose title contains TITLE "
                             "instead of the focused window (with --display: "
                             "focus that window on the other display)")
    parser.add_argument("--display", metavar="NAME",
                        help="type on another X display, e.g. :2 (the nested Xephyr "
                             "sober_display.sh prints)")
    parser.add_argument("--list-windows", action="store_true",
                        help="print open windows and exit")
    args = parser.parse_args()

    def server(name):           # ":1.0" / "unix:1" -> ":1"
        return ":" + name.rsplit(":", 1)[-1].split(".")[0]

    if args.display and server(args.display) == server(os.environ.get("DISPLAY", ":0")):
        sys.exit(f"[macro] {args.display} is your own desktop, not a nested display. "
                 f"Use the one sober_display.sh printed.")
    target = Display(args.display) if args.display else None
    if args.list_windows:
        for wid, title in list_windows(target):
            print(f"0x{wid:08x}  {title}")
        return

    match = None
    if args.window and not args.display:
        match = find_window(args.window, target)
        if match is None:
            sys.exit(f"[macro] no window title contains {args.window!r} "
                     f"(try --list-windows)")

    if args.display:
        kb = DisplayKeyboard(args.display, args.window)
        print(f"[macro] typing on display {args.display}"
              + (f", window {args.window!r}" if args.window else ""))
    elif match:
        wid, title = match
        kb = WindowKeyboard(wid)
        kb.focus()
        print(f"[macro] sending keys to 0x{wid:08x} {title!r}")

    print(f"[macro] Numpad 7 = start, Numpad 8 = stop, Ctrl+C = quit "
          f"(cycle ~{TAP_HOLD + CAST_TO_CHARGE + CHARGE_HOLD + CHARGE_TO_CAST + 3 * LENIENCY:.2f}s)")
    worker = threading.Thread(target=macro_loop, daemon=True)
    worker.start()
    try:
        with keyboard.Listener(on_press=on_press) as listener:
            listener.join()
    except KeyboardInterrupt:
        print("[macro] quitting")
    finally:
        quitting.set()
        running.clear()
        worker.join(timeout=1)
        release_all()


if __name__ == "__main__":
    main()
