#!/usr/bin/env python3
"""
Key spammer with a GUI (pynput + tkinter, X11).

Spams one key (Shift by default) over and over while running.

GUI:
    - Key: pick from the dropdown, or click "Press a key..." and then hit
      the key you want on your keyboard.
    - Speed: presses per second slider (the interval in ms is shown).
    - Start / Stop buttons.

Hotkeys (work even when the window isn't focused):
    Numpad 7 - start
    Numpad 8 - stop
    Close the window or Ctrl+C in the terminal - quit

Requires: pip install pynput python-xlib   (tkinter: sudo apt install python3-tk)
"""

import signal
import string
import threading
import time
import tkinter as tk
from tkinter import ttk

from pynput import keyboard

from numpad_hotkeys import is_start, is_stop

# ---- defaults -----------------------------------------------------------
DEFAULT_KEY = "shift"
DEFAULT_PER_SECOND = 10.0
MIN_PER_SECOND = 1.0
MAX_PER_SECOND = 30.0

TAP_HOLD = 0.02          # how long the key is held for each press
LENIENCY = 0.01          # extra hold so a lag spike doesn't drop the press
# -------------------------------------------------------------------------

# Keys offered in the dropdown. Special keys use pynput's Key names.
SPECIAL_KEYS = [
    "shift", "shift_r", "ctrl", "ctrl_r", "alt", "alt_r", "space", "enter",
    "tab", "backspace", "esc", "caps_lock", "up", "down", "left", "right",
] + [f"f{i}" for i in range(1, 13)]
KEY_CHOICES = SPECIAL_KEYS + list(string.ascii_lowercase) + list(string.digits)


def key_from_name(name):
    """'shift' -> Key.shift, 'q' -> KeyCode('q'), 'vk:65' -> KeyCode(vk=65)."""
    if name.startswith("vk:"):
        return keyboard.KeyCode.from_vk(int(name[3:]))
    if name in keyboard.Key.__members__:
        return keyboard.Key[name]
    return keyboard.KeyCode.from_char(name)


def name_from_key(key):
    if isinstance(key, keyboard.Key):
        return key.name
    if key.char:
        return key.char.lower()
    return f"vk:{key.vk}"


class Spammer:
    """Background worker. Settings are plain attributes so the worker thread
    never touches tkinter."""

    def __init__(self):
        self.kb = keyboard.Controller()
        self.key = key_from_name(DEFAULT_KEY)
        self.interval = 1.0 / DEFAULT_PER_SECOND
        self.running = threading.Event()
        self.quitting = threading.Event()
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def set_key(self, key):
        with self.lock:
            self.release()          # don't leave the old key stuck down
            self.key = key

    def start(self):
        self.running.set()

    def stop(self):
        self.running.clear()
        with self.lock:
            self.release()

    def release(self):
        try:
            self.kb.release(self.key)
        except Exception:
            pass

    def quit(self):
        self.quitting.set()
        self.stop()
        self.thread.join(timeout=1)
        self.release()

    def _loop(self):
        while not self.quitting.is_set():
            if not self.running.wait(timeout=0.1):
                continue
            start = time.monotonic()
            interval = self.interval
            # hold never takes up more than half the interval
            hold = min(TAP_HOLD + LENIENCY, interval / 2)
            with self.lock:
                self.kb.press(self.key)
                time.sleep(hold)
                self.kb.release(self.key)
            # wait out the rest of the interval in small steps so stopping
            # is instant even at slow speeds
            end = start + interval
            while time.monotonic() < end:
                if self.quitting.is_set() or not self.running.is_set():
                    break
                time.sleep(min(0.01, max(0.0, end - time.monotonic())))


class App:
    def __init__(self, root):
        self.root = root
        self.spammer = Spammer()
        self.capturing = False      # True while waiting for "Press a key..."
        self.captured = None        # key name handed from listener -> GUI

        root.title("Key Spammer")
        root.resizable(False, False)
        frame = ttk.Frame(root, padding=12)
        frame.grid()

        # key picker
        ttk.Label(frame, text="Key to spam:").grid(row=0, column=0, sticky="w")
        self.key_var = tk.StringVar(value=DEFAULT_KEY)
        self.key_box = ttk.Combobox(frame, textvariable=self.key_var,
                                    values=KEY_CHOICES, state="readonly", width=12)
        self.key_box.grid(row=0, column=1, padx=6, pady=4)
        self.key_box.bind("<<ComboboxSelected>>", lambda e: self.apply_key())
        self.bind_btn = ttk.Button(frame, text="Press a key...", command=self.begin_capture)
        self.bind_btn.grid(row=0, column=2, pady=4)

        # speed
        ttk.Label(frame, text="Presses / sec:").grid(row=1, column=0, sticky="w")
        self.speed_var = tk.DoubleVar(value=DEFAULT_PER_SECOND)
        ttk.Scale(frame, from_=MIN_PER_SECOND, to=MAX_PER_SECOND,
                  variable=self.speed_var, length=160,
                  command=lambda v: self.apply_speed()).grid(row=1, column=1, padx=6, pady=4)
        self.speed_spin = ttk.Spinbox(frame, from_=MIN_PER_SECOND, to=MAX_PER_SECOND,
                                      increment=0.5, textvariable=self.speed_var, width=6,
                                      command=self.apply_speed)
        self.speed_spin.grid(row=1, column=2, pady=4)
        self.speed_spin.bind("<Return>", lambda e: self.apply_speed())
        self.speed_spin.bind("<FocusOut>", lambda e: self.apply_speed())
        self.interval_lbl = ttk.Label(frame, text="")
        self.interval_lbl.grid(row=2, column=1, columnspan=2, sticky="w", padx=6)

        # start / stop
        btns = ttk.Frame(frame)
        btns.grid(row=3, column=0, columnspan=3, pady=(10, 4))
        ttk.Button(btns, text="Start (Numpad 7)", command=self.start).grid(row=0, column=0, padx=4)
        ttk.Button(btns, text="Stop (Numpad 8)", command=self.stop).grid(row=0, column=1, padx=4)

        self.status_lbl = ttk.Label(frame, text="", font=("TkDefaultFont", 11, "bold"))
        self.status_lbl.grid(row=4, column=0, columnspan=3, pady=(6, 0))

        self.apply_speed()

        # global hotkeys + key capture
        self.listener = keyboard.Listener(on_press=self.on_press)
        self.listener.start()

        root.protocol("WM_DELETE_WINDOW", self.quit)
        self.poll()

    # ---- settings ----------------------------------------------------------
    def apply_key(self):
        self.spammer.set_key(key_from_name(self.key_var.get()))

    def apply_speed(self):
        try:
            per_sec = float(self.speed_var.get())
        except (tk.TclError, ValueError):
            per_sec = DEFAULT_PER_SECOND
        per_sec = max(MIN_PER_SECOND, min(MAX_PER_SECOND, round(per_sec * 2) / 2))
        self.speed_var.set(per_sec)
        self.spammer.interval = 1.0 / per_sec
        self.interval_lbl.config(text=f"= one press every {1000 / per_sec:.0f} ms")

    def begin_capture(self):
        self.stop()
        self.capturing = True
        self.bind_btn.config(text="Waiting...")

    # ---- control -------------------------------------------------------------
    def start(self):
        if not self.capturing:
            self.spammer.start()

    def stop(self):
        self.spammer.stop()

    def on_press(self, key):
        # runs on the pynput thread: only set flags, the GUI picks them up in poll()
        if self.capturing:
            self.captured = name_from_key(key)
            self.capturing = False
        elif is_start(key):
            self.spammer.start()
        elif is_stop(key):
            self.spammer.stop()

    def poll(self):
        if self.captured is not None:
            name, self.captured = self.captured, None
            self.key_var.set(name)
            self.apply_key()
            self.bind_btn.config(text="Press a key...")
        if self.capturing:
            text = "Press the key you want to spam..."
        elif self.spammer.running.is_set():
            text = f"RUNNING - spamming {self.key_var.get()}"
        else:
            text = "Stopped"
        self.status_lbl.config(text=text)
        self.root.after(50, self.poll)

    def quit(self):
        self.listener.stop()
        self.spammer.quit()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = App(root)
    # tkinter swallows KeyboardInterrupt inside callbacks, so handle Ctrl+C
    # with a signal handler (poll() keeps Python code running so it fires)
    signal.signal(signal.SIGINT, lambda *_: root.after(0, app.quit))
    print("[spammer] Numpad 7 = start, Numpad 8 = stop, close window or Ctrl+C = quit")
    root.mainloop()


if __name__ == "__main__":
    main()
