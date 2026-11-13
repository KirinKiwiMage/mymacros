# mymacros

Python macros using pynput, run on X11 (Zorin OS).

## Conventions for every macro
- Start hotkey: **Numpad 7**. Stop hotkey: **Numpad 8**. Use `numpad_hotkeys.is_start(key)` /
  `is_stop(key)` in the pynput `on_press` callback. pynput on X11 reports these keys as
  `'7'`/`'8'` (NumLock on) or `Key.home`/`Key.up` (NumLock off) with no vk, same as the
  top-row/nav keys, so the helper checks the physical KP_7/KP_8 key via the Xlib keymap.
- Stopping releases any held keys. Quit with Ctrl+C in the terminal.
- Add a small leniency to waits so lag spikes don't break the timing.
