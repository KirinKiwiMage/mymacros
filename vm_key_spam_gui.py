#!/usr/bin/env python3
"""
Key spam macro for the Sober VM, with a small window to pick the key.

Same as vm_q_spam.py (keys go straight into the VM through QEMU's control
socket, so it works while you play on the host), but you choose the key:
click "Set key", then press the key you want spammed.

Hotkeys (pressed on the host, any window):
    Numpad 7 - start
    Numpad 8 - stop
The Start/Stop button does the same. Closing the window quits.

Usage:
    python3 vm_key_spam_gui.py

Requires: pip install pynput, plus Tk: sudo apt install python3-tk
"""

import json
import os
import threading
import time
import tkinter as tk
from tkinter import ttk

from pynput import keyboard

from numpad_hotkeys import is_start, is_stop
from vm_q_spam import LENIENCY, VM_ROOT, VMKeyboard

SETTINGS = os.path.expanduser("~/.config/sober-vm-key-spam.json")
DEFAULTS = {"key": "q", "label": "Q", "hold_ms": 50, "gap_ms": 100, "vm": "alt"}

# Tk keysym -> QEMU key name ("qcode") for keys that aren't spelled the same.
# Letters and digits map to themselves (lowercased).
QCODES = {
    "space": "spc", "Return": "ret", "KP_Enter": "kp_enter", "Tab": "tab",
    "Escape": "esc", "BackSpace": "backspace", "Delete": "delete",
    "Insert": "insert", "Home": "home", "End": "end", "Prior": "pgup",
    "Next": "pgdn", "Up": "up", "Down": "down", "Left": "left", "Right": "right",
    "Shift_L": "shift", "Shift_R": "shift_r", "Control_L": "ctrl",
    "Control_R": "ctrl_r", "Alt_L": "alt", "Alt_R": "alt_r",
    "Caps_Lock": "caps_lock", "minus": "minus", "equal": "equal",
    "bracketleft": "bracket_left", "bracketright": "bracket_right",
    "semicolon": "semicolon", "apostrophe": "apostrophe", "grave": "grave_accent",
    "backslash": "backslash", "comma": "comma", "period": "dot", "slash": "slash",
    **{f"F{i}": f"f{i}" for i in range(1, 13)},
}
# Shifted symbols report a different keysym; map back to the physical key.
SHIFTED = {"exclam": "1", "at": "2", "numbersign": "3", "dollar": "4",
           "percent": "5", "asciicircum": "6", "ampersand": "7", "asterisk": "8",
           "parenleft": "9", "parenright": "0", "underscore": "minus",
           "plus": "equal", "braceleft": "bracketleft", "braceright": "bracketright",
           "colon": "semicolon", "quotedbl": "apostrophe", "asciitilde": "grave",
           "bar": "backslash", "less": "comma", "greater": "period", "question": "slash"}


def keysym_to_qcode(keysym):
    """(qcode, label) for a Tk keysym, or None if unsupported."""
    keysym = SHIFTED.get(keysym, keysym)
    if len(keysym) == 1 and keysym.isalnum():
        return keysym.lower(), keysym.upper()
    if keysym in QCODES:
        return QCODES[keysym], keysym.replace("_L", "").replace("_R", " (right)")
    return None


def load_settings():
    try:
        with open(SETTINGS) as f:
            return {**DEFAULTS, **json.load(f)}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save_settings(s):
    try:
        os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
        with open(SETTINGS, "w") as f:
            json.dump(s, f, indent=2)
    except OSError:
        pass


class Spammer:
    """Background tap loop. Key and timings can change while it runs."""

    def __init__(self):
        self.running = threading.Event()
        self.quitting = threading.Event()
        self.lock = threading.Lock()
        self.vm = None
        self.key = "q"
        self.hold = 0.05
        self.gap = 0.10
        self.status = "Stopped"
        threading.Thread(target=self._loop, daemon=True).start()

    def configure(self, vm_name, key, hold, gap):
        with self.lock:
            path = os.path.join(VM_ROOT, vm_name, "qmp.sock")
            if self.vm is None or self.vm.path != path:
                if self.vm is not None:
                    self.vm.close()
                self.vm = VMKeyboard(path)
            if key != self.key and self.running.is_set():
                self.vm.key(self.key, False)       # don't leave the old key held
            self.key, self.hold, self.gap = key, hold, gap

    def start(self):
        with self.lock:
            path = self.vm.path if self.vm else ""
        if not os.path.exists(path):
            self.status = "No VM control socket: start the VM with ./sober_vm.sh start"
            return
        self.running.set()
        self.status = "Running"

    def stop(self, status="Stopped"):
        self.running.clear()
        with self.lock:
            vm, key = self.vm, self.key
        if vm is not None:
            vm.key(key, False)
        self.status = status

    def _wait(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end and self.running.is_set():
            time.sleep(0.01)

    def _loop(self):
        while not self.quitting.is_set():
            if not self.running.wait(timeout=0.1):
                continue
            with self.lock:
                vm, key, hold, gap = self.vm, self.key, self.hold, self.gap
            if not vm.key(key, True):
                self.stop("Can't reach the VM. Is it running?")
                continue
            time.sleep(hold)
            vm.key(key, False)
            self._wait(gap + LENIENCY)

    def shutdown(self):
        self.quitting.set()
        self.stop()
        with self.lock:
            if self.vm is not None:
                self.vm.close()


class App:
    def __init__(self, root):
        self.root = root
        self.settings = load_settings()
        self.spammer = Spammer()
        self.capturing = False

        root.title("VM key spam")
        root.resizable(False, False)
        f = ttk.Frame(root, padding=12)
        f.grid()

        ttk.Label(f, text="Key:").grid(row=0, column=0, sticky="w")
        self.key_label = ttk.Label(f, text=self.settings["label"], width=14,
                                   font=("TkDefaultFont", 14, "bold"))
        self.key_label.grid(row=0, column=1, sticky="w")
        self.set_btn = ttk.Button(f, text="Set key", command=self.capture_key)
        self.set_btn.grid(row=0, column=2, padx=(8, 0))

        self.hold = tk.IntVar(value=self.settings["hold_ms"])
        self.gap = tk.IntVar(value=self.settings["gap_ms"])
        ttk.Label(f, text="Hold (ms):").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Spinbox(f, from_=10, to=2000, increment=10, width=7,
                    textvariable=self.hold, command=self.apply).grid(
            row=1, column=1, sticky="w", pady=(8, 0))
        ttk.Label(f, text="Gap (ms):").grid(row=2, column=0, sticky="w")
        ttk.Spinbox(f, from_=0, to=10000, increment=10, width=7,
                    textvariable=self.gap, command=self.apply).grid(
            row=2, column=1, sticky="w")
        self.rate = ttk.Label(f, text="")
        self.rate.grid(row=1, column=2, rowspan=2)

        self.vm_name = tk.StringVar(value=self.settings["vm"])
        ttk.Label(f, text="VM:").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(f, textvariable=self.vm_name, width=10).grid(
            row=3, column=1, sticky="w", pady=(8, 0))

        self.toggle_btn = ttk.Button(f, text="Start (Numpad 7)", command=self.toggle)
        self.toggle_btn.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(12, 4))
        self.status = ttk.Label(f, text="", foreground="gray", wraplength=300)
        self.status.grid(row=5, column=0, columnspan=3, sticky="w")

        for var in (self.hold, self.gap, self.vm_name):
            var.trace_add("write", lambda *_: self.apply())
        root.bind("<Key>", self.on_key)
        root.protocol("WM_DELETE_WINDOW", self.quit)

        self.apply()
        self.hotkeys = keyboard.Listener(on_press=self.on_hotkey)
        self.hotkeys.daemon = True
        self.hotkeys.start()
        self.refresh()

    def apply(self):
        try:
            hold, gap = int(self.hold.get()), int(self.gap.get())
        except (tk.TclError, ValueError):
            return                               # mid-edit, not a number yet
        self.settings.update(hold_ms=hold, gap_ms=gap, vm=self.vm_name.get().strip() or "alt")
        self.spammer.configure(self.settings["vm"], self.settings["key"],
                               hold / 1000, gap / 1000)
        per = (hold + gap) / 1000 + LENIENCY
        self.rate.config(text=f"~{1 / per:.1f} taps/s" if per > 0 else "")
        save_settings(self.settings)

    def capture_key(self):
        self.capturing = True
        self.set_btn.config(text="Press a key...")
        self.root.focus_force()

    def on_key(self, event):
        if not self.capturing:
            return
        mapped = keysym_to_qcode(event.keysym)
        if mapped is None:
            self.spammer.status = f"Can't send '{event.keysym}' to the VM, try another key"
            return
        self.capturing = False
        self.set_btn.config(text="Set key")
        self.settings["key"], self.settings["label"] = mapped
        self.key_label.config(text=mapped[1])
        self.apply()
        return "break"                           # don't type it into a field

    def toggle(self):
        if self.spammer.running.is_set():
            self.spammer.stop()
        else:
            self.spammer.start()

    def on_hotkey(self, key):                    # pynput thread
        if is_start(key) and not self.spammer.running.is_set():
            self.spammer.start()
        elif is_stop(key) and self.spammer.running.is_set():
            self.spammer.stop()

    def refresh(self):
        running = self.spammer.running.is_set()
        self.toggle_btn.config(text="Stop (Numpad 8)" if running else "Start (Numpad 7)")
        self.status.config(text=self.spammer.status,
                           foreground="green" if running else "gray")
        self.root.after(100, self.refresh)

    def quit(self):
        self.spammer.shutdown()
        self.hotkeys.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
