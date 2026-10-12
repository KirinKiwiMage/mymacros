#!/usr/bin/env bash
# Run another Sober in a KVM virtual machine: a whole separate computer as
# far as Sober can tell, so its single-instance checks can't see the Sober
# running on the host.
#
# The VM is Ubuntu 24.04 (cloud image) with a bare X session (openbox) that
# logs in automatically and starts Sober. Graphics go through virtio-gpu +
# virgl (OpenGL passed through to your real GPU), so Sober is switched to its
# OpenGL renderer. Expect lower FPS than on the host: fine for an alt, not
# for serious play.
#
# Usage:
#   ./sober_vm.sh deps              # install QEMU etc. (uses sudo, once)
#   ./sober_vm.sh create [name]     # download Ubuntu + build the VM (once)
#   ./sober_vm.sh start  [name]     # boot it; first boot installs Sober (~15 min)
#   ./sober_vm.sh ssh    [name]     # shell into the VM (for logs)
#   ./sober_vm.sh update [name]     # apply in-VM fixes to a VM made earlier
#   ./sober_vm.sh logs   [name]     # why did Sober restart? -> ~/sober_vm_logs.txt
#   ./sober_vm.sh delete [name]     # remove the VM
#
# While a VM runs, $SOBER_VM_DIR/<name>/qmp.sock is QEMU's control socket;
# vm_q_spam.py uses it to type into the VM without it having focus.
#
# start options:
#   --no-stealth      show the VM as QEMU. By default it hides the hypervisor
#                     CPU flag and QEMU hardware names: as a QEMU device Roblox
#                     put the client on a test channel that kept restarting it
#                     (and the mouse misbehaved)
#   --sdl             use QEMU's SDL window instead of GTK (if GTK+GL fails)
#   --res WxH         VM screen size (default 1600x900, or your screen's size
#                     with --fullscreen)
#   --fullscreen      run the VM window fullscreen (Ctrl+Alt+F toggles)
#   --tablet          absolute "tablet" pointer instead of a normal mouse: no
#                     need to click in to grab it, but Roblox's camera turns
#                     wildly fast with it
#
# The mouse is captured when you click into the VM window; Ctrl+Alt+G
# releases it.
#
# Default name is "alt". Tweak with env vars: SOBER_VM_RAM (MiB, default
# 6144), SOBER_VM_CPUS (default 4), SOBER_VM_DISK (default 40G).

set -euo pipefail

VM_ROOT="${SOBER_VM_DIR:-$HOME/.local/share/sober-vm}"
IMG_URL="https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
BASE_IMG="$VM_ROOT/noble-server-cloudimg-amd64.img"
RAM="${SOBER_VM_RAM:-6144}"
CPUS="${SOBER_VM_CPUS:-4}"
DISK="${SOBER_VM_DISK:-40G}"

die() { echo "sober_vm: $*" >&2; exit 1; }

# Runs inside the VM as user "sober" (from cloud-init on new VMs, over SSH
# for `update`). Makes Sober's game window borderless fullscreen on the VM
# screen, so it isn't an 800x600 window in a corner.
guest_setup() {
    cat <<'EOF'
#!/bin/bash
set -e
mkdir -p ~/.config/openbox
python3 - <<'PY'
import os
xml = open("/etc/xdg/openbox/rc.xml").read()
rule = """  <application class="org.vinegarhq.Sober" type="normal">
    <decor>no</decor>
    <fullscreen>yes</fullscreen>
  </application>
"""
if 'class="org.vinegarhq.Sober"' not in xml:
    if "</applications>" in xml:
        xml = xml.replace("</applications>", rule + "</applications>", 1)
    else:
        xml = xml.replace("</openbox_config>",
                          "<applications>\n" + rule + "</applications>\n</openbox_config>", 1)
open(os.path.expanduser("~/.config/openbox/rc.xml"), "w").write(xml)
PY
# VMs made before Sober exits were logged: patch the restart loop.
python3 - <<'PY'
import os
p = os.path.expanduser("~/.xinitrc")
x = open(p).read()
old = "    flatpak run org.vinegarhq.Sober\n"
new = ("    [ -f ~/sober-last.log ] && mv ~/sober-last.log ~/sober-prev.log\n"
       "    flatpak run org.vinegarhq.Sober > ~/sober-last.log 2>&1\n"
       "    rc=$?\n"
       "    echo \"$(date '+%F %T') Sober exited with code $rc\" >> ~/sober-restarts.log\n")
if old in x:
    open(p, "w").write(x.replace(old, new, 1))
PY
echo "Sober VM setup applied. Restart loop is now:"
sed -n '/while true/,/done/p' ~/.xinitrc
EOF
}

vm_dir() {
    local name="${1:-alt}"
    [[ "$name" =~ ^[A-Za-z0-9_-]+$ ]] || die "bad VM name '$name' (letters, numbers, _ -)"
    echo "$VM_ROOT/$name"
}

cmd_deps() {
    grep -qE '(vmx|svm)' /proc/cpuinfo || die "CPU virtualization (VT-x) is off: enable it in the BIOS"
    local pkgs=(qemu-system-x86 qemu-utils cloud-image-utils curl)
    # Ubuntu 24.04+ split the GTK window and virgl/OpenGL support into modules.
    for p in qemu-system-gui qemu-system-modules-opengl; do
        apt-cache show "$p" >/dev/null 2>&1 && pkgs+=("$p")
    done
    echo "Installing: ${pkgs[*]}"
    sudo apt-get install -y "${pkgs[@]}"
    if [[ ! -w /dev/kvm ]]; then
        echo
        echo "You can't open /dev/kvm yet. Run this, then log out and back in:"
        echo "  sudo usermod -aG kvm $USER"
    fi
}

write_cloud_init() {
    local dir="$1" name="$2" password="$3"
    cat > "$dir/meta-data" <<EOF
instance-id: sober-$name
local-hostname: sober-$name
EOF
    # Quoted heredoc: nothing in here is expanded by this script; the
    # @@PLACEHOLDERS@@ are filled in by sed below.
    cat > "$dir/user-data" <<'EOF'
#cloud-config
users:
  - name: sober
    gecos: Sober
    shell: /bin/bash
    groups: [audio, video, render, input]
    sudo: "ALL=(ALL) NOPASSWD:ALL"
    lock_passwd: false
    plain_text_passwd: "@@PASSWORD@@"
ssh_pwauth: true

package_update: true
packages:
  - xserver-xorg
  - xinit
  - x11-xserver-utils
  - openbox
  - dbus-x11
  - flatpak
  - pipewire
  - pipewire-pulse
  - wireplumber
  - python3

write_files:
  - path: /usr/local/bin/sober-vm-setup
    permissions: "0755"
    content: |
@@GUEST_SETUP@@
  # Log in automatically on the first console...
  - path: /etc/systemd/system/getty@tty1.service.d/autologin.conf
    content: |
      [Service]
      ExecStart=
      ExecStart=-/sbin/agetty --autologin sober --noclear %I $TERM
  # ...and start X there.
  - path: /home/sober/.bash_profile
    owner: sober:sober
    defer: true
    content: |
      if [ -z "$DISPLAY" ] && [ "$(tty)" = /dev/tty1 ]; then
          exec startx
      fi
  - path: /home/sober/.xinitrc
    owner: sober:sober
    permissions: "0755"
    defer: true
    content: |
      #!/bin/sh
      xset s off -dpms
      openbox &
      # virgl gives real (GPU-backed) OpenGL but only CPU-rendered Vulkan,
      # so make sure Sober uses its OpenGL renderer.
      python3 - <<'PY'
      import json, os
      p = os.path.expanduser("~/.var/app/org.vinegarhq.Sober/config/sober/config.json")
      os.makedirs(os.path.dirname(p), exist_ok=True)
      try:
          cfg = json.load(open(p))
      except (OSError, ValueError):
          cfg = {}
      cfg["use_opengl"] = True
      json.dump(cfg, open(p, "w"), indent=4)
      PY
      # Reopen Sober if it's closed; shut the VM down from its window to stop.
      # Each run's output is kept (the one before a restart as sober-prev.log)
      # and every exit is logged, for `./sober_vm.sh logs`.
      while true; do
          [ -f ~/sober-last.log ] && mv ~/sober-last.log ~/sober-prev.log
          flatpak run org.vinegarhq.Sober > ~/sober-last.log 2>&1
          rc=$?  # before $(date) below resets $?
          echo "$(date '+%F %T') Sober exited with code $rc" >> ~/sober-restarts.log
          sleep 3
      done

runcmd:
  # The cloud image's minimal kernel lacks sound drivers.
  - [bash, -c, "apt-get install -y linux-modules-extra-$(uname -r) || true"]
  - [flatpak, remote-add, --if-not-exists, flathub, "https://dl.flathub.org/repo/flathub.flatpakrepo"]
  - [flatpak, install, -y, --noninteractive, flathub, org.vinegarhq.Sober]
  - [sudo, -u, sober, -H, /usr/local/bin/sober-vm-setup]

power_state:
  mode: reboot
  message: "Sober VM set up, rebooting into Sober"
  condition: true
EOF
    sed -i "s|@@PASSWORD@@|$password|" "$dir/user-data"
    # Splice in the setup script, indented to sit inside "content: |".
    local tmp="$dir/user-data.tmp"
    while IFS= read -r line; do
        if [[ "$line" == "@@GUEST_SETUP@@" ]]; then
            guest_setup | sed 's/^/      /'
        else
            printf '%s\n' "$line"
        fi
    done < "$dir/user-data" > "$tmp"
    mv "$tmp" "$dir/user-data"
    cloud-localds "$dir/seed.iso" "$dir/user-data" "$dir/meta-data"
}

cmd_create() {
    local name="${1:-alt}" dir
    dir="$(vm_dir "$name")"
    [[ -e "$dir/disk.qcow2" ]] && die "VM '$name' already exists ($dir)"
    command -v qemu-img >/dev/null && command -v cloud-localds >/dev/null \
        || die "missing tools: run ./sober_vm.sh deps first"
    mkdir -p "$dir"

    if [[ ! -f "$BASE_IMG" ]]; then
        echo "Downloading Ubuntu 24.04 cloud image (~600 MB)..."
        curl -fL -C - -o "$BASE_IMG.part" "$IMG_URL"
        mv "$BASE_IMG.part" "$BASE_IMG"
    fi
    # Copy-on-write disk on top of the shared base image; grows as needed.
    qemu-img create -q -f qcow2 -b "$BASE_IMG" -F qcow2 "$dir/disk.qcow2" "$DISK"

    # SSH port: first free one from 2222 among existing VMs.
    local port=2222
    while grep -qx "$port" "$VM_ROOT"/*/ssh_port 2>/dev/null; do port=$((port + 1)); done
    echo "$port" > "$dir/ssh_port"

    local password
    # Fixed-size read: `tr </dev/urandom | head` dies of SIGPIPE under pipefail.
    password="$(od -An -N12 -tx1 /dev/urandom | tr -d ' \n')"
    echo "$password" > "$dir/password.txt"
    chmod 600 "$dir/password.txt"
    write_cloud_init "$dir" "$name" "$password"

    echo "Created VM '$name' in $dir"
    echo "Next: ./sober_vm.sh start $name"
}

cmd_start() {
    local name="alt" stealth=1 sdl=0 tablet=0 fullscreen=0 res=""
    while (( $# )); do
        case "$1" in
            --stealth) stealth=1 ;;  # the default now
            --no-stealth) stealth=0 ;;
            --sdl) sdl=1 ;;
            --tablet) tablet=1 ;;
            --relative-mouse) ;;  # the default now
            --fullscreen) fullscreen=1 ;;
            --res) shift; res="${1:-}" ;;
            --res=*) res="${1#--res=}" ;;
            -*) die "unknown option $1" ;;
            *) name="$1" ;;
        esac
        shift
    done
    if [[ -z "$res" ]]; then
        res=1600x900
        if (( fullscreen )); then
            res="$(xrandr --current 2>/dev/null | awk '/\*/ {print $1; exit}')"
            [[ -n "$res" ]] || res=1920x1080
        fi
    fi
    [[ "$res" =~ ^([0-9]+)x([0-9]+)$ ]] || die "bad --res '$res' (want e.g. 1920x1080)"
    local xres="${BASH_REMATCH[1]}" yres="${BASH_REMATCH[2]}"
    # No zoom-to-fit: resizing the window must not stretch (or resize) the
    # VM's screen under a running game.
    local display="gtk,gl=on,zoom-to-fit=off"
    (( sdl )) && display="sdl,gl=on"
    local extra=()
    (( fullscreen )) && extra+=(-full-screen)
    local dir
    dir="$(vm_dir "$name")"
    [[ -f "$dir/disk.qcow2" ]] || die "no VM '$name': run ./sober_vm.sh create $name"
    [[ -w /dev/kvm ]] || die "can't open /dev/kvm (see ./sober_vm.sh deps)"

    local cpu="host" smbios=()
    if (( stealth )); then
        # No hypervisor CPUID bit / KVM signature, generic DMI names.
        cpu="host,-hypervisor,kvm=off"
        smbios=(-smbios "type=1,manufacturer=Generic,product=Desktop,version=1.0"
                -smbios "type=2,manufacturer=Generic,product=Desktop")
    fi
    # A tablet reports absolute positions; Roblox reads raw mouse motion for
    # the camera, and absolute values (0-32767) make it spin wildly. The
    # default PS/2 mouse is relative: click in to grab, Ctrl+Alt+G releases.
    local input=()
    (( tablet )) && input=(-device usb-tablet)

    echo "Starting VM '$name'."
    # cloud-init's install + reboot only happens on the very first boot.
    if [[ ! -e "$dir/started" ]]; then
        echo "First boot: installs Sober, then reboots into it (~15 min)."
        touch "$dir/started"
    fi
    echo "SSH: ./sober_vm.sh ssh $name   (password in $dir/password.txt)"
    exec qemu-system-x86_64 \
        -name "sober-$name" \
        -enable-kvm -machine q35 -cpu "$cpu" -smp "$CPUS" -m "$RAM" \
        "${smbios[@]}" \
        -drive "file=$dir/disk.qcow2,if=virtio" \
        -drive "file=$dir/seed.iso,format=raw,if=virtio,readonly=on" \
        -nic "user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$(cat "$dir/ssh_port")-:22" \
        -device "virtio-vga-gl,xres=$xres,yres=$yres" -display "$display" "${extra[@]}" \
        -audiodev pa,id=snd0 -device intel-hda -device hda-duplex,audiodev=snd0 \
        -device qemu-xhci "${input[@]}" \
        -qmp "unix:$dir/qmp.sock,server=on,wait=off"
}

cmd_ssh() {
    local dir
    dir="$(vm_dir "${1:-alt}")"
    [[ -f "$dir/ssh_port" ]] || die "no VM '${1:-alt}'"
    echo "Password: $(cat "$dir/password.txt")"
    exec ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -p "$(cat "$dir/ssh_port")" sober@127.0.0.1
}

cmd_update() {
    local dir
    dir="$(vm_dir "${1:-alt}")"
    [[ -f "$dir/ssh_port" ]] || die "no VM '${1:-alt}'"
    echo "Applying in-VM fixes to '${1:-alt}' (VM must be running)."
    echo "Password: $(cat "$dir/password.txt")"
    guest_setup | ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -p "$(cat "$dir/ssh_port")" sober@127.0.0.1 'bash -s'
    echo "Done. Shut the VM down and start it again to use it."
}

# Runs inside the VM for `logs`: why did Sober restart?
guest_logs() {
    cat <<'EOF'
echo "== uptime / memory"; uptime; free -m
echo; echo "== recent boots / reboots / shutdowns"
journalctl --list-boots --no-pager 2>/dev/null | tail -n 6
last -x reboot shutdown 2>/dev/null | head -n 8
echo; echo "== how the previous boot ended (last 60 lines of its journal)"
sudo journalctl -b -1 -n 60 --no-pager 2>/dev/null || echo "(no previous boot in the journal)"
echo; echo "== previous boot: errors"
sudo journalctl -b -1 -p err --no-pager 2>/dev/null | tail -n 40
echo; echo "== cloud-init (should be 'done', once)"
cloud-init status --long 2>/dev/null | head -n 6
ls /var/lib/cloud/instances 2>/dev/null
echo; echo "== Sober launches this boot (one systemd scope per 'flatpak run')"
journalctl --user -b --no-pager 2>/dev/null | grep -E "app-flatpak-org.vinegarhq.Sober-[0-9]+.scope" | tail -n 20
echo; echo "== Roblox sessions (one log file per launch, newest last)"
ls -ltr --time-style=+%T ~/.var/app/org.vinegarhq.Sober/data/sober/appData/logs/ 2>/dev/null | tail -n 12
echo; echo "== restart loop in ~/.xinitrc (should log exits)"
sed -n '/while true/,/done/p' ~/.xinitrc
echo; echo "== Sober exits (newest last)"
tail -n 30 ~/sober-restarts.log 2>/dev/null || echo "(none logged yet: run ./sober_vm.sh update first)"
echo; echo "== kernel: out-of-memory kills / GPU errors this boot"
sudo journalctl -k -b --no-pager 2>/dev/null \
    | grep -iE "out of memory|oom-kill|killed process|virtio_gpu|drm.*error|segfault" | tail -n 30
echo; echo "== output of the Sober run before the last restart (last 200 lines)"
tail -n 200 ~/sober-prev.log 2>/dev/null || echo "(none)"
echo; echo "== newest Roblox logs (last 80 lines each)"
for f in $(ls -t ~/.var/app/org.vinegarhq.Sober/data/sober/appData/logs/*.log 2>/dev/null | head -n 2); do
    echo "-- $f"; tail -n 80 "$f"
done
EOF
}

cmd_logs() {
    local dir out="$HOME/sober_vm_logs.txt"
    dir="$(vm_dir "${1:-alt}")"
    [[ -f "$dir/ssh_port" ]] || die "no VM '${1:-alt}'"
    echo "Collecting logs from '${1:-alt}' (VM must be running)."
    echo "Password: $(cat "$dir/password.txt")"
    guest_logs | ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -p "$(cat "$dir/ssh_port")" sober@127.0.0.1 'bash -s' > "$out"
    echo "Saved to $out"
}

cmd_delete() {
    local dir
    dir="$(vm_dir "${1:-alt}")"
    [[ -d "$dir" ]] || die "no VM '${1:-alt}'"
    read -r -p "Delete VM '${1:-alt}' ($dir) and its Roblox login? [y/N] " ok
    [[ "$ok" == [yY] ]] && rm -rf "$dir" && echo "Deleted."
}

case "${1:-}" in
    deps) cmd_deps ;;
    create) shift; cmd_create "$@" ;;
    start) shift; cmd_start "$@" ;;
    ssh) shift; cmd_ssh "$@" ;;
    update) shift; cmd_update "$@" ;;
    logs) shift; cmd_logs "$@" ;;
    delete) shift; cmd_delete "$@" ;;
    *) sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
