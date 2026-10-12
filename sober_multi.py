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

Sober also has a "frozen instance" check: byte-range locks on the time
namespace file (/proc/self/ns/time), which every Flatpak sandbox shares.
So each instance is also started in its own user + time namespace (no root
needed, clocks unchanged), and its own IPC namespace.

Profiles live in:
    ~/.local/share/sober-multi/<name>/      (fake HOME for that instance)

Usage:
    python3 sober_multi.py launch alt1 alt2 alt3   # start one instance per profile
    python3 sober_multi.py launch alt1 -- roblox://...   # pass args to Sober
    python3 sober_multi.py list                    # show profiles
    python3 sober_multi.py remove alt1             # delete a profile (logs it out)
    python3 sober_multi.py diagnose                # with Sober open: show its
                                                   # locks/sockets/temp files
    python3 sober_multi.py trace alt1 alt2         # launch + record Sober's D-Bus

launch options:
    --same-pids         don't move each profile's game process to its own pid
    --share-timens      don't give each instance its own time namespace
    --isolate-runtime   own /tmp and /run/user per profile (experimental)
    --share-ipc         don't give each instance its own IPC namespace

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


REAL_RUNTIME = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
RUNTIME_PROFILES = os.path.join(REAL_RUNTIME, "sober-multi")
# Sockets flatpak hands into the sandbox from the runtime dir.
RUNTIME_LINKS = ("pulse", "pipewire-0", "bus", "at-spi", "dconf")


def fake_runtime(name):
    """Per-profile XDG_RUNTIME_DIR. Flatpak shares /tmp and /run/user/UID
    between all instances of one app ID, keyed off the host runtime dir, so
    a separate one per profile splits those too. Audio/D-Bus sockets are
    symlinked from the real one."""
    rt = os.path.join(RUNTIME_PROFILES, name)
    os.makedirs(rt, mode=0o700, exist_ok=True)
    wayland = [e for e in os.listdir(REAL_RUNTIME) if e.startswith("wayland-")]
    for entry in (*RUNTIME_LINKS, *wayland):
        src, dst = os.path.join(REAL_RUNTIME, entry), os.path.join(rt, entry)
        if os.path.exists(src) and not os.path.lexists(dst):
            os.symlink(src, dst)
    return rt


def flatpak_env(fake_home, runtime_dir=None):
    """Host env for `flatpak run` with HOME swapped to the profile, keeping
    everything flatpak itself still needs from the real home."""
    env = dict(os.environ, HOME=fake_home)
    if runtime_dir:
        env["XDG_RUNTIME_DIR"] = runtime_dir
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={REAL_RUNTIME}/bus")
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


CLONE_NEWUSER, CLONE_NEWTIME = 0x10000000, 0x80


def timens_exec(argv):
    """Exec argv in a new user + time namespace (no root needed). Sober's
    "frozen instance" check holds byte-range locks on the time namespace
    file, which every Flatpak sandbox shares, so two instances collide.
    A fresh time namespace (zero clock offsets, same clocks) gives each
    instance its own. Our uid/gid map to themselves, so files, D-Bus,
    X11 and audio all still see the real user."""
    import ctypes
    uid, gid = os.getuid(), os.getgid()
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.unshare(CLONE_NEWUSER | CLONE_NEWTIME) != 0:
        sys.exit(f"sober_multi: can't create a time namespace: {os.strerror(ctypes.get_errno())}")
    try:
        for path, data in (("setgroups", "deny"), ("uid_map", f"{uid} {uid} 1"),
                           ("gid_map", f"{gid} {gid} 1")):
            with open(f"/proc/self/{path}", "w") as f:
                f.write(data)
    except OSError as e:
        sys.exit(f"sober_multi: can't set up the user namespace: {e}")
    os.execvp(argv[0], argv)


def timens_prefix():
    return [sys.executable, os.path.abspath(__file__), "_timens"]


def check_timens():
    r = subprocess.run([*timens_prefix(), "true"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit((r.stderr.strip() or "time namespace test failed") + "\n"
                 "Your system blocks unprivileged user namespaces (Ubuntu 24.04+ AppArmor\n"
                 "does this). Check: sysctl kernel.apparmor_restrict_unprivileged_userns\n"
                 "Or run with --share-timens (second instance will hit 'frozen instance').")


# Each sandbox numbers its processes from 1, so every instance's game
# process is pid 3 and its window says _NET_WM_PID=3. Sober's watchdog
# looks for "pid 3's" Sober window, finds two, and calls one frozen.
# Burning some pids before exec'ing sober moves each profile's game
# process to its own pid.
PID_STEP = 500
PID_WRAPPER = ('n=0; while [ "$n" -lt "$SOBER_MULTI_PIDS" ]; do /bin/true; n=$((n+1)); done; '
               'exec sober "$@"')


def pid_offset(name):
    """Stable per-profile offset: 500 x (position among profiles + 1)."""
    names = sorted(n for n in os.listdir(PROFILES_DIR)
                   if os.path.isdir(os.path.join(PROFILES_DIR, n)))
    return PID_STEP * (names.index(name) + 1)


def launch(names, extra_args, share_ipc=False, isolate_runtime=False, share_timens=False,
           same_pids=False):
    check_sober()
    if not share_timens:
        check_timens()
    for i, name in enumerate(names):
        if i:
            time.sleep(LAUNCH_GAP)
        root, new = ensure_profile(name)
        log = open(os.path.join(root, "sober.log"), "ab")
        # New session so closing this terminal doesn't kill the instances.
        # Sober shares the host's IPC namespace (SysV shm/semaphores), and a
        # second instance there trips its "frozen instance" check. Give each
        # instance its own.
        flags = [] if share_ipc else ["--unshare=ipc"]
        prefix = [] if share_timens else timens_prefix()
        if same_pids:
            run = [*flags, APP_ID, *extra_args]
        else:
            run = [*flags, f"--env=SOBER_MULTI_PIDS={pid_offset(name)}", "--command=sh",
                   APP_ID, "-c", PID_WRAPPER, "sober", *extra_args]
        proc = subprocess.Popen([*prefix, "flatpak", "run", *run],
                                env=flatpak_env(root, fake_runtime(name) if isolate_runtime else None),
                                cwd=root,
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


def _sh(cmd, env=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10, env=env)
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


TCP_STATES = {"01": "ESTABLISHED", "0A": "LISTEN", "07": "UNCONN"}


def _inet_sockets(netdir):
    """(proto, local addr, state, inode) for our uid's TCP/UDP sockets."""
    import socket
    out = []
    for proto in ("tcp", "tcp6", "udp", "udp6"):
        try:
            with open(f"{netdir}/{proto}") as f:
                rows = f.read().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            c = row.split()
            if len(c) < 10 or c[7] != str(os.getuid()):
                continue
            host, port = c[1].split(":")
            raw = bytes.fromhex(host)
            # /proc stores addresses as host-order 32-bit words.
            raw = b"".join(raw[i:i + 4][::-1] for i in range(0, len(raw), 4))
            fam = socket.AF_INET6 if proto.endswith("6") else socket.AF_INET
            addr = f"{socket.inet_ntop(fam, raw)}:{int(port, 16)}"
            out.append((proto, addr, TCP_STATES.get(c[3], c[3]), c[9]))
    return out


def _ps_envs():
    """`flatpak ps` only sees instances under its XDG_RUNTIME_DIR, so also
    look in each --isolate-runtime profile's."""
    envs = [None]
    if os.path.isdir(RUNTIME_PROFILES):
        envs += [dict(os.environ, XDG_RUNTIME_DIR=os.path.join(RUNTIME_PROFILES, n))
                 for n in sorted(os.listdir(RUNTIME_PROFILES))]
    return envs


def _x11_report():
    """X atoms, selection owners and window properties mentioning Sober:
    the X server is shared by every instance whatever the sandbox does."""
    try:
        from Xlib import X, display, error
    except ImportError:
        return ["  (python-xlib not installed)"]
    out = []
    try:
        d = display.Display()
    except Exception as e:
        return [f"  <{e}>"]
    pat = re.compile(r"sober|roblox|vinegar|sdl", re.I)
    misses, atom = 0, 1
    while misses < 50 and atom < 20000:
        try:
            name = d.get_atom_name(atom)
            misses = 0
            if pat.search(name):
                owner = d.get_selection_owner(atom)
                owned = f" selection owner 0x{owner.id:x}" if owner != X.NONE else ""
                out.append(f"  atom {atom} {name}{owned}")
        except error.BadAtom:
            misses += 1
        atom += 1
    root = d.screen().root
    for prop in root.list_properties():
        name = d.get_atom_name(prop)
        if pat.search(name):
            out.append(f"  root property {name}")
    clients = root.get_full_property(d.intern_atom("_NET_CLIENT_LIST"), X.AnyPropertyType)
    for wid in (clients.value if clients else []):
        w = d.create_resource_object("window", wid)
        try:
            cls = w.get_wm_class() or ()
            if not any(pat.search(c) for c in cls):
                continue
            pid = w.get_full_property(d.intern_atom("_NET_WM_PID"), X.AnyPropertyType)
            props = sorted(d.get_atom_name(p) for p in w.list_properties())
            out.append(f"  window 0x{wid:x} class={cls} pid={pid.value[0] if pid else '?'}")
            out.append(f"    props: {' '.join(props)}")
        except error.XError:
            pass
    return out or ["  (nothing matching)"]


def diagnose():
    """Dump how a running Sober could be spotting other instances: open
    files, file locks, unix sockets, D-Bus names, temp/runtime dirs."""
    out = ["== flatpak ps", _sh(["flatpak", "ps", "--columns=instance,pid,child-pid,application"])]
    out += ["== permissions", _sh(["flatpak", "info", "--show-permissions", APP_ID])]
    out += ["== dbus names", "\n".join(l for l in _sh(["busctl", "--user", "list", "--no-legend"]).splitlines()
                                       if "sober" in l.lower() or "vinegar" in l.lower()) or "(none matching)"]

    pids = []
    for env in _ps_envs():
        for line in _sh(["flatpak", "ps", "--columns=pid,application"], env).splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == APP_ID and parts[0].isdigit():
                pids += _children(int(parts[0]))
    if not pids:
        out.append("!! No running Sober: this is a baseline to compare against.")

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
        if comm == "bwrap":
            # Shows whether --unshare=ipc reached the sandbox, and which
            # namespaces instances share (same ns id = shared).
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as f:
                    argv = f.read().decode(errors="replace").split("\0")
                out.append("  sandbox flags: " + " ".join(a for a in argv if a.startswith("--unshare")))
            except OSError:
                pass
            for ns in ("ipc", "net", "pid", "time"):
                try:
                    out.append(f"  ns {os.readlink(f'/proc/{pid}/ns/{ns}')}")
                except OSError:
                    pass
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
        for proto, addr, state, ino in _inet_sockets(f"/proc/{pid}/net"):
            if ino in socks:
                out.append(f"  {proto} socket {addr} {state}")
        for l in locks:
            cols = l.split()
            if len(cols) >= 6 and cols[4] == str(pid):
                out.append(f"  lock {l}")
                major, minor, ino = cols[5].split(":")
                if int(major, 16):  # 0 = virtual fs (/proc etc.), skip
                    held[(int(major, 16), int(minor, 16), int(ino))] = None

    if pids:
        out.append("== locked files (Sober hides its fds, so look them up by inode)")
        _find_inodes(held)
        for (major, minor, ino), path in held.items():
            out.append(f"  {major:x}:{minor:02x}:{ino} -> {path or '(not found under home)'}")

    # Shared with the host unless --unshare=ipc / no network: SysV IPC and
    # abstract unix sockets are where a cross-instance check could hide.
    for kind in ("shm", "sem", "msg"):
        try:
            with open(f"/proc/sysvipc/{kind}") as f:
                rows = f.read().splitlines()
        except OSError as e:
            rows = [f"<{e.strerror}>"]
        out += [f"== sysv {kind} (host IPC namespace)"] + [f"  {r}" for r in rows]
    try:
        with open(f"/proc/{pids[-1] if pids else 'self'}/net/unix") as f:
            names = sorted({c[7] for c in (l.split() for l in f.read().splitlines()[1:])
                            if len(c) >= 8 and c[7].startswith("@")})
    except OSError as e:
        names = [f"<{e.strerror}>"]
    out += ["== abstract unix sockets (shared network namespace)"] + [f"  {n}" for n in names]
    # Ports every instance shares. Compare with the closed baseline.
    net = f"/proc/{pids[-1]}/net" if pids else "/proc/self/net"
    listen = sorted({f"{p} {a} {st}" for p, a, st, _ in _inet_sockets(net)
                     if st in ("LISTEN", "UNCONN")})
    out += ["== listening TCP / bound UDP ports (shared network namespace)"]
    out += [f"  {l}" for l in listen] or ["  (none)"]
    # Lock kinds not tied to a pid above: OFD locks (pid -1) and any lock
    # on a namespace file (nsfs, like the time namespace).
    nsfs = {os.stat(f"/proc/self/ns/{n}").st_dev for n in ("time", "pid")}
    nsdevs = {f"{os.major(d):02x}:{os.minor(d):02x}" for d in nsfs}
    odd = [l for l in locks if "OFDLCK" in l or
           any(f" {d}:" in l for d in nsdevs)]
    out += ["== OFD / namespace-file locks"]
    out += [f"  {l}" for l in odd] or ["  (none)"]
    out += ["== X11 (shared by all instances)"] + _x11_report()
    if pids:
        # The sandbox's view of the filesystem: the innermost bwrap we can read.
        root = next((f"/proc/{p}/root" for p in reversed(pids)
                     if os.path.exists(f"/proc/{p}/root/.flatpak-info")), f"/proc/{pids[0]}/root")
        out += ["== sandbox /dev/shm"] + _ls(f"{root}/dev/shm")
        out += [f"== sandbox /tmp"] + _ls(f"{root}/tmp")
        out += [f"== sandbox /run/user/{os.getuid()}"] + _ls(f"{root}/run/user/{os.getuid()}")
        out += [f"== sandbox /run/flatpak"] + _ls(f"{root}/run/flatpak", depth=1)

    report = "\n".join(out)
    name = "sober_multi_diagnose.txt" if pids else "sober_multi_diagnose_closed.txt"
    path = os.path.join(REAL_HOME, name)
    with open(path, "w") as f:
        f.write(report + "\n")
    print(report)
    print(f"\nSaved to {path}")


def _proxy_pids():
    pids = set()
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/comm") as f:
                    if f.read().strip() == "xdg-dbus-proxy":
                        pids.add(int(d))
            except OSError:
                pass
    return pids


def trace(names, seconds):
    """Record Sober's session D-Bus traffic while launching profiles.
    Flatpak routes each sandbox's D-Bus through its own xdg-dbus-proxy, so
    proxies that appear after we start belong to Sober; their bus
    connections are Sober's. Only those messages are kept."""
    if shutil.which("dbus-monitor") is None:
        sys.exit("dbus-monitor not found: sudo apt install dbus-bin")
    old_proxies = _proxy_pids()
    raw = subprocess.Popen(["dbus-monitor", "--session"], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True)
    lines = []
    import threading
    reader = threading.Thread(target=lambda: lines.extend(raw.stdout), daemon=True)
    reader.start()
    time.sleep(1.0)

    launch(names, [])
    sober_conns = {}  # unique bus name -> busctl line
    end = time.time() + seconds
    print(f"Recording D-Bus for {seconds}s. Leave the windows alone until it says done...")
    while time.time() < end:
        new = _proxy_pids() - old_proxies
        for l in _sh(["busctl", "--user", "list", "--no-legend"]).splitlines():
            cols = l.split()
            if len(cols) >= 2 and cols[1].isdigit() and int(cols[1]) in new:
                sober_conns.setdefault(cols[0], l)
        time.sleep(1.0)
    raw.terminate()
    reader.join(timeout=3)

    # dbus-monitor: a message is a header line at column 0 plus indented args.
    blocks, cur = [], []
    for l in lines:
        if l and not l[0].isspace() and cur:
            blocks.append(cur)
            cur = []
        cur.append(l.rstrip("\n"))
    if cur:
        blocks.append(cur)
    keep = []
    for b in blocks:
        head = b[0]
        m_s = re.search(r"sender=(\S+)", head)
        m_d = re.search(r"destination=(\S+)", head)
        ends = {m.group(1) for m in (m_s, m_d) if m}
        text = "\n".join(b)
        if ends & set(sober_conns) or "vinegarhq" in text.lower():
            keep.append(text)

    out = ["== Sober bus connections (via its xdg-dbus-proxy)"]
    out += [f"  {l}" for l in sober_conns.values()] or ["  (none found)"]
    out += [f"== Sober D-Bus messages ({len(keep)} of {len(blocks)} recorded)"] + keep
    if len(out) > 6000:
        out = out[:6000] + ["... (cut)"]
    path = os.path.join(REAL_HOME, "sober_multi_trace.txt")
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")
    print(f"Done. Saved to {path}")


def main():
    p = argparse.ArgumentParser(description="Run multiple Sober instances, one per profile.")
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("launch", help="start one instance per profile name")
    pl.add_argument("names", nargs="+",
                    help="profile names; args after -- go to Sober (e.g. a roblox:// link)")
    pl.add_argument("--share-ipc", action="store_true",
                    help="don't give instances their own IPC namespace (if graphics break)")
    pl.add_argument("--same-pids", action="store_true",
                    help="don't shift each profile's game process to its own pid")
    pl.add_argument("--share-timens", action="store_true",
                    help="don't give each instance its own time namespace")
    pl.add_argument("--isolate-runtime", action="store_true",
                    help="give each profile its own /tmp and /run/user (experimental)")
    sub.add_parser("list", help="list profiles")
    pr = sub.add_parser("remove", help="delete profiles")
    pr.add_argument("names", nargs="+")
    sub.add_parser("diagnose", help="with Sober running, dump how it detects other instances")
    pt = sub.add_parser("trace", help="launch profiles and record Sober's D-Bus traffic")
    pt.add_argument("names", nargs="*", default=["alt1", "alt2"])
    pt.add_argument("--seconds", type=int, default=60)

    if sys.argv[1:2] == ["_timens"]:  # internal: see timens_exec()
        timens_exec(sys.argv[2:])
    # argparse can't split "names... -- args" on its own.
    argv = sys.argv[1:]
    extra = []
    if "--" in argv:
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    args = p.parse_args(argv)

    if args.cmd == "launch":
        launch(args.names, extra, args.share_ipc, args.isolate_runtime, args.share_timens,
               args.same_pids)
    elif args.cmd == "list":
        list_profiles()
    elif args.cmd == "remove":
        remove(args.names)
    elif args.cmd == "diagnose":
        diagnose()
    elif args.cmd == "trace":
        trace(args.names, args.seconds)


if __name__ == "__main__":
    main()
