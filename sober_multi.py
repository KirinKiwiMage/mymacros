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


def main():
    p = argparse.ArgumentParser(description="Run multiple Sober instances, one per profile.")
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("launch", help="start one instance per profile name")
    pl.add_argument("names", nargs="+",
                    help="profile names; args after -- go to Sober (e.g. a roblox:// link)")
    sub.add_parser("list", help="list profiles")
    pr = sub.add_parser("remove", help="delete profiles")
    pr.add_argument("names", nargs="+")

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


if __name__ == "__main__":
    main()
