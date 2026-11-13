# mymacros

Python macros using pynput, run on X11 (Zorin OS).

## Conventions for every macro
- Start hotkey: **Numpad 7**. Stop hotkey: **Numpad 8**. Use
  `hotkeys = numpad_hotkeys.listen(on_start, on_stop)` (callbacks run on a background thread;
  `hotkeys.join()` / `hotkeys.stop()`). It reads the keyboard via evdev (/dev/input), so it
  still works when Sober/the game hides keys from X11; needs `pip install evdev` and the user
  in the `input` group. Falls back to pynput + the Xlib KP_7/KP_8 keymap check otherwise.
  Don't use a plain pynput Listener for the hotkeys.
- Stopping releases any held keys. Quit with Ctrl+C in the terminal.
- Add a small leniency to waits so lag spikes don't break the timing.
