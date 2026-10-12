#!/usr/bin/env python3
"""
Small auto clicker with a GUI (pynput + tkinter, X11).

Type the delay between clicks in milliseconds, then press ` (backtick)
anywhere to start / stop clicking at the current mouse position.
Closing the window or Ctrl+C in the terminal quits.

The ` key is grabbed through X11 while this runs, so it's swallowed and never
reaches other windows (e.g. it won't open Roblox's inventory). Shift+` (~)
still types normally.

Requires: pip install pynput python-xlib   (tkinter: sudo apt install python3-tk)
"""

import threading
import time
import tkinter as tk

from pynput import mouse
from Xlib import X, XK, error
from Xlib.display import Display

TOGGLE_KEYSYM = "grave"  # the ` key
DEFAULT_MS = 100
MIN_MS = 1

clicker = mouse.Controller()
running = threading.Event()
quitting = threading.Event()
interval = DEFAULT_MS / 1000


def click_loop():
    while not quitting.is_set():
        if not running.wait(0.05):
            continue
        # Schedule clicks against a fixed timeline so the rate doesn't drift,
        # but never try to "catch up" with a burst after a lag spike.
        next_time = time.monotonic()
        while running.is_set() and not quitting.is_set():
            clicker.click(mouse.Button.left)
            next_time += interval
            delay = next_time - time.monotonic()
            if delay > 0:
                running_wait(delay)
            else:
                next_time = time.monotonic()


def running_wait(seconds):
    """Sleep, but wake early if clicking is stopped."""
    end = time.monotonic() + seconds
    while running.is_set():
        left = end - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(left, 0.02))


def grab_toggle_key(on_toggle):
    """Grab the toggle key on the X server so other windows never see it, and
    call on_toggle() once per physical press (auto-repeat is ignored).
    Returns False if another program already grabbed the key."""
    display = Display()
    root = display.screen().root
    keycode = display.keysym_to_keycode(XK.string_to_keysym(TOGGLE_KEYSYM))

    # A grab only matches the exact modifier state, so also grab it with
    # NumLock (Mod2) and CapsLock (Lock) on.
    catch = error.CatchError(error.BadAccess)
    for mods in (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask):
        root.grab_key(keycode, mods, True, X.GrabModeAsync, X.GrabModeAsync,
                      onerror=catch)
    display.sync()
    if catch.get_error():
        root.ungrab_key(keycode, X.AnyModifier)
        display.close()
        return False

    def loop():
        held = False
        while True:
            event = display.next_event()
            if event.type == X.KeyRelease:
                # X auto-repeat sends a release immediately followed by a press
                # with the same timestamp; skip those so holding ` doesn't spam.
                if display.pending_events():
                    nxt = display.next_event()
                    if (nxt.type == X.KeyPress and nxt.time == event.time
                            and nxt.detail == event.detail):
                        continue
                    held = False
                    event = nxt
                else:
                    held = False
                    continue
            if event.type == X.KeyPress and not held:
                held = True
                on_toggle()

    threading.Thread(target=loop, daemon=True).start()
    return True


def main():
    root = tk.Tk()
    root.title("Clicker")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    frame = tk.Frame(root, padx=6, pady=6)
    frame.pack()

    tk.Label(frame, text="ms").grid(row=0, column=1, padx=(3, 0))
    ms_var = tk.StringVar(value=str(DEFAULT_MS))
    entry = tk.Entry(frame, textvariable=ms_var, width=7, justify="right",
                     validate="key",
                     validatecommand=(root.register(lambda p: p == "" or p.isdigit()), "%P"))
    entry.grid(row=0, column=0)

    status = tk.Label(frame, text="OFF  ( ` )", width=10, fg="white", bg="#a33")
    status.grid(row=1, column=0, columnspan=2, pady=(5, 0), sticky="ew")

    def apply_rate(*_):
        global interval
        try:
            value = max(MIN_MS, int(ms_var.get()))
        except ValueError:
            return
        interval = value / 1000

    ms_var.trace_add("write", apply_rate)

    def refresh():
        if running.is_set():
            status.config(text="ON  ( ` )", bg="#3a3")
            entry.config(state="disabled")
        else:
            status.config(text="OFF  ( ` )", bg="#a33")
            entry.config(state="normal")

    def toggle():
        if running.is_set():
            running.clear()
        else:
            apply_rate()
            running.set()
        refresh()

    def on_toggle():
        root.after(0, toggle)  # touch tkinter only from its own thread

    def quit_app(*_):
        running.clear()
        quitting.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", quit_app)

    threading.Thread(target=click_loop, daemon=True).start()
    if not grab_toggle_key(on_toggle):
        print("Couldn't grab ` - another program is already using it as a hotkey.")
        root.destroy()
        return

    # Let Ctrl+C in the terminal reach Python while tk's mainloop runs.
    def poll():
        root.after(200, poll)
    poll()

    try:
        root.mainloop()
    except KeyboardInterrupt:
        quit_app()
    finally:
        running.clear()
        quitting.set()


if __name__ == "__main__":
    main()
