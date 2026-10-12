#!/usr/bin/env python3
"""
Multi-Sober: run several Sober (Roblox on Linux) instances at once, each
logged into its own account. Linux take on "multi-roblox" for Windows.

Multi-roblox on Windows works by holding Roblox's singleton mutex. Sober is
a Flatpak, so this script gives each instance its own profile instead:
separate XDG data/config/cache dirs. Each profile has its own login, its
own settings and its own Sober state, so the instances don't see each other.

Profiles live inside Sober's own Flatpak folder so the sandbox can always
reach them:
    ~/.var/app/org.vinegarhq.Sober/profiles/<name>/{data,config,cache}

Usage:
    python3 sober_multi.py launch alt1 alt2 alt3   # start one instance per profile
    python3 sober_multi.py launch alt1 -- roblox://...   # pass args to Sober
    python3 sober_multi.py list                    # show profiles
    python3 sober_multi.py remove alt1             # delete a profile (logs it out)
    python3 sober_multi.py diagnose                # with Sober open: show its
                                                   # locks/sockets/temp files

The first launch of a new profile downloads Roblox again and asks you to
log in. After that the login sticks. Your normal Sober (plain
`flatpak run org.vinegarhq.Sober`) is left untouched and can run alongside.
New profiles copy your normal Sober config.json, so settings like FPS
carry over; logins don't.

Each instance's output goes to <profile>/sober.log.

Requires: Sober installed from Flathub (flatpak install flathub org.vinegarhq.Sober)
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import time

APP_ID = "org.vinegarhq.Sober"
APP_DIR = os.path.expanduser(f"~/.var/app/{APP_ID}")
PROFILES_DIR = os.path.join(APP_DIR, "profiles")
DEFAULT_CONFIG = os.path.join(APP_DIR, "config", "sober", "config.json")

# Gap between launches so instances don't fight over first-run setup.
# Includes a little leniency for slow disks / lag spikes.
LAUNCH_GAP = 3.0 + 0.5

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def profile_dir(name):
    if not NAME_RE.match(name) or name in (".", ".."):
        sys.exit(f"Bad profile name {name!r}: use letters, numbers, '_', '-', '.'")
    return os.path.join(PROFILES_DIR, name)


def ensure_profile(name):
    root = profile_dir(name)
    new = not os.path.isdir(root)
    for sub in ("data", "config", "cache"):
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    if new and os.path.isfile(DEFAULT_CONFIG):
        dst = os.path.join(root, "config", "sober")
        os.makedirs(dst, exist_ok=True)
        shutil.copy2(DEFAULT_CONFIG, dst)
    return root, new


def check_sober():
    if shutil.which("flatpak") is None:
        sys.exit("flatpak not found. Install Sober: https://sober.vinegarhq.org")
    r = subprocess.run(["flatpak", "info", APP_ID], capture_output=True)
    if r.returncode != 0:
        sys.exit(f"{APP_ID} isn't installed. Run: flatpak install flathub {APP_ID}")


def launch(names, extra_args):
    check_sober()
    for i, name in enumerate(names):
        if i:
            time.sleep(LAUNCH_GAP)
        root, new = ensure_profile(name)
        cmd = [
            "flatpak", "run",
            f"--env=XDG_DATA_HOME={root}/data",
            f"--env=XDG_CONFIG_HOME={root}/config",
            f"--env=XDG_CACHE_HOME={root}/cache",
            APP_ID, *extra_args,
        ]
        log = open(os.path.join(root, "sober.log"), "ab")
        # New session so closing this terminal doesn't kill the instances.
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        log.close()
        note = " (new profile: log in once)" if new else ""
        print(f"[{name}] started, pid {proc.pid}{note}")


def list_profiles():
    if not os.path.isdir(PROFILES_DIR) or not os.listdir(PROFILES_DIR):
        print("No profiles yet. Make one with: sober_multi.py launch <name>")
        return
    for name in sorted(os.listdir(PROFILES_DIR)):
        if os.path.isdir(os.path.join(PROFILES_DIR, name)):
            print(name)


def remove(names):
    for name in names:
        root = profile_dir(name)
        if not os.path.isdir(root):
            print(f"[{name}] no such profile")
            continue
        shutil.rmtree(root)
        print(f"[{name}] removed")


def _children(pid):
    """All descendant pids of pid (Sober runs as several processes)."""
    kids = {}
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/stat") as f:
                    ppid = int(f.read().rsplit(")", 1)[1].split()[1])
                kids.setdefault(ppid, []).append(int(d))
            except (OSError, ValueError, IndexError):
                pass
    out, todo = [], [pid]
    while todo:
        p = todo.pop()
        out.append(p)
        todo.extend(kids.get(p, []))
    return out


def _sh(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"<{e}>"


def _ls(path, depth=2, indent="  "):
    lines = []
    try:
        for name in sorted(os.listdir(path)):
            full = os.path.join(path, name)
            lines.append(f"{indent}{name}{'/' if os.path.isdir(full) else ''}")
            if depth > 1 and os.path.isdir(full) and not os.path.islink(full):
                lines += _ls(full, depth - 1, indent + "  ")
    except OSError as e:
        lines.append(f"{indent}<{e.strerror}>")
    return lines


def diagnose():
    """Dump how a running Sober could be spotting other instances: open
    files, file locks, unix sockets, D-Bus names, temp/runtime dirs."""
    out = ["== flatpak ps", _sh(["flatpak", "ps", "--columns=instance,pid,child-pid,application"])]
    out += ["== permissions", _sh(["flatpak", "info", "--show-permissions", APP_ID])]
    out += ["== dbus names", "\n".join(l for l in _sh(["busctl", "--user", "list", "--no-legend"]).splitlines()
                                       if "sober" in l.lower() or "vinegar" in l.lower()) or "(none matching)"]

    pids = []
    for line in _sh(["flatpak", "ps", "--columns=pid,application"]).splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == APP_ID and parts[0].isdigit():
            pids += _children(int(parts[0]))
    if not pids:
        out.append("!! No running Sober found. Start one first, then run diagnose.")
        print("\n".join(out))
        return

    try:
        with open("/proc/locks") as f:
            locks = f.read().splitlines()
    except OSError:
        locks = []
    for pid in pids:
        try:
            with open(f"/proc/{pid}/comm") as f:
                comm = f.read().strip()
        except OSError:
            continue
        out.append(f"== pid {pid} ({comm})")
        socks = set()
        try:
            fds = os.listdir(f"/proc/{pid}/fd")
        except OSError as e:
            out.append(f"  <fd: {e.strerror}>")
            fds = []
        for fd in fds:
            try:
                target = os.readlink(f"/proc/{pid}/fd/{fd}")
            except OSError:
                continue
            if target.startswith("socket:["):
                socks.add(target[8:-1])
            elif not target.startswith(("/dev/", "pipe:", "anon_inode:", "/memfd:")) \
                    and not target.endswith((".so", ".apk")):
                out.append(f"  fd {fd} -> {target}")
        try:
            with open(f"/proc/{pid}/net/unix") as f:
                for line in f.read().splitlines()[1:]:
                    cols = line.split()
                    if len(cols) >= 8 and cols[6] in socks:
                        out.append(f"  unix socket {cols[7]}")
        except OSError:
            pass
        out += [f"  lock {l}" for l in locks if f" {pid} " in l]

    root = f"/proc/{pids[0]}/root"
    out += [f"== sandbox /tmp"] + _ls(f"{root}/tmp")
    out += [f"== sandbox /run/user/{os.getuid()}"] + _ls(f"{root}/run/user/{os.getuid()}")
    out += [f"== sandbox /run/flatpak"] + _ls(f"{root}/run/flatpak", depth=1)

    report = "\n".join(out)
    path = os.path.join(APP_DIR, "sober_multi_diagnose.txt")
    with open(path, "w") as f:
        f.write(report + "\n")
    print(report)
    print(f"\nSaved to {path}")


def main():
    p = argparse.ArgumentParser(description="Run multiple Sober instances, one per profile.")
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("launch", help="start one instance per profile name")
    pl.add_argument("names", nargs="+",
                    help="profile names; args after -- go to Sober (e.g. a roblox:// link)")
    sub.add_parser("list", help="list profiles")
    pr = sub.add_parser("remove", help="delete profiles")
    pr.add_argument("names", nargs="+")
    sub.add_parser("diagnose", help="with Sober running, dump how it detects other instances")

    # argparse can't split "names... -- args" on its own.
    argv = sys.argv[1:]
    extra = []
    if "--" in argv:
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    args = p.parse_args(argv)

    if args.cmd == "launch":
        launch(args.names, extra)
    elif args.cmd == "list":
        list_profiles()
    elif args.cmd == "remove":
        remove(args.names)
    elif args.cmd == "diagnose":
        diagnose()


if __name__ == "__main__":
    main()
