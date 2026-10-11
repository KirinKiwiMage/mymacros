#!/usr/bin/env python3
"""
Fireball / charge loop macro (pynput, X11).

Cycle (timed from the recording, ~2.2s per loop):
    1. Tap Q          -> fireball cast animation plays (~0.6s), mana drops
    2. Wait           -> ~1.1s after the cast, mana is ready to recharge
    3. Hold Shift     -> charge, mana bar refills almost instantly
    4. Wait           -> ~1.0s, then loop back to 1

Every wait has a small LENIENCY added so a lag spike doesn't make an
input land before the game is ready for it.

Hotkeys:
    Numpad 7 - start
    Numpad 8 - stop
    Ctrl+C in the terminal - quit

Requires: pip install pynput   (X11 session, which Zorin uses by default)
"""

import threading
import time

from pynput import keyboard

from numpad_hotkeys import is_start, is_stop

# ---- tuning -------------------------------------------------------------
FIRE_KEY = "q"
CHARGE_KEY = keyboard.Key.shift

TAP_HOLD = 0.05          # how long Q is held down for a "tap"
CAST_TO_CHARGE = 1.10    # Q press -> Shift press (cast anim + mana drop)
CHARGE_HOLD = 0.20       # how long Shift is held while charging
CHARGE_TO_CAST = 0.85    # Shift release -> next Q press

LENIENCY = 0.12          # extra seconds added to each wait for lag spikes
# -------------------------------------------------------------------------

kb = keyboard.Controller()
running = threading.Event()
quitting = threading.Event()


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
    for key in (FIRE_KEY, CHARGE_KEY):
        try:
            kb.release(key)
        except Exception:
            pass


def macro_loop():
    while not quitting.is_set():
        if not running.wait(timeout=0.1):
            continue

        # 1. fireball
        press_for(FIRE_KEY, TAP_HOLD)
        if not wait(CAST_TO_CHARGE + LENIENCY):
            continue

        # 2. charge
        press_for(CHARGE_KEY, CHARGE_HOLD + LENIENCY)
        if not wait(CHARGE_TO_CAST + LENIENCY):
            continue

    release_all()


def on_press(key):
    if is_start(key) and not running.is_set():
        running.set()
        print("[macro] started")
    elif is_stop(key) and running.is_set():
        running.clear()
        release_all()
        print("[macro] stopped")


def main():
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
