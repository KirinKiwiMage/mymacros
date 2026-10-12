#!/usr/bin/env python3
"""
Q spam macro for the Sober VM (sober_vm.sh).

Taps Q over and over inside the VM. Keys go straight into the VM through
QEMU's control socket (QMP), not through your desktop, so it keeps working
while you play on the host with the VM window in the background.

Hotkeys (pressed on the host, any window):
    Numpad 7 - start
    Numpad 8 - stop
    Ctrl+C in the terminal - quit

Usage:
    python3 vm_q_spam.py [vm-name]      # default VM name: alt

The VM must have been started with the current sober_vm.sh (it adds the
control socket); restart it once if it was running before.

Requires: pip install pynput   (X11 session, which Zorin uses by default)
"""

import json
import os
import socket
import sys
import threading
import time

from pynput import keyboard

from numpad_hotkeys import is_start, is_stop

# ---- tuning -------------------------------------------------------------
KEY = "q"                # QEMU key name ("qcode"): q, e, spc, shift, ...
TAP_HOLD = 0.05          # how long Q is held down per tap
TAP_GAP = 0.10           # pause between taps

LENIENCY = 0.03          # extra seconds added to each wait for lag spikes
# -------------------------------------------------------------------------

VM_ROOT = os.environ.get("SOBER_VM_DIR",
                         os.path.expanduser("~/.local/share/sober-vm"))

running = threading.Event()
quitting = threading.Event()


class VMKeyboard:
    """Minimal QMP client: key down/up events into the VM's keyboard."""

    def __init__(self, path):
        self.path = path
        self.sock = None
        self.buf = b""
        self.lock = threading.Lock()

    def _connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(self.path)
        self.sock, self.buf = s, b""
        self._reply()                                  # greeting
        self._command({"execute": "qmp_capabilities"})

    def _reply(self):
        """Next reply line, skipping async events QEMU may send."""
        while True:
            while b"\n" not in self.buf:
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise ConnectionError("VM control socket closed")
                self.buf += chunk
            line, self.buf = self.buf.split(b"\n", 1)
            msg = json.loads(line)
            if "event" not in msg:
                return msg

    def _command(self, cmd):
        self.sock.sendall(json.dumps(cmd).encode() + b"\n")
        msg = self._reply()
        if "error" in msg:
            raise RuntimeError(msg["error"].get("desc", msg["error"]))
        return msg

    def key(self, name, down):
        """Send one key event; reconnects if the VM was restarted."""
        cmd = {"execute": "input-send-event", "arguments": {"events": [
            {"type": "key", "data": {"down": down,
                                     "key": {"type": "qcode", "data": name}}}]}}
        with self.lock:
            for attempt in (1, 2):
                try:
                    if self.sock is None:
                        self._connect()
                    self._command(cmd)
                    return True
                except (OSError, ValueError, RuntimeError) as e:
                    self.close()
                    if attempt == 2:
                        print(f"[macro] can't reach the VM ({e}); is it running?")
        return False

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None


def wait(seconds):
    """Sleep in small steps so pausing/quitting is responsive.
    Returns False if the macro was paused or quit during the wait."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if quitting.is_set() or not running.is_set():
            return False
        time.sleep(0.01)
    return True


def macro_loop(vm):
    while not quitting.is_set():
        if not running.wait(timeout=0.1):
            continue
        if not vm.key(KEY, True):
            running.clear()          # VM unreachable: stop instead of spamming errors
            continue
        time.sleep(TAP_HOLD)
        vm.key(KEY, False)
        wait(TAP_GAP + LENIENCY)
    vm.key(KEY, False)


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "alt"
    path = os.path.join(VM_ROOT, name, "qmp.sock")
    if not os.path.exists(path):
        sys.exit(f"No control socket at {path}.\n"
                 f"Start the VM with: ./sober_vm.sh start {name}  "
                 f"(restart it if it was started before this update)")
    vm = VMKeyboard(path)

    def on_press(key):
        if is_start(key) and not running.is_set():
            running.set()
            print("[macro] started")
        elif is_stop(key) and running.is_set():
            running.clear()
            vm.key(KEY, False)
            print("[macro] stopped")

    print(f"[macro] spamming {KEY.upper()} in VM '{name}': Numpad 7 = start, "
          f"Numpad 8 = stop, Ctrl+C = quit "
          f"(~{1 / (TAP_HOLD + TAP_GAP + LENIENCY):.1f} taps/s)")
    worker = threading.Thread(target=macro_loop, args=(vm,), daemon=True)
    worker.start()
    try:
        with keyboard.Listener(on_press=on_press) as listener:
            listener.join()
    except KeyboardInterrupt:
        print("[macro] quitting")
    finally:
        quitting.set()
        running.clear()
        worker.join(timeout=1)
        vm.key(KEY, False)
        vm.close()


if __name__ == "__main__":
    main()
