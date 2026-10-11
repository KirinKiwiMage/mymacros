#!/usr/bin/env bash
# Run Roblox (Sober) on its own nested X display so a macro can type into it
# while you use your normal desktop. Close Sober on your desktop first.
#
#   ./sober_display.sh            # 1280x720 on display :1
#   ./sober_display.sh 1600x900 :2
#
# Then: python3 fireball_macro.py --display :1 --window Sober
# Needs: sudo apt install xserver-xephyr
set -euo pipefail

SIZE=${1:-1280x720}
DISP=${2:-:1}

Xephyr "$DISP" -screen "$SIZE" -glamor -title "Roblox ($DISP)" -no-host-grab &
XEPHYR=$!
trap 'kill $XEPHYR 2>/dev/null' EXIT

# wait for the nested server to accept connections
for _ in $(seq 50); do
    xdpyinfo -display "$DISP" >/dev/null 2>&1 && break
    sleep 0.1
done

env -u WAYLAND_DISPLAY DISPLAY="$DISP" flatpak run org.vinegarhq.Sober
