# mymacros

Python macros using pynput, run on X11 (Zorin OS).

## Conventions for every macro
- Start hotkey: **Numpad 7**. Stop hotkey: **Numpad 8**. Match by X11 keysym `vk`:
  start `{65463 (KP_7), 65429 (KP_Home)}`, stop `{65464 (KP_8), 65431 (KP_Up)}`,
  so they work with NumLock on or off.
- Stopping releases any held keys. Quit with Ctrl+C in the terminal.
- Add a small leniency to waits so lag spikes don't break the timing.
