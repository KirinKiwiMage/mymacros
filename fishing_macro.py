#!/usr/bin/env python3
"""
Fishing minigame macro (pynput + screen capture, X11).

When a fish bites, the screen gets a coloured tint and circles show up:

    Dark blue tint  ("timing")   - a white disc with a dark ring shrinking onto
                                   it. Click the moment the ring closes in,
                                   which is when the disc turns pure white.
    Light blue tint ("any time") - coloured discs pop up all over the screen.
                                   They can be clicked at any time, so each one
                                   is clicked as soon as it appears.

Either version can come at any difficulty (higher = faster), so nothing here
depends on the difficulty. Both penalise clicks that miss, so the macro only
ever clicks on the centre of a disc it has found on screen.

Hotkeys:
    Numpad 7 - start
    Numpad 8 - stop
    Ctrl+C in the terminal - quit

Requires: pynput, python-xlib and numpy   (X11 session)
    sudo apt install python3-pynput python3-xlib python3-numpy
"""

import threading
import time

import numpy as np
from pynput import keyboard, mouse
from Xlib import X
from Xlib.display import Display

from numpad_hotkeys import is_start, is_stop

# ---- tuning -------------------------------------------------------------
# Disc sizes as a fraction of screen height (measured at 1920x1080).
TIMING_DISC_R = 44 / 1080
ANYTIME_DISC_R = 54 / 1080
DISC_R_TOLERANCE = (0.4, 1.8)   # accept blobs this many times the expected radius

WHITE_MIN = 245          # timing: every channel this bright = ring has closed
COLOR_DIFF = 110         # any time: how far a pixel's colour must be from the tint

ANYTIME_FIRST_DELAY = 0.05  # wait after a disc first appears before clicking it
RECLICK_AFTER = 0.40        # click the same spot again if the disc is still there
CLICK_GAP = 0.04            # minimum time between two clicks
MOVE_SETTLE = 0.035         # time between moving the mouse and pressing (~2 game frames)
CLICK_HOLD = 0.035          # how long the button is held down (~2 game frames)

# Re-cast the rod by clicking once after a minigame ends. Off by default -
# turn on if the game doesn't re-cast by itself.
AUTO_RECAST = False
RECAST_DELAY = 2.0          # seconds after the tint fades before re-casting

LENIENCY = 0.03             # extra seconds added to waits for lag spikes
# -------------------------------------------------------------------------

STRIDE = 2   # analyse every 2nd pixel
CELL = 4     # blobs are found on a grid of CELLxCELL analysed pixels

clicker = mouse.Controller()
running = threading.Event()
quitting = threading.Event()


def classify(frame):
    """Which minigame (if any) is on screen, from the overall tint colour.
    Returns ("timing" | "anytime" | None, tint colour)."""
    r, g, b = np.median(frame[::8, ::8].reshape(-1, 3), axis=0)
    if b > 210 and g < 80 and r < 50:
        return "timing", (r, g, b)
    if b > 200 and g > 150 and r < 100:
        return "anytime", (r, g, b)
    return None, (r, g, b)


def find_discs(mask, expected_r):
    """Find filled round blobs in a boolean mask (in analysed-pixel units).
    Thin rings don't fill grid cells, so they are ignored.
    Returns a list of (x, y) centres in analysed-pixel units."""
    h, w = mask.shape
    h, w = h - h % CELL, w - w % CELL
    cells = mask[:h, :w].reshape(h // CELL, CELL, w // CELL, CELL).mean(axis=(1, 3)) >= 0.6

    seen = np.zeros_like(cells)
    lo, hi = DISC_R_TOLERANCE
    discs = []
    for cy, cx in zip(*np.nonzero(cells)):
        if seen[cy, cx]:
            continue
        # flood fill this blob
        stack, blob = [(cy, cx)], []
        seen[cy, cx] = True
        while stack:
            y, x = stack.pop()
            blob.append((y, x))
            for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if (0 <= ny < cells.shape[0] and 0 <= nx < cells.shape[1]
                        and cells[ny, nx] and not seen[ny, nx]):
                    seen[ny, nx] = True
                    stack.append((ny, nx))

        ys, xs = np.array(blob).T
        radius = np.sqrt(len(blob) * CELL * CELL / np.pi)
        bw, bh = np.ptp(xs) + 1, np.ptp(ys) + 1
        round_enough = 0.75 < bw / bh < 1.33 and 0.6 < len(blob) / (bw * bh) < 0.92
        if lo * expected_r <= radius <= hi * expected_r and round_enough:
            discs.append(((xs.mean() + 0.5) * CELL, (ys.mean() + 0.5) * CELL))
    return discs


def detect(frame):
    """Return (mode, [(x, y), ...]) with disc centres to click, in frame pixels.
    In timing mode only discs whose ring has closed are returned."""
    mode, tint = classify(frame)
    if mode is None:
        return None, []
    small = frame[::STRIDE, ::STRIDE].astype(np.int16)
    height = frame.shape[0]
    if mode == "timing":
        mask = small.min(axis=2) >= WHITE_MIN
        expected = TIMING_DISC_R * height / STRIDE
    else:
        mask = np.abs(small - np.array(tint, dtype=np.int16)).sum(axis=2) > COLOR_DIFF
        expected = ANYTIME_DISC_R * height / STRIDE
    return mode, [(x * STRIDE, y * STRIDE) for x, y in find_discs(mask, expected)]


class Screen:
    """Grabs screenshots of the whole X screen as RGB numpy arrays."""

    def __enter__(self):
        self.display = Display()
        self.root = self.display.screen().root
        geom = self.root.get_geometry()
        self.width, self.height = geom.width, geom.height
        return self

    def grab(self):
        image = self.root.get_image(0, 0, self.width, self.height, X.ZPixmap, 0xFFFFFFFF)
        pixels = np.frombuffer(image.data, dtype=np.uint8)
        return pixels.reshape(self.height, self.width, 4)[:, :, 2::-1]   # BGRX -> RGB

    def __exit__(self, *_):
        self.display.close()


def click_at(x, y):
    # Roblox reads input once per frame, so give it a frame or two to see the
    # move and the press; an instant press+release can be missed entirely.
    clicker.position = (x + 1, y)   # tiny wiggle so the game registers the move
    clicker.position = (x, y)
    time.sleep(MOVE_SETTLE)
    clicker.press(mouse.Button.left)
    time.sleep(CLICK_HOLD)
    clicker.release(mouse.Button.left)


def macro_loop():
    with Screen() as screen:
        while not quitting.is_set():
            if not running.wait(0.1):
                continue
            tracked = []             # discs on screen: {"x", "y", "first", "clicked"}
            last_mode_time = None
            recast_done = True
            last_click = -1e9

            while running.is_set() and not quitting.is_set():
                frame = screen.grab()
                now = time.monotonic()
                mode, discs = detect(frame)

                if mode is None:
                    tracked.clear()
                    if (AUTO_RECAST and not recast_done and last_mode_time
                            and now - last_mode_time >= RECAST_DELAY + LENIENCY):
                        print("re-casting")
                        click_at(frame.shape[1] // 2, frame.shape[0] // 2)
                        recast_done = True
                    continue
                last_mode_time = now
                recast_done = False

                # Match each disc to the one seen nearest to it last frame.
                match_dist = ANYTIME_DISC_R * frame.shape[0]
                still_there = []
                for x, y in discs:
                    old = min(tracked, default=None,
                              key=lambda d: (d["x"] - x) ** 2 + (d["y"] - y) ** 2)
                    if old and (old["x"] - x) ** 2 + (old["y"] - y) ** 2 < match_dist ** 2:
                        tracked.remove(old)
                        old["x"], old["y"] = x, y
                        still_there.append(old)
                    else:
                        still_there.append({"x": x, "y": y, "first": now, "clicked": -1e9})
                tracked = still_there

                for disc in sorted(tracked, key=lambda d: d["first"]):
                    if mode == "anytime" and now - disc["first"] < ANYTIME_FIRST_DELAY:
                        continue
                    if now - disc["clicked"] < RECLICK_AFTER:
                        continue
                    gap = CLICK_GAP - (time.monotonic() - last_click)
                    if gap > 0:
                        time.sleep(gap)
                    click_at(disc["x"], disc["y"])
                    disc["clicked"] = last_click = time.monotonic()
                    print(f"{mode}: click at ({disc['x']:.0f}, {disc['y']:.0f})")

                time.sleep(0.005)


def on_press(key):
    if is_start(key) and not running.is_set():
        print("started")
        running.set()
    elif is_stop(key) and running.is_set():
        print("stopped")
        running.clear()


def main():
    print("Fishing macro ready. Numpad 7 = start, Numpad 8 = stop, Ctrl+C = quit.")
    threading.Thread(target=macro_loop, daemon=True).start()
    listener = keyboard.Listener(on_press=on_press)
    listener.start()
    try:
        while listener.is_alive():
            listener.join(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        running.clear()
        quitting.set()
        listener.stop()


if __name__ == "__main__":
    main()
