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

Setup (once, or whenever the window/camera changes):
    python3 fishing_macro.py --calibrate
        hover the mouse just above your character's head (where the "!"
        bubble pops up) and press Numpad 7. Saved to fishing_box.json.
    python3 fishing_macro.py --test
        prints what the detector sees twice a second; press Numpad 7 while
        the "!" is up to save fishing_debug_full.png / fishing_debug_box.png.

Hotkeys:
    Numpad 7 - start
    Numpad 8 - stop
    Ctrl+C in the terminal - quit

Requires: pip install pynput pillow   (X11 session, which Zorin uses by default)
"""

import json
import os
import sys
import threading
import time

from PIL import ImageChops, ImageGrab, ImageStat
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

# Box around the spot picked with --calibrate where the "!" appears. Without
# calibration it falls back to the middle of the primary monitor.
BOX_SIZE = (360, 360)    # width, height in pixels
DEFAULT_BOX = (0.40, 0.25, 0.60, 0.55)  # fractions of the primary monitor
# A pixel counts as "!"-red when red is bright and well above green and blue.
# Differences rather than fixed values so gamma/colour tweaks don't break it.
RED_MIN = 120
RED_OVER_OTHERS = 100    # skin tones only reach ~75
BITE_PIXELS = 40         # red pixels needed to count as a bite (seen: 240-690)

LENIENCY = 0.15          # extra seconds added to each wait for lag spikes
# -------------------------------------------------------------------------

mo = mouse.Controller()
running = threading.Event()
quitting = threading.Event()
_box = None


HERE = os.path.dirname(os.path.abspath(__file__))
BOX_FILE = os.path.join(HERE, "fishing_box.json")


def search_box():
    global _box
    if _box is None:
        try:
            with open(BOX_FILE) as f:
                cx, cy = json.load(f)["center"]
            w, h = BOX_SIZE
            _box = (cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2)
        except (OSError, ValueError, KeyError):
            x, y, w, h = primary_monitor()
            l, t, r, b = DEFAULT_BOX
            _box = (x + int(l * w), y + int(t * h), x + int(r * w), y + int(b * h))
    return _box


def primary_monitor():
    """(x, y, width, height) of the primary monitor. The screen grab spans
    every monitor, so the box has to be offset onto the one the game is on."""
    try:
        from Xlib.display import Display
        d = Display()
        for m in d.screen().root.xrandr_get_monitors().monitors:
            if m.primary:
                return m.x, m.y, m.width_in_pixels, m.height_in_pixels
    except Exception:
        pass
    w, h = ImageGrab.grab().size
    return 0, 0, w, h


def grab_box():
    return ImageGrab.grab(bbox=search_box()).convert("RGB")


def red_pixels(img=None):
    """Number of "!"-red pixels in the search box."""
    r, g, b = (img or grab_box()).split()
    bright = r.point(lambda v: 255 if v >= RED_MIN else 0)
    over_g = ImageChops.subtract(r, g).point(lambda v: 255 if v >= RED_OVER_OTHERS else 0)
    over_b = ImageChops.subtract(r, b).point(lambda v: 255 if v >= RED_OVER_OTHERS else 0)
    mask = ImageChops.multiply(ImageChops.multiply(bright, over_g), over_b)
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


def calibrate():
    print("[calibrate] hover the mouse just above your character's head, where the")
    print("[calibrate] \"!\" bubble pops up, then press Numpad 7 (Ctrl+C to cancel)")

    def on_key(key):
        if is_start(key):
            x, y = (int(v) for v in mouse.Controller().position)
            with open(BOX_FILE, "w") as f:
                json.dump({"center": [x, y]}, f)
            print(f"[calibrate] saved centre ({x}, {y}) to {BOX_FILE}")
            return False

    try:
        with keyboard.Listener(on_press=on_key) as listener:
            listener.join()
    except KeyboardInterrupt:
        pass


def test_detector():
    full = ImageGrab.grab()
    print(f"[test] screen grab is {full.size[0]}x{full.size[1]}, "
          f"search box {search_box()}"
          f"{'' if os.path.exists(BOX_FILE) else ' (NOT calibrated, run --calibrate)'}")
    print(f"[test] bite at >= {BITE_PIXELS} red px. Numpad 7 = save screenshots, Ctrl+C = quit")

    def on_key(key):
        if is_start(key):
            ImageGrab.grab().save(os.path.join(HERE, "fishing_debug_full.png"))
            grab_box().save(os.path.join(HERE, "fishing_debug_box.png"))
            print("[test] saved fishing_debug_full.png and fishing_debug_box.png")

    listener = keyboard.Listener(on_press=on_key)
    listener.start()
    try:
        while True:
            img = grab_box()
            n = red_pixels(img)
            bright = sum(ImageStat.Stat(img).mean) / 3
            note = "  <- grab is black, screen capture not working" if bright < 2 else ""
            print(f"[test] red px = {n:5d}  brightness = {bright:5.1f}  "
                  f"{'BITE' if n >= BITE_PIXELS else ''}{note}")
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()


def main():
    if "--calibrate" in sys.argv:
        calibrate()
        return
    if "--test" in sys.argv:
        test_detector()
        return
    if not os.path.exists(BOX_FILE):
        print("[macro] not calibrated, using the middle of the screen "
              "(run with --calibrate if bites are missed)")
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
