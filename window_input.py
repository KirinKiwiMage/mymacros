"""
Send key presses somewhere other than the window you're using, so a macro
can run in the background while you use the rest of the desktop.

WindowKeyboard: XSendEvent straight to one window on your desktop. The X
    server marks these events as synthetic, and some programs ignore them.
DisplayKeyboard: real (XTEST) key presses on a separate X display, such as
    a nested Xephyr server (see sober_display.sh). The program can't tell
    them apart from a keyboard, and your own desktop never sees them.

Both have the same press()/release() interface as
pynput.keyboard.Controller, so they can be dropped in place of one.
"""

from pynput import keyboard
from Xlib import X, XK, protocol
from Xlib.display import Display
from Xlib.ext import xtest

# keysym -> modifier mask, so held modifiers show up in the event state
_MODIFIERS = {
    XK.XK_Shift_L: X.ShiftMask, XK.XK_Shift_R: X.ShiftMask,
    XK.XK_Control_L: X.ControlMask, XK.XK_Control_R: X.ControlMask,
    XK.XK_Alt_L: X.Mod1Mask, XK.XK_Alt_R: X.Mod1Mask,
}


def _title(display, window):
    try:
        name = window.get_full_property(
            display.intern_atom("_NET_WM_NAME"), display.intern_atom("UTF8_STRING"))
        if name:
            return name.value.decode("utf-8", "replace")
        return window.get_wm_name() or ""
    except Exception:
        return ""


def list_windows(display=None):
    """[(window_id, title)] for every top-level window the window manager knows."""
    display = display or Display()
    root = display.screen().root
    prop = root.get_full_property(display.intern_atom("_NET_CLIENT_LIST"), X.AnyPropertyType)
    ids = prop.value if prop else [w.id for w in root.query_tree().children]
    return [(wid, _title(display, display.create_resource_object("window", wid)))
            for wid in ids]


def find_window(name, display=None):
    """First top-level window whose title contains `name` (case-insensitive)."""
    matches = [(wid, title) for wid, title in list_windows(display)
               if name.lower() in title.lower()]
    return matches[0] if matches else None


def _keysym(key):
    if isinstance(key, keyboard.Key):
        key = key.value
    if isinstance(key, keyboard.KeyCode):
        if key.vk is not None:
            return key.vk          # pynput's X11 backend stores the keysym here
        key = key.char
    keysym = XK.string_to_keysym(key)
    return keysym or ord(key)


class WindowKeyboard:
    """Drop-in for pynput.keyboard.Controller that types into one window."""

    def __init__(self, window_id):
        self.display = Display()
        self.root = self.display.screen().root
        self.window = self.display.create_resource_object("window", window_id)
        self.state = 0

    def focus(self):
        """Tell the window it has focus. Many games ignore keys while they
        think they're in the background, so send this once before typing."""
        event = protocol.event.FocusIn(window=self.window, mode=X.NotifyNormal,
                                       detail=X.NotifyNonlinear)
        self.window.send_event(event, event_mask=X.FocusChangeMask)
        self.display.flush()

    def _send(self, event_class, key):
        keysym = _keysym(key)
        event = event_class(
            time=X.CurrentTime, root=self.root, window=self.window,
            child=X.NONE, same_screen=1, root_x=0, root_y=0, event_x=0, event_y=0,
            state=self.state, detail=self.display.keysym_to_keycode(keysym))
        mask = X.KeyPressMask if event_class is protocol.event.KeyPress else X.KeyReleaseMask
        self.window.send_event(event, event_mask=mask, propagate=True)
        self.display.flush()
        return keysym

    def press(self, key):
        keysym = self._send(protocol.event.KeyPress, key)
        self.state |= _MODIFIERS.get(keysym, 0)

    def release(self, key):
        keysym = self._send(protocol.event.KeyRelease, key)
        self.state &= ~_MODIFIERS.get(keysym, 0)


class DisplayKeyboard:
    """Drop-in for pynput.keyboard.Controller that types on another X display."""

    def __init__(self, display_name):
        self.display = Display(display_name)

    def focus(self, window_id):
        """Give the window keyboard focus. A bare Xephyr has no window manager,
        so nothing moves the focus away again."""
        window = self.display.create_resource_object("window", window_id)
        window.set_input_focus(X.RevertToPointerRoot, X.CurrentTime)
        self.display.flush()

    def _send(self, event_type, key):
        keycode = self.display.keysym_to_keycode(_keysym(key))
        xtest.fake_input(self.display, event_type, keycode)
        self.display.flush()

    def press(self, key):
        self._send(X.KeyPress, key)

    def release(self, key):
        self._send(X.KeyRelease, key)
