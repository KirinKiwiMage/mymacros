#!/usr/bin/env python3
"""
Roblox fishing macro (pynput, X11).

From the recording, one catch goes:
    1. Click            -> cast the line (rod must already be equipped)
    2. Wait             -> RANDOM time until a bite, no fixed timer
    3. Bite             -> a white bubble with a red "!" pops up above the
                           character and stays ~2s
    4. Click            -> reel in while the "!" is showing
    5. Wait ~2.5s       -> catch animation, then back to 1 (recast)

The bite is detected by looking for the red "!" in a box above the middle
of the screen. That bubble is GUI, not lit by the world, so it looks the same
at day and night (the scene drops to ~55% brightness at night and nothing
else in that box turns that shade of red). If no bite shows up within
NO_BITE_TIMEOUT the line is reeled in and recast, so a lost/despawned
line never stalls the loop.

Run with --test to print how many red pixels the detector sees, so you can
check the box/threshold on your screen before letting it fish.

Hotkeys:
    Numpad 7 - start
    Numpad 8 - stop
    Ctrl+C in the terminal - quit

Requires: pip install pynput pillow   (X11 session, which Zorin uses by default)
"""

import sys
import threading
import time

from PIL import ImageChops, ImageGrab
from pynput import keyboard, mouse

from numpad_hotkeys import is_start, is_stop

# ---- tuning -------------------------------------------------------------
CAST_BUTTON = mouse.Button.left

CLICK_HOLD = 0.05        # how long the mouse is held for a click
CAST_SETTLE = 2.0        # after casting, ignore the screen while the line flies out
NO_BITE_TIMEOUT = 90.0   # no "!" for this long -> reel in and recast
REEL_CLICK_GAP = 0.15    # time between reel clicks while the "!" is up
REEL_MAX = 8.0           # stop reeling after this long even if "!" is still seen
CATCH_TO_CAST = 2.5      # "!" gone -> next cast (catch animation)
POLL = 0.05              # how often the screen is checked for the "!"

# Box (fractions of the screen) above the character where the "!" appears.
# In the 1920x1080 recording the red "!" sits around x 950-966, y 400-465.
SEARCH_BOX = (0.40, 0.25, 0.60, 0.55)   # left, top, right, bottom
RED_MIN = 150            # "!" red channel is at least this
OTHER_MAX = 90           # ...and green/blue at most this
BITE_PIXELS = 60         # red pixels needed to count as a bite (seen: 240-690)

LENIENCY = 0.15          # extra seconds added to each wait for lag spikes
# -------------------------------------------------------------------------

mo = mouse.Controller()
running = threading.Event()
quitting = threading.Event()
_box = None


def search_box():
    global _box
    if _box is None:
        w, h = ImageGrab.grab().size
        l, t, r, b = SEARCH_BOX
        _box = (int(l * w), int(t * h), int(r * w), int(b * h))
    return _box


def red_pixels():
    """Number of "!"-red pixels in the search box."""
    r, g, b = ImageGrab.grab(bbox=search_box()).convert("RGB").split()
    r = r.point(lambda v: 255 if v >= RED_MIN else 0)
    g = g.point(lambda v: 255 if v <= OTHER_MAX else 0)
    b = b.point(lambda v: 255 if v <= OTHER_MAX else 0)
    mask = ImageChops.multiply(ImageChops.multiply(r, g), b)
    return mask.histogram()[255]


def bite_visible():
    return red_pixels() >= BITE_PIXELS


def wait(seconds):
    """Sleep in small steps so pausing/quitting is responsive.
    Returns False if the macro was paused or quit during the wait."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if quitting.is_set() or not running.is_set():
            return False
        time.sleep(0.01)
    return True


def click():
    mo.press(CAST_BUTTON)
    try:
        time.sleep(CLICK_HOLD)
    finally:
        mo.release(CAST_BUTTON)


def release_all():
    try:
        mo.release(CAST_BUTTON)
    except Exception:
        pass


def wait_for_bite():
    """True once the "!" shows, False on timeout or stop."""
    end = time.monotonic() + NO_BITE_TIMEOUT
    while time.monotonic() < end:
        if not wait(POLL):
            return False
        if bite_visible():
            return True
    return False


def reel():
    """Click until the "!" goes away. False if stopped."""
    end = time.monotonic() + REEL_MAX + LENIENCY
    while time.monotonic() < end:
        if not running.is_set() or quitting.is_set():
            return False
        click()
        if not wait(REEL_CLICK_GAP):
            return False
        if not bite_visible():
            return True
    return True


def macro_loop():
    while not quitting.is_set():
        if not running.wait(timeout=0.1):
            continue

        # 1. cast
        click()
        if not wait(CAST_SETTLE + LENIENCY):
            continue

        # 2. wait for a random-length bite
        if not wait_for_bite():
            if running.is_set():
                print("[macro] no bite, recasting")
                click()                      # reel the empty line in
                wait(CATCH_TO_CAST + LENIENCY)
            continue

        # 3. reel in
        print("[macro] bite!")
        if not reel():
            continue

        # 4. let the catch animation finish, then recast
        wait(CATCH_TO_CAST + LENIENCY)

    release_all()


def on_press(key):
    if is_start(key) and not running.is_set():
        running.set()
        print("[macro] started")
    elif is_stop(key) and running.is_set():
        running.clear()
        release_all()
        print("[macro] stopped")


def test_detector():
    print(f"[test] search box {search_box()}, bite at >= {BITE_PIXELS} red px. Ctrl+C to quit")
    try:
        while True:
            n = red_pixels()
            print(f"[test] red px = {n:5d}  {'BITE' if n >= BITE_PIXELS else ''}")
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass


def main():
    if "--test" in sys.argv:
        test_detector()
        return
    print("[macro] Numpad 7 = start, Numpad 8 = stop, Ctrl+C = quit "
          "(equip the rod and point the mouse at the water first)")
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
