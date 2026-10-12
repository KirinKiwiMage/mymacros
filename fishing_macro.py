#!/usr/bin/env python3
"""
Fishing minigame macro (pynput + screen capture, X11).

When a fish bites, the screen gets a coloured tint and circles show up:

    Dark blue tint  ("timing")   - a white disc with a dark ring shrinking onto
                                   it. Click the moment the ring closes in,
                                   which is when the disc turns pure white.
    Light blue tint ("any time") - coloured discs pop up all over the screen,
                                   each with a ring of its own colour. A disc
                                   can be hit any time while its ring is
                                   shrinking, but not in the first moment
                                   before the ring starts moving, so it is
                                   clicked once the ring is part-way in.

Either version can come at any difficulty (higher = faster), so nothing here
depends on the difficulty. Both penalise clicks that miss, so the macro only
ever clicks on the centre of a disc it has found on screen.

Run with --debug to print what it sees and save marked-up screenshots of every
click (and of rounds where it finds nothing) to ~/fishing_debug/.

Hotkeys:
    Numpad 7 - start
    Numpad 8 - stop
    Ctrl+C in the terminal - quit

Requires: pynput, python-xlib and numpy   (X11 session)
    sudo apt install python3-pynput python3-xlib python3-numpy
"""

import os
import queue
import struct
import sys
import threading
import time
import zlib

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
RING_DIFF = 70           # any time: same, for the thinner approach ring
RING_SEARCH = 4          # look for the ring out to this many disc radii
MAX_DISC_R = 0.06        # fraction of screen height; caps the ring search

ANYTIME_CLICK_RATIO = 1.6  # any time: click once ring radius <= this x disc radius
                           # (rings start ~2.9x and close to 1.0x; hits counted 1.0-2.0)
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

DEBUG = "--debug" in sys.argv
DEBUG_DIR = os.path.expanduser("~/fishing_debug")

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
    Returns a list of (x, y, radius) in analysed-pixel units."""
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
            discs.append(((xs.mean() + 0.5) * CELL, (ys.mean() + 0.5) * CELL, radius))
    return discs


def ring_ratio(small, tint, x, y):
    """How far a disc's approach ring is from the disc's edge, as ring radius /
    disc radius (1.0 = closed onto the edge). Measured along 16 rays from the
    centre: each ray crosses the disc, a gap of background, then the ring.
    Rays that hit something else are outvoted by the median.
    Returns None if this doesn't look like a disc with a ring."""
    h, w = small.shape[:2]
    angles = np.linspace(0, 2 * np.pi, 16, endpoint=False)
    steps = np.arange(1, int(RING_SEARCH * MAX_DISC_R * h))
    px = (x + np.outer(np.cos(angles), steps)).astype(int)
    py = (y + np.outer(np.sin(angles), steps)).astype(int)
    inside = (px >= 0) & (px < w) & (py >= 0) & (py < h)
    samples = small[py.clip(0, h - 1), px.clip(0, w - 1)]
    coloured = (np.abs(samples - tint).sum(axis=2) > RING_DIFF) & inside

    ratios = []
    for ray, ok in zip(coloured, inside):
        gap = np.argmax(~ray)                    # first step off the disc
        if ray[gap] or not ok[gap]:
            continue                             # never left the disc / ran off screen
        ring = np.argmax(ray[gap:])
        # No separate ring past the disc = it has closed onto the edge.
        ratios.append((gap + ring + 1) / (gap + 1) if ray[gap + ring] else 1.0)
    return float(np.median(ratios)) if len(ratios) >= 4 else None


def detect(frame):
    """Return (mode, [(x, y), ...]) with centres of discs ready to click, in
    frame pixels (timing: ring has closed; any time: ring is part-way in)."""
    mode, tint = classify(frame)
    if mode is None:
        return None, []
    small = frame[::STRIDE, ::STRIDE].astype(np.int16)
    height = frame.shape[0]
    if mode == "timing":
        mask = small.min(axis=2) >= WHITE_MIN
        expected = TIMING_DISC_R * height / STRIDE
    else:
        tint = np.array(tint, dtype=np.int16)
        mask = np.abs(small - tint).sum(axis=2) > COLOR_DIFF
        expected = ANYTIME_DISC_R * height / STRIDE
    discs = find_discs(mask, expected)
    if mode == "anytime":
        discs = [d for d in discs
                 if (ratio := ring_ratio(small, tint, d[0], d[1])) is not None
                 and ratio <= ANYTIME_CLICK_RATIO]
    return mode, [(x * STRIDE, y * STRIDE) for x, y, _ in discs]


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


def save_png(path, rgb):
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b""))


debug_queue = queue.Queue()


def debug_writer():
    """Saves debug screenshots in the background so clicking isn't slowed down."""
    count = 0
    while True:
        name, frame, discs, click = debug_queue.get()
        img = frame[::2, ::2].copy()
        for x, y in discs:   # green box around every disc found
            x, y = int(x) // 2, int(y) // 2
            img[max(y - 20, 0):y + 20, max(x - 20, 0):x + 20][[0, -1]] = (0, 255, 0)
            img[max(y - 20, 0):y + 20, max(x - 20, 0):x + 20][:, [0, -1]] = (0, 255, 0)
        if click:            # red cross where we clicked
            x, y = int(click[0]) // 2, int(click[1]) // 2
            img[y, max(x - 12, 0):x + 12] = (255, 0, 0)
            img[max(y - 12, 0):y + 12, x] = (255, 0, 0)
        count += 1
        save_png(os.path.join(DEBUG_DIR, f"{count:04d}_{name}.png"), img)


def debug_shot(name, frame, discs, click=None):
    if DEBUG:
        debug_queue.put((name, frame.copy(), list(discs), click))


def macro_loop():
    with Screen() as screen:
        while not quitting.is_set():
            if not running.wait(0.1):
                continue
            tracked = []             # discs on screen: {"x", "y", "first", "clicked"}
            last_mode_time = None
            recast_done = True
            last_click = -1e9
            last_mode = "start"
            last_empty_shot = 0

            while running.is_set() and not quitting.is_set():
                frame = screen.grab()
                now = time.monotonic()
                mode, discs = detect(frame)

                if DEBUG:
                    if mode != last_mode:
                        print(f"mode: {mode}  tint: {classify(frame)[1]}  screen: "
                              f"{frame.shape[1]}x{frame.shape[0]}")
                        debug_shot(f"mode_{mode}", frame, discs)
                        last_mode = mode
                    if mode and not discs and now - last_empty_shot > 1.0:
                        debug_shot(f"{mode}_nothing_found", frame, discs)
                        last_empty_shot = now

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
                    if now - disc["clicked"] < RECLICK_AFTER:
                        continue
                    gap = CLICK_GAP - (time.monotonic() - last_click)
                    if gap > 0:
                        time.sleep(gap)
                    click_at(disc["x"], disc["y"])
                    disc["clicked"] = last_click = time.monotonic()
                    debug_shot(f"{mode}_click", frame, discs, (disc["x"], disc["y"]))
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
    if DEBUG:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        threading.Thread(target=debug_writer, daemon=True).start()
        print(f"Debug on: screenshots go to {DEBUG_DIR}")
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
