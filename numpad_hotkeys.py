"""
Shared start/stop hotkeys for all macros: Numpad 7 = start, Numpad 8 = stop.

Use it like this:

    hotkeys = numpad_hotkeys.listen(on_start, on_stop)
    ...
    hotkeys.stop()

on_start / on_stop are called (with no arguments) on a background thread.

Two ways of seeing the keys, best one first:

1. evdev (default): reads the keyboard straight from /dev/input, below X11
   and every app. Whatever the focused window does with the keyboard (Sober
   grabbing it, raw input, a native Wayland window, ...) can't hide the key,
   and the kernel reports Numpad 7/8 as their own keys (KEY_KP7/KEY_KP8)
   whatever NumLock is set to. Key presses sent by macros (XTEST) never show
   up here, so a macro spamming "7" can't start itself.
   Needs:  pip install evdev
           sudo usermod -aG input $USER   (then log out and back in)

2. pynput fallback, used only if evdev isn't installed or /dev/input can't
   be read. pynput on X11 reports Numpad 7 as KeyCode(char='7') (NumLock on)
   or Key.home (NumLock off), exactly like the top-row 7 / Home key, so when
   one of those arrives we ask the X server whether the physical KP_7 key is
   down. This only sees keys the X server sees, which some games block.

Run `python3 numpad_hotkeys.py` to check which one you get and that the keys
are detected while the game is focused.
"""

import os
import selectors
import threading
import time

from pynput import keyboard

try:
    import evdev
    from evdev import ecodes
except ImportError:
    evdev = None

RESCAN_EVERY = 2.0   # seconds between checks for newly plugged-in keyboards


# ---- evdev backend ------------------------------------------------------------

class _EvdevListener:
    backend = "evdev"

    def __init__(self, on_start, on_stop):
        self._actions = {ecodes.KEY_KP7: on_start, ecodes.KEY_KP8: on_stop}
        self._selector = selectors.DefaultSelector()
        self._devices = {}            # path -> InputDevice
        self._stopping = threading.Event()
        self._scan()
        if not self._devices:
            self._selector.close()
            raise PermissionError(_no_access_reason())
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _scan(self):
        """Open every keyboard that has a numpad and isn't open yet."""
        for path in evdev.list_devices():
            if path in self._devices:
                continue
            try:
                dev = evdev.InputDevice(path)
            except OSError:
                continue              # no permission, or it just went away
            keys = dev.capabilities().get(ecodes.EV_KEY, [])
            if ecodes.KEY_KP7 in keys or ecodes.KEY_KP8 in keys:
                self._devices[path] = dev
                self._selector.register(dev, selectors.EVENT_READ)
            else:
                dev.close()

    def _drop(self, dev):
        self._devices.pop(dev.path, None)
        try:
            self._selector.unregister(dev)
        except (KeyError, ValueError):
            pass
        try:
            dev.close()
        except OSError:
            pass

    def _run(self):
        next_scan = time.monotonic() + RESCAN_EVERY
        while not self._stopping.is_set():
            for sel_key, _ in self._selector.select(timeout=0.25):
                dev = sel_key.fileobj
                try:
                    events = list(dev.read())
                except (OSError, BlockingIOError) as err:
                    if not isinstance(err, BlockingIOError):
                        self._drop(dev)   # keyboard unplugged
                    continue
                for event in events:
                    # value 1 = pressed (0 = released, 2 = key repeat)
                    if event.type == ecodes.EV_KEY and event.value == 1:
                        action = self._actions.get(event.code)
                        if action:
                            action()
            if time.monotonic() >= next_scan:
                self._scan()
                next_scan = time.monotonic() + RESCAN_EVERY
        for dev in list(self._devices.values()):
            self._drop(dev)
        self._selector.close()

    def stop(self):
        self._stopping.set()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=1)

    def join(self):
        # short timeouts so Ctrl+C still reaches the main thread
        while self._thread.is_alive():
            self._thread.join(timeout=0.2)


def _no_access_reason():
    try:
        nodes = [os.path.join("/dev/input", n) for n in os.listdir("/dev/input")
                 if n.startswith("event")]
    except OSError:
        nodes = []
    if any(not os.access(p, os.R_OK) for p in nodes):
        return ("no permission to read /dev/input/event* - run "
                "'sudo usermod -aG input $USER', then log out and back in")
    return "no keyboard with a numpad found in /dev/input"


# ---- pynput / X11 fallback ------------------------------------------------------

_display = None
_keycodes = {}


def _setup():
    global _display
    if _display is None:
        from Xlib import XK
        from Xlib.display import Display
        _display = Display()
        for name, keysym in (("7", "KP_7"), ("8", "KP_8")):
            _keycodes[name] = _display.keysym_to_keycode(XK.string_to_keysym(keysym))


def _is_down(keycode):
    keymap = _display.query_keymap()
    return bool(keymap[keycode // 8] & (1 << (keycode % 8)))


def _numpad(key, digit, nav_key):
    if key != keyboard.KeyCode.from_char(digit) and key != nav_key:
        return False
    _setup()
    return _is_down(_keycodes[digit])


def is_start(key):
    """True if `key` (from a pynput on_press callback) is Numpad 7. X11 only;
    prefer listen(), which also works when a game hides keys from X11."""
    return _numpad(key, "7", keyboard.Key.home)


def is_stop(key):
    """True if `key` (from a pynput on_press callback) is Numpad 8. X11 only;
    prefer listen(), which also works when a game hides keys from X11."""
    return _numpad(key, "8", keyboard.Key.up)


class _PynputListener:
    backend = "pynput"

    def __init__(self, on_start, on_stop):
        def on_press(key):
            if is_start(key):
                on_start()
            elif is_stop(key):
                on_stop()
        self._listener = keyboard.Listener(on_press=on_press)
        self._listener.start()

    def stop(self):
        self._listener.stop()

    def join(self):
        while self._listener.is_alive():
            self._listener.join(timeout=0.2)


# ---- public -----------------------------------------------------------------------

def listen(on_start, on_stop, quiet=False):
    """Call on_start() on Numpad 7 and on_stop() on Numpad 8, from a background
    thread. Returns an object with .stop(), .join() and .backend."""
    if evdev is None:
        reason = "the evdev module isn't installed (pip install evdev)"
    else:
        try:
            return _EvdevListener(on_start, on_stop)
        except PermissionError as err:
            reason = str(err)
    if not quiet:
        print(f"[hotkeys] WARNING: {reason}.\n"
              "[hotkeys] Falling back to X11 hotkeys, which some games (Sober) block.")
    return _PynputListener(on_start, on_stop)


if __name__ == "__main__":
    hotkeys = listen(lambda: print("Numpad 7 -> START", flush=True),
                     lambda: print("Numpad 8 -> STOP", flush=True))
    print(f"[hotkeys] using {hotkeys.backend}. Focus the game, press Numpad 7 / 8. "
          "Ctrl+C to quit.")
    try:
        hotkeys.join()
    except KeyboardInterrupt:
        hotkeys.stop()
