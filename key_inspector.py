#!/usr/bin/env python3
"""
Prints exactly what pynput sees for every key you press / release.

Run it, press Numpad 7 and Numpad 8 (try with NumLock on AND off),
then press Esc to quit and paste the output back.
"""

from pynput import keyboard


def describe(key):
    vk = getattr(key, "vk", None)
    char = getattr(key, "char", None)
    name = getattr(key, "name", None)
    value = getattr(key, "value", None)  # set for special Key.* members
    value_vk = getattr(value, "vk", None) if value is not None else None
    return (f"repr={key!r:<28} type={type(key).__name__:<8} "
            f"name={name!r:<12} char={char!r:<6} vk={vk!r:<8} "
            f"value.vk={value_vk!r:<8} hex_vk={hex(vk or value_vk or 0)}")


def on_press(key):
    print("PRESS   " + describe(key), flush=True)
    if key == keyboard.Key.esc:
        return False


def on_release(key):
    print("RELEASE " + describe(key), flush=True)


print("Listening... press keys (Esc to quit)")
with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
    listener.join()
