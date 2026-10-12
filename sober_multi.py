#!/usr/bin/env python3
"""
Multi-Sober: run several Sober (Roblox on Linux) instances at once, each
logged into its own account. Linux take on "multi-roblox" for Windows.

Multi-roblox on Windows works by holding Roblox's singleton mutex. Sober's
equivalent is an exclusive lock on its Roblox APK inside
~/.var/app/org.vinegarhq.Sober, and it ignores HOME/XDG_* set inside the
sandbox. So each profile is a fake home folder that `flatpak run` itself is
started with: Flatpak then mounts <profile>/.var/app/org.vinegarhq.Sober as
Sober's folder, and every instance gets its own APK, lock, login and settings.

Profiles live in:
    ~/.local/share/sober-multi/<name>/      (fake HOME for that instance)

Usage:
    python3 sober_multi.py launch alt1 alt2 alt3   # start one instance per profile
    python3 sober_multi.py launch alt1 -- roblox://...   # pass args to Sober
    python3 sober_multi.py list                    # show profiles
    python3 sober_multi.py remove alt1             # delete a profile (logs it out)
    python3 sober_multi.py diagnose                # with Sober open: show its
                                                   # locks/sockets/temp files

A new profile copies your normal Sober's config.json and downloaded Roblox
files (so it doesn't download them again), but not your login: log in once
per profile, after that it sticks. Your normal Sober (plain
`flatpak run org.vinegarhq.Sober`) is left untouched and can run alongside.

Each instance's output goes to ~/.local/share/sober-multi/<name>/sober.log.

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
REAL_HOME = os.path.expanduser("~")
APP_DIR = os.path.join(REAL_HOME, ".var", "app", APP_ID)
PROFILES_DIR = os.path.join(REAL_HOME, ".local", "share", "sober-multi")
APP_SUBDIR = os.path.join(".var", "app", APP_ID)  # inside each profile

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
    app = os.path.join(root, APP_SUBDIR)
    new = not os.path.isdir(app)
    if not new:
        return root, False
    os.makedirs(app)
    # Seed from the normal Sober: settings and the downloaded Roblox APKs.
    # Real copies, not hardlinks: Sober's instance lock is on the APK, and a
    # hardlink would share the lock with the original.
    for rel in (("config", "sober", "config.json"), ("data", "sober", "packages")):
        src = os.path.join(APP_DIR, *rel)
        dst = os.path.join(app, *rel)
        if os.path.isfile(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        elif os.path.isdir(src):
            print(f"[{name}] copying Roblox files from your normal Sober...")
            shutil.copytree(src, dst, symlinks=True)
    return root, True


def flatpak_env(fake_home):
    """Host env for `flatpak run` with HOME swapped to the profile, keeping
    everything flatpak itself still needs from the real home."""
    env = dict(os.environ, HOME=fake_home)
    # Sober may be a --user install, which flatpak finds via HOME.
    env.setdefault("FLATPAK_USER_DIR", os.path.join(
        os.environ.get("XDG_DATA_HOME") or os.path.join(REAL_HOME, ".local", "share"), "flatpak"))
    # X11 auth cookie defaults to ~/.Xauthority when XAUTHORITY isn't set.
    xauth = os.path.join(REAL_HOME, ".Xauthority")
    if "XAUTHORITY" not in env and os.path.isfile(xauth):
        env["XAUTHORITY"] = xauth
    return env


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
        log = open(os.path.join(root, "sober.log"), "ab")
        # New session so closing this terminal doesn't kill the instances.
        proc = subprocess.Popen(["flatpak", "run", APP_ID, *extra_args],
                                env=flatpak_env(root), cwd=root,
                                stdout=log, stderr=subprocess.STDOUT,
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


def _find_inodes(wanted):
    """Fill in paths for {(major, minor, inode): None}, searching Sober's
    Flatpak dir first, then the rest of home (same disk only)."""
    def walk(top, skip=None):
        for dirpath, dirnames, filenames in os.walk(top):
            if skip:
                dirnames[:] = [d for d in dirnames if os.path.join(dirpath, d) != skip]
            for name in dirnames + filenames:
                full = os.path.join(dirpath, name)
                try:
                    st = os.lstat(full)
                except OSError:
                    continue
                key = (os.major(st.st_dev), os.minor(st.st_dev), st.st_ino)
                if key in wanted and wanted[key] is None:
                    wanted[key] = full
                    if all(wanted.values()):
                        return True
        return False
    if wanted and not walk(APP_DIR):
        walk(os.path.expanduser("~"), skip=APP_DIR)


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
    held = {}  # (major, minor, inode) of locked files -> path
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
        for l in locks:
            cols = l.split()
            if len(cols) >= 6 and cols[4] == str(pid):
                out.append(f"  lock {l}")
                major, minor, ino = cols[5].split(":")
                if int(major, 16):  # 0 = virtual fs (/proc etc.), skip
                    held[(int(major, 16), int(minor, 16), int(ino))] = None

    out.append("== locked files (Sober hides its fds, so look them up by inode)")
    _find_inodes(held)
    for (major, minor, ino), path in held.items():
        out.append(f"  {major:x}:{minor:02x}:{ino} -> {path or '(not found under home)'}")

    # The sandbox's view of the filesystem: the innermost bwrap we can read.
    root = next((f"/proc/{p}/root" for p in reversed(pids)
                 if os.path.exists(f"/proc/{p}/root/.flatpak-info")), f"/proc/{pids[0]}/root")
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
