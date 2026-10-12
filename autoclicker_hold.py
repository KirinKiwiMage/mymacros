#!/usr/bin/env python3
"""
Hold-to-click auto clicker with a GUI (pynput + tkinter, X11).

Type the delay between clicks in milliseconds, then HOLD the front side
button on your mouse (the "forward" button, X button 9) to spam left click
at the current mouse position. Let go and it stops.
Closing the window or Ctrl+C in the terminal quits.

The side button press is grabbed through X11 while this runs, so other
windows don't get it (e.g. a browser won't go "forward").

Requires: pip install pynput python-xlib   (tkinter: sudo apt install python3-tk)
"""

import threading
import time
import tkinter as tk

from pynput import mouse
from Xlib import X, error
from Xlib.display import Display

HOLD_BUTTON = 9  # front side button; use 8 for the back side button
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


def grab_hold_button(on_press):
    """Grab the side button on the X server so other windows never see its
    press, and call on_press() when it goes down.
    Returns False if another program already grabbed the button."""
    display = Display()
    root = display.screen().root

    # A grab only matches the exact modifier state, so also grab it with
    # NumLock (Mod2) and CapsLock (Lock) on.
    catch = error.CatchError(error.BadAccess)
    for mods in (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask):
        root.grab_button(HOLD_BUTTON, mods, False, X.ButtonPressMask,
                         X.GrabModeAsync, X.GrabModeAsync, X.NONE, X.NONE,
                         onerror=catch)
    display.sync()
    if catch.get_error():
        root.ungrab_button(HOLD_BUTTON, X.AnyModifier)
        display.close()
        return False

    def loop():
        while True:
            event = display.next_event()
            if event.type == X.ButtonPress and event.detail == HOLD_BUTTON:
                # The press activates a pointer grab that would send our own
                # clicks to this program instead of the game, so let go of it
                # straight away. The release is caught by the pynput listener.
                display.ungrab_pointer(X.CurrentTime)
                display.sync()
                on_press()

    threading.Thread(target=loop, daemon=True).start()
    return True


def main():
    root = tk.Tk()
    root.title("Hold Clicker")
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

    status = tk.Label(frame, text="hold side btn", width=11, fg="white", bg="#a33")
    status.grid(row=1, column=0, columnspan=2, pady=(5, 0), sticky="ew")

    def apply_rate(*_):
        global interval
        try:
            value = max(MIN_MS, int(ms_var.get()))
        except ValueError:
            return
        interval = value / 1000

    ms_var.trace_add("write", apply_rate)

    def start():
        apply_rate()
        running.set()

    def on_click(x, y, button, pressed):
        if getattr(button, "value", None) != HOLD_BUTTON:
            return
        if pressed:
            if not grabbed:
                start()
        else:
            running.clear()

    # Repaint the status from the running flag (and let Ctrl+C in the
    # terminal reach Python while tk's mainloop runs).
    def refresh():
        if running.is_set():
            status.config(text="CLICKING", bg="#3a3")
        else:
            status.config(text="hold side btn", bg="#a33")
        root.after(50, refresh)
    refresh()

    def quit_app(*_):
        running.clear()
        quitting.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", quit_app)

    threading.Thread(target=click_loop, daemon=True).start()
    grabbed = grab_hold_button(start)
    if not grabbed:
        print("Couldn't grab the side button - another program is using it. "
              "It will still work, but other windows will see it too.")
    listener = mouse.Listener(on_click=on_click)
    listener.daemon = True
    listener.start()

    try:
        root.mainloop()
    except KeyboardInterrupt:
        quit_app()
    finally:
        running.clear()
        quitting.set()
        listener.stop()


if __name__ == "__main__":
    main()
