#!/usr/bin/env bash
# Run Roblox (Sober) on its own nested X display so a macro can type into it
# while you use your normal desktop. Close Sober on your desktop first.
#
#   ./sober_display.sh              # 1280x720 on the first free display
#   ./sober_display.sh 1600x900
#
# It prints the display it picked (e.g. :2); pass that to the macro:
#   python3 fireball_macro.py --display :2 --window Sober
# Needs: sudo apt install xserver-xephyr
set -euo pipefail

SIZE=${1:-1280x720}

# Your desktop is usually :0 or :1 (Zorin/Ubuntu put the login screen on :0),
# so find the first number nothing is using.
N=2
while [ -e "/tmp/.X11-unix/X$N" ] || [ -e "/tmp/.X$N-lock" ]; do
    N=$((N + 1))
done
DISP=":$N"

Xephyr "$DISP" -screen "$SIZE" -glamor -title "Roblox ($DISP)" -no-host-grab &
XEPHYR=$!
trap 'kill $XEPHYR 2>/dev/null' EXIT

# wait for the nested server to accept connections, and bail if it died
for _ in $(seq 50); do
    kill -0 "$XEPHYR" 2>/dev/null || { echo "Xephyr failed to start on $DISP" >&2; exit 1; }
    xdpyinfo -display "$DISP" >/dev/null 2>&1 && break
    sleep 0.1
done
xdpyinfo -display "$DISP" >/dev/null 2>&1 || { echo "Xephyr on $DISP never came up" >&2; exit 1; }

echo
echo ">>> Roblox is on display $DISP. Run the macro with:"
echo ">>>   python3 fireball_macro.py --display $DISP --window Sober"
echo

env -u WAYLAND_DISPLAY DISPLAY="$DISP" flatpak run org.vinegarhq.Sober
