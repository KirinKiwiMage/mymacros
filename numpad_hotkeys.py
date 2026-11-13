"""
Shared start/stop hotkeys for all macros: Numpad 7 = start, Numpad 8 = stop.

pynput on X11 reports Numpad 7 as KeyCode(char='7') (NumLock on) or
Key.home (NumLock off), exactly like the top-row 7 / Home key, and gives no
keycode. So when one of those arrives we ask the X server which physical
keys are currently down and only accept it if it's the numpad one.
"""

from pynput import keyboard
from Xlib import XK
from Xlib.display import Display

_display = None
_keycodes = {}


def _setup():
    global _display
    if _display is None:
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
    """True if `key` (from a pynput on_press callback) is Numpad 7."""
    return _numpad(key, "7", keyboard.Key.home)


def is_stop(key):
    """True if `key` (from a pynput on_press callback) is Numpad 8."""
    return _numpad(key, "8", keyboard.Key.up)
