#!/bin/sh
# GL.iNet Router Screen Saver - Uninstall. Mac: double-click it. Linux: python3 uninstall-screen-saver.py (or: sh uninstall-screen-saver.py)
''''[ "$(uname -s)" = Darwin ] && [ "$(command -v python3)" = /usr/bin/python3 ] && ! xcode-select -p >/dev/null 2>&1 && { echo; echo '  This needs Python 3, which comes with the free Command Line Tools from Apple.'; echo '  Your Mac will offer to install them now: click Install, wait until it has finished, then double-click this file again.'; xcode-select --install >/dev/null 2>&1; echo; printf '  Press Enter to close this window. '; read -r _; exit 1; } #'''
''''command -v python3 >/dev/null 2>&1 || { echo; echo '  This needs Python 3. Install it (for example: sudo apt install python3), then run this again.'; exit 1; } #'''
''''exec python3 "$0" "$@" #'''
"""Studio Link for macOS and Linux (Windows: Studio-Link.cmd). Standard library only.

Lets Motion Studio, in your browser, send animations to your router and show what is
on it. Start it, type your router's admin password once when it asks, and leave the
terminal open.

It listens on THIS computer only (127.0.0.1), so nothing else on your network can
reach it. The password is kept in memory only while this window is open (never written
anywhere, never given to the web page) and is handed to ssh through a small helper
program, so nothing asks again.

    python3 studio-link.py [--port 8791] [--router 192.168.8.1] [--key FILE] [--dry-run]
                           [--no-browser] [--forget]

The first time, it also puts the screen saver on your router if it is not there yet (press
Enter to agree), and offers to remember this computer so you never type the password again
(--forget undoes that). It then opens Motion Studio in your browser.

Who may use it: only a page served from the Motion Studio site (or localhost) AND that
holds this run's secret token. The token is made fresh each time Studio Link starts and is
handed to the page in the address Studio Link opens, so other websites, other programs and
other users on this computer cannot drive it. To allow another site (for example your own
fork's GitHub Pages address) set STUDIO_LINK_ORIGINS to a comma-separated list.
"""
import argparse
import atexit
import getpass
import hmac
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

# The animation-file reader from tools/bea2.py, folded in so this is a single file.
import types  # noqa: E402
bea2 = types.ModuleType("bea2")
exec(compile('#!/usr/bin/env python3\n"""Convert between the two .bea animation formats. Standard library only.\n\n    python3 bea2.py encode IN.bea OUT.bea     BEA1 (full frames) -> BEA2 (changes only, much smaller)\n    python3 bea2.py decode IN.bea OUT.bea     BEA2 -> BEA1\n    python3 bea2.py info   FILE.bea           show the format, size and how well it packs\n\nThe format is described in docs/BEA-FORMAT.md. Both formats play on the router;\nBEA2 is just smaller (an animation that only moves a small part of the screen\nshrinks by 10x or more) and writes less to the display.\n"""\nimport struct\nimport sys\n\nFRAME_BYTES = 43168\nHEADER = 12\nGAP = 12            # changed bytes closer together than this are merged into one span\nSPAN_COST = 6       # u32 offset + u16 length in front of every span\nFULL, DELTA, HOLD = 0, 1, 2\n\n\ndef read_header(data):\n    if len(data) < HEADER:\n        sys.exit("not a .bea file: shorter than the 12-byte header")\n    magic = data[:4]\n    if magic not in (b"BEA1", b"BEA2"):\n        sys.exit("not a .bea file: bad magic %r" % magic)\n    fps, records, frame_bytes = struct.unpack_from("<HHI", data, 4)\n    if frame_bytes != FRAME_BYTES:\n        sys.exit("frame size is %d bytes, this display needs %d" % (frame_bytes, FRAME_BYTES))\n    return magic, fps, records\n\n\ndef read_bea1(data):\n    """-> (fps, [(run, frame_bytes), ...])"""\n    _, fps, records = read_header(data)\n    if len(data) != HEADER + records * (2 + FRAME_BYTES):\n        sys.exit("BEA1 size does not match its header")\n    frames, pos = [], HEADER\n    for _ in range(records):\n        (run,) = struct.unpack_from("<H", data, pos)\n        frames.append((run, data[pos + 2:pos + 2 + FRAME_BYTES]))\n        pos += 2 + FRAME_BYTES\n    return fps, frames\n\n\ndef spans_between(prev, cur):\n    """Byte ranges (offset, bytes) where cur differs from prev, nearby ones merged."""\n    if prev == cur:\n        return []\n    out, i, n = [], 0, FRAME_BYTES\n    while i < n:\n        if prev[i] == cur[i]:\n            i += 1\n            continue\n        start = i\n        last = i\n        i += 1\n        while i < n and i - last <= GAP:\n            if prev[i] != cur[i]:\n                last = i\n            i += 1\n        out.append((start, cur[start:last + 1]))\n        i = last + 1\n    # A span may not be longer than a u16.\n    split = []\n    for off, blob in out:\n        while len(blob) > 65535:\n            split.append((off, blob[:65535]))\n            off, blob = off + 65535, blob[65535:]\n        split.append((off, blob))\n    return split\n\n\ndef record(run, kind, payload=b""):\n    return struct.pack("<HBI", run, kind, len(payload)) + payload\n\n\ndef encode(src, dst):\n    data = open(src, "rb").read()\n    magic, _, _ = read_header(data)\n    if magic == b"BEA2":\n        sys.exit("already BEA2")\n    fps, frames = read_bea1(data)\n    recs, prev = [], None\n    for run, frame in frames:\n        if prev is None:\n            recs.append([run, FULL, frame])\n        else:\n            spans = spans_between(prev, frame)\n            if not spans:\n                # identical to the previous frame: just hold it longer\n                if recs[-1][0] + run <= 65535:\n                    recs[-1][0] += run\n                else:\n                    recs.append([run, HOLD, b""])\n            else:\n                payload = struct.pack("<H", len(spans)) + b"".join(\n                    struct.pack("<IH", off, len(b)) + b for off, b in spans)\n                if len(spans) > 65535 or len(payload) >= FRAME_BYTES:\n                    recs.append([run, FULL, frame])\n                else:\n                    recs.append([run, DELTA, payload])\n        prev = frame\n    body = b"".join(record(r, k, p) for r, k, p in recs)\n    open(dst, "wb").write(b"BEA2" + struct.pack("<HHI", fps, len(recs), FRAME_BYTES) + body)\n    print("wrote %s: %d -> %d bytes (%.1fx smaller), %d records" % (\n        dst, len(data), HEADER + len(body), len(data) / (HEADER + len(body)), len(recs)))\n\n\ndef read_bea2(data):\n    """-> (fps, [(run, kind, payload), ...]) with the structure fully validated."""\n    _, fps, records = read_header(data)\n    recs, pos = [], HEADER\n    for i in range(records):\n        if pos + 7 > len(data):\n            sys.exit("truncated at record %d" % (i + 1))\n        run, kind, plen = struct.unpack_from("<HBI", data, pos)\n        pos += 7\n        if pos + plen > len(data):\n            sys.exit("truncated inside record %d" % (i + 1))\n        recs.append((run, kind, data[pos:pos + plen]))\n        pos += plen\n    if pos != len(data):\n        sys.exit("%d unexpected trailing bytes" % (len(data) - pos))\n    return fps, recs\n\n\ndef apply_delta(frame, payload):\n    frame = bytearray(frame)\n    (n,) = struct.unpack_from("<H", payload, 0)\n    pos = 2\n    for _ in range(n):\n        off, ln = struct.unpack_from("<IH", payload, pos)\n        pos += 6\n        frame[off:off + ln] = payload[pos:pos + ln]\n        pos += ln\n    return bytes(frame)\n\n\ndef decode(src, dst):\n    data = open(src, "rb").read()\n    magic, _, _ = read_header(data)\n    if magic == b"BEA1":\n        sys.exit("already BEA1")\n    fps, recs = read_bea2(data)\n    frames, cur = [], None\n    for run, kind, payload in recs:\n        if kind == FULL:\n            cur = payload\n        elif kind == DELTA:\n            cur = apply_delta(cur, payload)\n        # HOLD: same picture\n        frames.append((run, cur))\n    with open(dst, "wb") as f:\n        f.write(b"BEA1" + struct.pack("<HHI", fps, len(frames), FRAME_BYTES))\n        for run, frame in frames:\n            f.write(struct.pack("<H", run) + frame)\n    print("wrote %s: %d frames" % (dst, len(frames)))\n\n\ndef info(path):\n    data = open(path, "rb").read()\n    magic, fps, records = read_header(data)\n    if magic == b"BEA1":\n        _, frames = read_bea1(data)\n        ticks = sum(r for r, _ in frames)\n    else:\n        _, recs = read_bea2(data)\n        ticks = sum(r for r, _, _ in recs)\n        kinds = [k for _, k, _ in recs]\n        print("records: %d full, %d delta, %d hold" % (kinds.count(FULL), kinds.count(DELTA), kinds.count(HOLD)))\n    print("%s  %d records, %d ticks at %d fps = %.1f s per loop, %d bytes"\n          % (magic.decode(), records, ticks, fps, ticks / fps, len(data)))\n\n\nif __name__ == "__main__":\n    if len(sys.argv) >= 3 and sys.argv[1] == "info":\n        info(sys.argv[2])\n    elif len(sys.argv) == 4 and sys.argv[1] in ("encode", "decode"):\n        {"encode": encode, "decode": decode}[sys.argv[1]](sys.argv[2], sys.argv[3])\n    else:\n        sys.exit(__doc__)\n', "bea2.py", "exec"), bea2.__dict__)

FRAME_BYTES = 43168
MAX_BODY = 32 * 1024 * 1024
MAX_SECONDS = 25

# Test hooks (a stand-in ssh, answering "yes" for you) only work when BE3600_TESTING is set,
# so nothing in a normal environment can switch them on by accident.
TESTING = bool(os.environ.get("BE3600_TESTING"))

# "null" (a sandboxed page or a local file) is NOT accepted by default: any website can
# produce that origin. Add it yourself in STUDIO_LINK_ORIGINS if you open a local copy.
ORIGIN_SHAPE = re.compile(r"^(null|https?://[A-Za-z0-9.-]+(:\d+)?)$")
ALLOWED_ORIGINS = {"https://cristoxd73.github.io"}
ALLOWED_ORIGINS.update(o.strip() for o in os.environ.get("STUDIO_LINK_ORIGINS", "").split(",")
                       if ORIGIN_SHAPE.match(o.strip()))
LOCAL_ORIGIN = re.compile(r"^http://(localhost|127\.0\.0\.1)(:\d+)?$")
LOCAL_HOST = re.compile(r"^(localhost|127\.0\.0\.1)(:\d+)?$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
HOST_OK = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")   # an address or name, never an option
TOKEN_HEADER = "X-Studio-Token"

STATE_FILE = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                          "be3600-screensaver", "router")
KEY_FILE = os.path.join(os.path.dirname(STATE_FILE), "studio-key")     # the "remember this computer" key
KEY_COMMENT_SHAPE = re.compile(r"^be3600-studio-link-[A-Za-z0-9_-]{1,30}$")


def computer_name(platform=None):
    """A name for this computer that stays put. On a Mac the network name (gethostname) follows
    whatever network it joins ("MacBook-Pro.local" at home, "dhcp-10-2-3-4" at work), so the
    Sharing name is asked for instead; the key's label and its keychain entry depend on it."""
    if (platform or sys.platform) == "darwin":
        for what in ("LocalHostName", "ComputerName"):
            try:
                r = subprocess.run(["scutil", "--get", what], capture_output=True, text=True, timeout=3)
                if r.returncode == 0 and r.stdout.strip():
                    return r.stdout.strip()
            except (OSError, subprocess.SubprocessError):
                pass
    return socket.gethostname() or "computer"


def fresh_key_comment(platform=None):
    return "be3600-studio-link-" + (re.sub(r"[^A-Za-z0-9_-]+", "-", computer_name(platform))[:30] or "computer")


def key_comment(pub_path=None, platform=None):
    """The label of this computer's remembered key: the one it was made with (read back from the
    .pub file, so a later change of name cannot orphan it), or a fresh one for a new key."""
    try:
        with open(pub_path or KEY_FILE + ".pub") as f:
            parts = f.read().split()
        if len(parts) >= 3 and KEY_COMMENT_SHAPE.match(parts[2]):
            return parts[2]
    except OSError:
        pass
    return fresh_key_comment(platform)


KEY_COMMENT = key_comment()
VAULT_SERVICE = "be3600-studio-link"
AUTH_KEYS = "/etc/dropbear/authorized_keys"
DEFAULT_STUDIO_URL = "https://cristoxd73.github.io/GL.iNet-Router-Screen-Saver-BE3600/studio/"
STUDIO_URL = os.environ.get("STUDIO_LINK_URL") or DEFAULT_STUDIO_URL
if not re.match(r"^(https://[A-Za-z0-9.-]+(:\d+)?/[^\s#]*|http://(localhost|127\.0\.0\.1)(:\d+)?/[^\s#]*)$", STUDIO_URL):
    STUDIO_URL = DEFAULT_STUDIO_URL                 # never open file:, javascript: or anything odd

# In the single-file download the files the router needs are packed in here as
# (version id, base64 of a .tar.gz); from a clone of the repo they are packed on the fly.
PAYLOAD = None  # __PAYLOAD__

# Motion Studio itself (studio/index.html, fan.html and what they load), packed the same way, so
# Studio Link can serve it from this computer at http://127.0.0.1:PORT/. Safari, the Mac's own
# browser, never lets an https page talk to http://127.0.0.1 (WebKit treats it as mixed content),
# so on a Mac the website cannot reach Studio Link; the copy served here can, in any browser.
STUDIO = None  # __STUDIO__

# The one-click downloads are this same program with what it does set here: "install" or
# "uninstall" (tools/build_downloads.py). None: Studio Link, or whatever the command line says.
ACTION = "uninstall"
STUDIO_FILES = ("index.html", "fan.html", "shared.js", "shared.css", "OPEN_SOURCE.md")
STUDIO_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".md": "text/plain; charset=utf-8"}


class Config:
    local_studio = False                           # open the copy of Motion Studio served here
    port = 8791
    router = None
    key = None
    key_pass = None                                # the remembered key's passphrase (from the keychain), memory only
    dry_run = False
    ssh = (os.environ.get("BE3600_SSH") or "ssh") if TESTING else "ssh"    # test hook: a stand-in for ssh
    password = None                                # kept in memory only
    askpass = None
    sent = 0
    lock = threading.Lock()
    found = None
    token = None                                   # this run's secret; the page must present it
    paired = False                                 # has a page proved it holds the token yet?
    stale_key = False                              # a remembered key is here, but it no longer gets in


def origin_allowed(origin):
    """A browser page must come from an allowed origin. A request with no Origin header is
    not a browser page; it is let through here and still has to hold the token."""
    if not origin:
        return True
    return origin in ALLOWED_ORIGINS or bool(LOCAL_ORIGIN.match(origin))


def token_ok(given):
    return bool(Config.token) and bool(given) and hmac.compare_digest(str(given), Config.token)


def valid_host(h):
    return bool(h) and bool(HOST_OK.match(h))


def lib_name(raw):
    """Letters, digits . _ - only (safe inside a shell command), at most 40 characters."""
    n = re.sub(r"[^A-Za-z0-9._-]+", "-", raw or "")
    if n.lower().endswith(".bea"):
        n = n[:-4]
    n = n.strip("-")
    return (n or "animation")[:40]


def check_bea(data):
    """-> (None, seconds) if usable, else (plain-words reason, None)."""
    if len(data) < 12:
        return "That file is too small to be a .bea animation.", None
    magic = data[:4]
    if magic not in (b"BEA1", b"BEA2"):
        return "That is not a .bea animation (it does not start with the BEA1 or BEA2 marker).", None
    import struct
    fps, records, frame_bytes = struct.unpack_from("<HHI", data, 4)
    if frame_bytes != FRAME_BYTES:
        return "Its frames are %d bytes; this display needs %d (76 x 284 pixels)." % (frame_bytes, FRAME_BYTES), None
    if not 1 <= fps <= 24:
        return "Its speed is %d frames per second; it must be 1 to 24." % fps, None
    if records < 1:
        return "It has no frames.", None
    try:
        if magic == b"BEA1":
            _, frames = bea2.read_bea1(data)
            ticks = sum(run for run, _ in frames)
        else:
            _, recs = bea2.read_bea2(data)
            ticks = sum(run for run, _, _ in recs)
    except SystemExit:
        return "Its size does not match its frames. It may be incomplete.", None
    seconds = ticks / fps
    if seconds > MAX_SECONDS:
        return "It loops for %.1f seconds; the limit is %d seconds." % (seconds, MAX_SECONDS), None
    return None, seconds


def ssh_open(ip, timeout=1.5):
    if TESTING and "BE3600_REACHABLE" in os.environ:        # test hook: which addresses "answer"
        return ip in os.environ["BE3600_REACHABLE"].split(",")
    try:
        with socket.create_connection((ip, 22), timeout=timeout):
            return True
    except OSError:
        return False


def local_network_hint(platform=None):
    """On macOS 15 and later a program needs the Local Network permission to reach the router;
    without it every address on the network fails at once with "No route to host"."""
    if (platform or sys.platform) != "darwin":
        return ""
    return ("On a Mac, also check System Settings > Privacy & Security > Local Network: the app you run this "
            "in (Terminal, iTerm, VS Code...) must be switched on there, or it cannot reach the router.")


def default_gateway():
    if TESTING and "BE3600_GATEWAY" in os.environ:          # test hook: this computer's gateway ("none": no gateway)
        return os.environ["BE3600_GATEWAY"].replace("none", "") or None
    for cmd, pat in ((["ip", "route", "show", "default"], r"default via (\S+)"),
                     (["route", "-n", "get", "default"], r"gateway:\s*(\S+)")):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
            m = re.search(pat, out)
            if m:
                return m.group(1)
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def find_router():
    if Config.router:
        return Config.router
    if Config.found and ssh_open(Config.found):
        return Config.found
    for c in router_candidates():
        if ssh_open(c):
            Config.found = c
            return c
    return None


def router_candidates():
    """Where to look, in order: the address that worked last time, this computer's gateway,
    and GL.iNet's factory address. Only a few well-known places are probed, never a scan."""
    cands = []
    try:
        with open(STATE_FILE) as f:
            cands.append(f.readline().strip())
    except OSError:
        pass
    cands += [default_gateway(), "192.168.8.1"]
    # Anything that is not a plain address or name (for example text that looks like an ssh
    # option) is dropped, whether it came from the saved file or from the network.
    return list(dict.fromkeys(x for x in cands if valid_host(x)))


def remember_router(ip):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            f.write(ip + "\n")
    except OSError:
        pass


def say(kind, text):
    print("  [%s] %s" % (kind, text), flush=True)


# ---------------------------------------------------------------------------------------
# Talking to the router
# ---------------------------------------------------------------------------------------

def quiet_login():
    """True if ssh can log in without asking anyone (a key, or the password we hold)."""
    return bool(Config.key) or Config.password is not None


def make_askpass():
    """A tiny program ssh runs to ask for the password. It prints the one held in this
    process's environment; the password itself is never written into the file."""
    d = tempfile.mkdtemp(prefix="be3600-studio-")
    path = os.path.join(d, "askpass.sh")
    with open(path, "w") as f:
        f.write('#!/bin/sh\nprintf \'%s\\n\' "$BE3600_STUDIO_PW"\n')
    os.chmod(path, 0o700)
    atexit.register(shutil.rmtree, d, True)
    return path


def run_ssh(ip, remote, stdin_path=None):
    """Run one command on the router. -> (exit code, its output).

    With a key or a held password nothing is asked and the router's output comes back.
    Otherwise ssh asks for the password on this terminal, as Set-Animation does.
    A secret (the password, or the remembered key's passphrase) goes only into the
    environment of this one ssh process, through the askpass helper, never onto a command line."""
    args = [Config.ssh]
    env = dict(os.environ)
    quiet = quiet_login()
    secret = None
    if Config.key:
        args += ["-i", Config.key, "-o", "IdentitiesOnly=yes"]
        if Config.key_pass is not None:
            args += ["-o", "NumberOfPasswordPrompts=1"]          # ssh asks for the key's passphrase
            secret = Config.key_pass
        else:
            args += ["-o", "BatchMode=yes"]
    elif Config.password is not None:
        args += ["-o", "NumberOfPasswordPrompts=1"]
        secret = Config.password
    if secret is not None:
        if not Config.askpass:
            Config.askpass = make_askpass()
        env.update(SSH_ASKPASS=Config.askpass, SSH_ASKPASS_REQUIRE="force", BE3600_STUDIO_PW=secret)
        env.setdefault("DISPLAY", "studio-link")
    args += ["-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10", "root@" + ip, remote]

    stdin = open(stdin_path, "rb") if stdin_path else open(os.devnull, "rb")
    try:
        r = subprocess.run(args, stdin=stdin, env=env, capture_output=quiet, text=True,
                           start_new_session=quiet)      # no terminal: askpass is used, not a prompt
    finally:
        stdin.close()
    out = ((r.stdout or "") + (r.stderr or "")) if quiet else ""
    return r.returncode, out


def last_line(text):
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    return lines[-1] if lines else ""


def parse_library(text):
    items, limits = [], {"max": 3, "maxSeconds": 25}
    seen = False
    for line in (text or "").splitlines():
        p = line.split("\t")
        if p[0] in ("*", "-") and len(p) >= 4:
            try:
                items.append({"name": p[1], "bytes": int(p[2]), "seconds": float(p[3]), "active": p[0] == "*"})
            except ValueError:
                pass
        elif p[0] == "limits" and len(p) >= 3:
            try:
                limits = {"max": int(p[1]), "maxSeconds": int(p[2])}
                seen = True
            except ValueError:
                pass
    return (items, limits) if seen else (None, None)


def login_failed(out):
    return "Permission denied" in out


def get_library():
    if Config.dry_run:
        return 200, "OK", {"ok": True, "dryRun": True, "max": 3, "maxSeconds": MAX_SECONDS, "items": []}
    if not quiet_login():
        return 200, "OK", {"ok": False, "needPassword": True,
                           "message": "Studio Link was started without your router password, so it cannot look at "
                                      "your animations. Close it and start it again, and type the password when it asks."}
    ip = find_router()
    if not ip:
        return 502, "Bad Gateway", {"ok": False, "message": "Could not find your router."}
    code, out = run_ssh(ip, "be3600-anim list --plain")
    if code == 255 or login_failed(out):
        return 502, "Bad Gateway", {"ok": False, "message": "Could not log in to the router. Is the password right?"}
    items, limits = parse_library(out)
    if items is None:
        return 502, "Bad Gateway", {"ok": False, "message": "The screen saver on the router is missing or out of date. "
                                    "Close Studio Link and start it again; it will offer to update it."}
    return 200, "OK", {"ok": True, "router": ip, "max": limits["max"], "maxSeconds": limits["maxSeconds"], "items": items}


def router_command(verb, name):
    """be3600-anim use NAME / remove NAME. -> (status, reason, dict)."""
    if not SAFE_NAME.match(name or ""):
        return 400, "Bad Request", {"ok": False, "message": "That is not a valid animation name."}
    if Config.dry_run:
        return 200, "OK", {"ok": True, "dryRun": True, "message": "Dry run: nothing was changed."}
    ip = find_router()
    if not ip:
        return 502, "Bad Gateway", {"ok": False, "message": "Could not find your router."}
    say(verb, "%s '%s'" % (verb, name))
    code, out = run_ssh(ip, "be3600-anim %s %s" % (verb, name))
    if code == 255 or login_failed(out):
        return 502, "Bad Gateway", {"ok": False, "message": "Could not log in to the router."}
    if code != 0:
        return 422, "Unprocessable Entity", {"ok": False, "message": last_line(out) or "The router refused."}
    return 200, "OK", {"ok": True, "message": last_line(out) or "Done."}


SAFE_STEPS = re.compile(r"^\d{1,3}:\d{1,6}( \d{1,3}:\d{1,6}){0,31}$")


def send_chime(name, steps):
    """Fan Studio's Send to router: be3600-fan save NAME "STEPS". -> (status, reason, dict)."""
    steps = " ".join((steps or "").split())
    if not SAFE_NAME.match(name or ""):
        return 400, "Bad Request", {"ok": False, "message": "That is not a valid chime name."}
    if not SAFE_STEPS.match(steps):
        return 400, "Bad Request", {"ok": False, "message": "A chime is a list of duty:milliseconds steps, nothing else."}
    if Config.dry_run:
        return 200, "OK", {"ok": True, "dryRun": True, "message": "Dry run: nothing was sent."}
    ip = find_router()
    if not ip:
        return 502, "Bad Gateway", {"ok": False, "message": "Could not find your router."}
    say("send", "chime '%s' to %s" % (name, ip))
    # steps has already been checked to be digits, colons and single spaces, so it cannot escape the quotes.
    code, out = run_ssh(ip, "be3600-fan save %s '%s'" % (name, steps))
    if code == 255 or login_failed(out):
        return 502, "Bad Gateway", {"ok": False, "message": "Could not log in to the router."}
    if code != 0:
        return 422, "Unprocessable Entity", {"ok": False, "message": last_line(out) or
                                             "The router refused it. It keeps at most 8 chimes of your own."}
    say("ok", "saved '%s' on the router" % name)
    return 200, "OK", {"ok": True, "message": "Saved on the router as '%s'. Hear it from the chimes page, "
                                              "or set FAN_CHIME_TAPS=%s for five taps." % (name, name)}


SAFE_PAGES = re.compile(r"^[A-Za-z0-9:._-]{1,40}( [A-Za-z0-9:._-]{1,40}){0,40}$")


def parse_pages(text):
    """be3600-anim pages --plain -> (order, [{name, on, about}]) or (None, None)."""
    order, pages = None, []
    for line in (text or "").splitlines():
        p = line.rstrip("\r").split("\t")
        if p[0] == "order" and len(p) >= 2:
            order = p[1].split()
        elif p[0] in ("*", "-") and len(p) >= 3 and SAFE_NAME.match(p[1]):
            pages.append({"name": p[1], "on": p[0] == "*", "about": p[2]})
    return (order, pages) if order is not None and pages else (None, None)


def get_pages():
    """The screen pages the router can show, and which are on (Motion Studio's widget list)."""
    if Config.dry_run:
        return 200, "OK", {"ok": True, "dryRun": True, "order": ["animations"],
                           "pages": [{"name": "animations", "on": True, "about": "your saved animations"}]}
    if not quiet_login():
        return 200, "OK", {"ok": False, "needPassword": True,
                           "message": "Studio Link was started without your router password, so it cannot look at the pages."}
    ip = find_router()
    if not ip:
        return 502, "Bad Gateway", {"ok": False, "message": "Could not find your router."}
    code, out = run_ssh(ip, "be3600-anim pages --plain")
    if code == 255 or login_failed(out):
        return 502, "Bad Gateway", {"ok": False, "message": "Could not log in to the router."}
    order, pages = parse_pages(out)
    if order is None:
        return 502, "Bad Gateway", {"ok": False, "message": "The screen saver on the router is too old to choose pages from here. "
                                    "Close Studio Link and start it again; it will offer to update it."}
    return 200, "OK", {"ok": True, "order": order, "pages": pages}


def set_pages(text):
    """be3600-anim pages set "animations clock ..." -> (status, reason, dict)."""
    names = " ".join((text or "").split())
    if not SAFE_PAGES.match(names):
        return 400, "Bad Request", {"ok": False, "message": "Choose at least one page."}
    if Config.dry_run:
        return 200, "OK", {"ok": True, "dryRun": True, "message": "Dry run: nothing was changed."}
    ip = find_router()
    if not ip:
        return 502, "Bad Gateway", {"ok": False, "message": "Could not find your router."}
    say("pages", names)
    # names has already been checked to be page names and single spaces, so it cannot escape the quotes.
    code, out = run_ssh(ip, "be3600-anim pages set '%s'" % names)
    if code == 255 or login_failed(out):
        return 502, "Bad Gateway", {"ok": False, "message": "Could not log in to the router."}
    if code != 0:
        return 422, "Unprocessable Entity", {"ok": False, "message": last_line(out) or "The router refused."}
    return 200, "OK", {"ok": True, "message": "Saved. The router's screen now shows: " + names}


def send_file(data, name):
    """The same steps as set-animation.sh. -> (http status, reason, dict)."""
    say("got", "'%s' from Motion Studio (%d KB)" % (name, (len(data) + 1023) // 1024))

    problem, seconds = check_bea(data)
    if problem:
        say("x", problem)
        return 400, "Bad Request", {"ok": False, "message": problem}
    say("ok", "%.1f s per loop" % seconds)

    ip = find_router()
    if not ip:
        msg = "Could not find your router. Start Studio Link with its address, for example: python3 studio-link.py --router 192.168.8.1"
        say("x", msg)
        return 502, "Bad Gateway", {"ok": False, "message": msg}

    if Config.dry_run:
        say("!", "Dry run: nothing was sent.")
        return 200, "OK", {"ok": True, "message": "Dry run: the file is valid, nothing was sent.",
                           "name": name, "seconds": round(seconds, 1), "dryRun": True}

    say("send", "Sending it to %s" % ip)
    if not quiet_login():
        print("\n  Type your router admin password here when asked.\n"
              "\a\n", flush=True)
    # A fresh private temp name on the router each time (never a fixed, guessable path).
    remote = "T=$(mktemp /tmp/be3600-new.XXXXXX) && cat > $T && be3600-anim set $T %s; R=$?; rm -f $T; exit $R" % name

    fd, tmp = tempfile.mkstemp(suffix=".bea")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        code, out = run_ssh(ip, remote, stdin_path=tmp)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass

    if code == 0:
        remember_router(ip)
        Config.sent += 1
        say("ok", "Done - your animation is on the router as '%s'." % name)
        return 200, "OK", {"ok": True, "message": "Sent. It is saved on the router as '%s' and playing." % name,
                           "name": name, "seconds": round(seconds, 1)}
    if code == 255 or login_failed(out):
        Config.found = None
        msg = "Could not connect to the router or log in. Check the connection and the password, then try again."
        say("x", "Could not connect or log in.")
        return 502, "Bad Gateway", {"ok": False, "message": msg}
    msg = ("The router did not accept that file. The usual reason: it already holds 3 animations. "
           "Give the file a name you already use to replace one, or remove one below.")
    say("x", "The router did not accept that file (%s)." % (last_line(out) or "see the message above"))
    return 422, "Unprocessable Entity", {"ok": False, "message": msg}


# ---------------------------------------------------------------------------------------
# The little web server
# ---------------------------------------------------------------------------------------

_studio_cache = {}


def studio_file(path):
    """-> (bytes, content type) of a Motion Studio file this helper may serve, or None.
    Only the files the pages load: nothing else on this computer can be asked for."""
    if not _studio_cache:
        found = {}
        if STUDIO:
            import base64
            import gzip
            import io
            import tarfile
            raw = gzip.decompress(base64.b64decode("".join(STUDIO[1].split())))
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r") as t:
                for m in t.getmembers():
                    if m.isfile():
                        found[m.name] = t.extractfile(m).read()
        else:
            base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "studio")
            for rel in studio_names(base):
                with open(os.path.join(base, rel), "rb") as f:
                    found[rel] = f.read()
        _studio_cache.update(found)
        _studio_cache.setdefault("", b"")                 # remember that it was loaded, even if empty
    rel = path.lstrip("/") or "index.html"
    data = _studio_cache.get(rel)
    if not rel or data is None:
        return None
    return data, STUDIO_TYPES.get(os.path.splitext(rel)[1], "application/octet-stream")


def studio_names(base):
    """The Motion Studio files to serve, relative to studio/ (also what the download packs)."""
    names = [n for n in STUDIO_FILES if os.path.isfile(os.path.join(base, n))]
    vendor = os.path.join(base, "vendor")
    if os.path.isdir(vendor):
        names += ["vendor/" + n for n in sorted(os.listdir(vendor)) if n.endswith(".js")]
    return names


def studio_address(port):
    return "http://127.0.0.1:%d/" % port


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 30
    server_version = "be3600-studio-link"

    def log_message(self, *args):        # quiet: we print our own lines
        pass

    def _reply(self, status, reason, data=None, cors=None):
        body = json.dumps(data, separators=(",", ":")).encode() if data is not None else b""
        self.send_response(status, reason)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        if cors:
            self.send_header("Access-Control-Allow-Origin", cors)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, " + TOKEN_HEADER)
            # Browsers ask permission before a public web page talks to your own computer.
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()
        if body:
            self.wfile.write(body)
        self.close_connection = True

    def _gate(self):
        """-> the origin to echo back (or '' for non-browsers), or None if refused."""
        origin = self.headers.get("Origin")
        if not (origin_allowed(origin) and LOCAL_HOST.match(self.headers.get("Host", ""))):
            say("!", "Refused a request from '%s' (host '%s')." % (origin, self.headers.get("Host")))
            self._reply(403, "Forbidden", {"ok": False, "message": "That page is not allowed to use Studio Link."})
            return None
        return origin or ""

    def _authed(self, cors):
        """Everything except the bare ping needs this run's secret token."""
        if token_ok(self.headers.get(TOKEN_HEADER)):
            return True
        self._reply(401, "Unauthorized", {"ok": False, "needToken": True,
                                          "message": "This page has not been paired with Studio Link yet."}, cors)
        return False

    def _locked(self, cors, fn):
        """Run fn() (which talks to the router) unless another job is already doing so."""
        if not Config.lock.acquire(blocking=False):
            self._reply(409, "Conflict", {"ok": False, "busy": True, "message": "Busy: another job is still running."}, cors)
            return
        try:
            status, reason, out = fn()
        finally:
            Config.lock.release()
        self._reply(status, reason, out, cors)

    def do_OPTIONS(self):
        cors = self._gate()
        if cors is not None:
            self._reply(204, "No Content", None, cors or "*")

    def do_GET(self):
        cors = self._gate()
        if cors is None:
            return
        path = urlsplit(self.path).path
        if path.startswith("/downloads/"):
            # The copy served here has no downloads of its own; they live on the website.
            self.send_response(302, "Found")
            self.send_header("Location", DEFAULT_STUDIO_URL + path.lstrip("/"))
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            return
        static = studio_file(path) if path not in ("/ping", "/library", "/pages") else None
        if static:
            body, ctype = static
            self.send_response(200, "OK")
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True
            return
        if path == "/ping":
            # Without the token this only says "I am Studio Link, and I need pairing": no router
            # address, nothing else. With it, the page gets the full picture.
            if not token_ok(self.headers.get(TOKEN_HEADER)):
                self._reply(200, "OK", {"ok": True, "app": "be3600-studio-link", "version": 3, "needToken": True}, cors)
                return
            Config.paired = True
            self._reply(200, "OK", {"ok": True, "app": "be3600-studio-link", "version": 3, "authed": True,
                                    "dryRun": Config.dry_run, "sent": Config.sent,
                                    "router": Config.router or Config.found, "loggedIn": quiet_login(),
                                    "pages": True}, cors)
        elif path == "/library":
            if self._authed(cors):
                self._locked(cors, get_library)
        elif path == "/pages":
            if self._authed(cors):
                self._locked(cors, get_pages)
        else:
            self._reply(404, "Not Found", {"ok": False, "message": "Not found."}, cors)

    def do_POST(self):
        cors = self._gate()
        if cors is None or not self._authed(cors):
            return
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)

        if parts.path in ("/use", "/remove"):
            name = (query.get("name") or [""])[0]
            self._locked(cors, lambda: router_command(parts.path[1:], name))
            return

        if parts.path == "/pages":
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                n = 0
            if n <= 0 or n > 2048:
                self._reply(400, "Bad Request", {"ok": False, "message": "Choose at least one page."}, cors)
                return
            body = self.rfile.read(n).decode("utf-8", "replace")
            self._locked(cors, lambda: set_pages(body))
            return

        if parts.path == "/chime":
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                n = 0
            if n <= 0 or n > 4096:
                self._reply(400, "Bad Request", {"ok": False, "message": "A chime is a short list of steps."}, cors)
                return
            body = self.rfile.read(n).decode("utf-8", "replace")
            name = (query.get("name") or [""])[0]
            self._locked(cors, lambda: send_chime(name, body))
            return

        if parts.path != "/send":
            self._reply(404, "Not Found", {"ok": False, "message": "Not found."}, cors)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            self._reply(400, "Bad Request", {"ok": False, "message": "No file was sent."}, cors)
            return
        if length > MAX_BODY:
            self._reply(413, "Payload Too Large", {"ok": False, "message": "That file is far too big to be an animation."}, cors)
            return
        try:
            data = self.rfile.read(length)
        except OSError:
            data = b""
        if len(data) != length:
            self._reply(400, "Bad Request", {"ok": False, "message": "The connection closed before the whole file arrived."}, cors)
            return

        name = lib_name((query.get("name") or ["animation"])[0])
        self._locked(cors, lambda: send_file(data, name))


class BoundedServer(ThreadingHTTPServer):
    """At most a handful of connections at once, so a flood of them cannot exhaust this computer."""
    daemon_threads = True
    _slots = threading.BoundedSemaphore(12)

    def handle_error(self, request, client_address):
        # The page hangs up on its own quick status checks while a router job runs; that is normal
        # and not worth a traceback in the window (Studio-Link.cmd stays quiet about it too).
        if isinstance(sys.exc_info()[1], (ConnectionError, socket.timeout)):
            return
        say("!", "Studio Link could not handle a request (%s)." % (sys.exc_info()[1],))

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def make_server(port):
    return BoundedServer(("127.0.0.1", port), Handler)


def test_login(ip):
    """Can we get in without anyone typing (a key, or the password we hold)?"""
    code, out = run_ssh(ip, "echo studio-ok")
    return code == 0 and "studio-ok" in out


def is_home_address(ip):
    """False only for a plain IPv4 address that is not on a home/office network: 10.x, 127.x,
    192.168.x, 172.16-31.x and 169.254.x are. The same list as Test-UnusualAddress on Windows
    (Python's is_private also counts documentation and benchmark ranges, which Windows does not)."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return True                                          # a name: cannot tell, so do not nag
    if a.version != 4:
        return True
    return any(a in ipaddress.ip_network(n) for n in
               ("10.0.0.0/8", "127.0.0.0/8", "192.168.0.0/16", "172.16.0.0/12", "169.254.0.0/16"))


# ---- the remembered key's passphrase lives in the system keychain -----------------------
# The key file on disk is passphrase-protected; the passphrase itself is kept by the
# operating system (macOS Keychain, or libsecret's secret-tool on Linux), so copying the key
# file elsewhere gets an attacker nothing.

def _test_vault():
    """Test hook: a keychain in a folder, so the Save-this-login steps can be tested anywhere."""
    d = os.environ.get("BE3600_VAULT_DIR") if TESTING else None
    return os.path.join(d, "%s--%s" % (VAULT_SERVICE, KEY_COMMENT)) if d else None


def vault_kind():
    if _test_vault():
        return "test"
    if sys.platform == "darwin" and shutil.which("security"):
        return "keychain"
    if shutil.which("secret-tool"):
        return "libsecret"
    return None


def vault_store(secret):
    k = vault_kind()
    if k == "test":
        with open(_test_vault(), "w") as f:
            f.write(secret)
    elif k == "keychain":
        subprocess.run(["security", "add-generic-password", "-U", "-s", VAULT_SERVICE, "-a", KEY_COMMENT, "-w", secret],
                       check=True, capture_output=True)
    elif k == "libsecret":
        subprocess.run(["secret-tool", "store", "--label=BE3600 Studio Link", "service", VAULT_SERVICE,
                        "account", KEY_COMMENT], input=secret, text=True, check=True, capture_output=True)
    else:
        raise RuntimeError("this computer has no keychain (macOS Keychain or secret-tool)")


def vault_load():
    k = vault_kind()
    if k == "test":
        try:
            with open(_test_vault()) as f:
                return f.read() or None
        except OSError:
            return None
    try:
        if k == "keychain":
            r = subprocess.run(["security", "find-generic-password", "-s", VAULT_SERVICE, "-a", KEY_COMMENT, "-w"],
                               capture_output=True, text=True)
        elif k == "libsecret":
            r = subprocess.run(["secret-tool", "lookup", "service", VAULT_SERVICE, "account", KEY_COMMENT],
                               capture_output=True, text=True)
        else:
            return None
    except OSError:
        return None
    return r.stdout.strip() or None if r.returncode == 0 else None


def vault_clear():
    k = vault_kind()
    if k == "test":
        try:
            os.unlink(_test_vault())
        except OSError:
            pass
        return
    try:
        if k == "keychain":
            subprocess.run(["security", "delete-generic-password", "-s", VAULT_SERVICE, "-a", KEY_COMMENT], capture_output=True)
        elif k == "libsecret":
            subprocess.run(["secret-tool", "clear", "service", VAULT_SERVICE, "account", KEY_COMMENT], capture_output=True)
    except OSError:
        pass


def upgrade_key(ip):
    """A key remembered by an earlier version has no passphrase: lock it now, in place."""
    if vault_kind() is None or not os.path.exists(KEY_FILE):
        return
    try:
        phrase = secrets.token_hex(24)
        vault_store(phrase)                                  # stored first, so it can never be lost
        subprocess.run(["ssh-keygen", "-q", "-p", "-f", KEY_FILE, "-P", "", "-N", phrase], check=True, capture_output=True)
        Config.key_pass = phrase
        if test_login(ip):
            say("ok", "Your remembered key is now locked with a passphrase kept in the system keychain.")
        else:
            raise RuntimeError("the locked key did not work")
    except (OSError, subprocess.SubprocessError, RuntimeError) as e:
        Config.key_pass = None
        say("!", "Could not lock the remembered key (%s); it keeps working as before." % e)


def log_in():
    """Get in: the remembered key if there is one, otherwise the password, asked for once
    (it stays in memory). -> the router's address, or None."""
    ip = find_router()
    if not ip:
        say("!", "Could not find your router. Start Studio Link with --router 192.168.x.x")
        if local_network_hint():
            say("!", local_network_hint())
        return None
    say("ok", "Router found at %s" % ip)
    if Config.key:
        say("ok", "Using your key file; no password needed.")
        return ip
    if os.path.exists(KEY_FILE):
        Config.key = KEY_FILE
        Config.key_pass = vault_load()                       # None: a key from an earlier version (no passphrase)
        if test_login(ip):
            say("ok", "Logged in (this computer is remembered).")
            if Config.key_pass is None:
                upgrade_key(ip)
            return ip
        Config.key, Config.key_pass = None, None
        Config.stale_key = True                          # offered again after the password works
        say("!", "The remembered login no longer works (was the router reset?). Please type the password.")
    if not sys.stdin.isatty():
        return ip
    if not is_home_address(ip):
        say("!", "%s is not an address on a home or office network. Your password would travel to it." % ip)
        try:
            ans = "y" if (TESTING and os.environ.get("BE3600_ASSUME_YES")) else input("  Continue anyway? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = ""
        if ans not in ("y", "yes"):
            return ip
    if not Config.askpass:
        Config.askpass = make_askpass()
    for attempt in range(3):
        try:
            pw = getpass.getpass("  Router admin password (kept in memory only; just Enter to be asked each time): ")
        except (EOFError, KeyboardInterrupt):
            break
        if not pw:
            say("!", "No password held: you will be asked in this window for every send, and Motion Studio "
                     "cannot show your animations.")
            return ip
        Config.password = pw
        if test_login(ip):
            say("ok", "Logged in.")
            return ip
        Config.password = None
        say("x", "That did not work. Is it your router's admin password?")
    Config.password = None
    return ip


# ---------------------------------------------------------------------------------------
# Putting the screen saver on the router, and remembering this computer
# ---------------------------------------------------------------------------------------

def packable(info):
    """tarfile filter: leave out __pycache__ and what Finder / Explorer leave in folders
    (.DS_Store, ._ resource forks, Thumbs.db); owned by root, like the published bundle."""
    base = os.path.basename(info.name)
    if "__pycache__" in info.name.split("/") or base in (".DS_Store", "Thumbs.db", "desktop.ini") or base.startswith("._"):
        return None
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def get_payload():
    """-> (path of a .tar.gz with the router's files, version id), or (None, None)."""
    fd, tmp = tempfile.mkstemp(suffix=".tar.gz")
    if PAYLOAD:
        import base64
        with os.fdopen(fd, "wb") as f:
            f.write(base64.b64decode("".join(PAYLOAD[1].split())))
        return tmp, PAYLOAD[0]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not os.path.isdir(os.path.join(root, "router")):
        os.close(fd)
        os.unlink(tmp)
        return None, None
    import tarfile
    with os.fdopen(fd, "wb") as f, tarfile.open(fileobj=f, mode="w:gz") as t:
        for d in ("router", "setup", "animations"):
            t.add(os.path.join(root, d), arcname=d, filter=packable)
    return tmp, "dev"


def yes_no(question):
    """Enter or Y = yes, N = no. Never asks when nobody is there to answer."""
    if TESTING and os.environ.get("BE3600_ASSUME_YES"):      # test hook
        return True
    if not sys.stdin.isatty():
        return False
    try:
        return input("  %s [Enter = yes, N = no] " % question).strip().lower() not in ("n", "no")
    except (EOFError, KeyboardInterrupt):
        return False


def install_on_router(ip, path, version):
    say("install", "Putting the screen saver on your router")
    # Unpacked in a fresh private folder on the router, and removed afterwards; the installer's own exit code is kept.
    remote = ("D=$(mktemp -d /tmp/be3600-setup.XXXXXX) && cd $D && gunzip -c | tar xf - && "
              "BE3600_VERSION=%s sh setup/router-install.sh; R=$?; cd /; rm -rf $D; exit $R" % version)
    code, out = run_ssh(ip, remote, stdin_path=path)
    for line in out.splitlines():
        if line.strip():
            print("      " + line.rstrip(), flush=True)
    if code == 0:
        say("ok", "The screen saver is installed and running.")
        return True
    if code == 3:
        say("x", "That device is not a GL-BE3600 with a front display (nothing was changed). "
                 "Start Studio Link with the right address: --router 192.168.x.x")
    else:
        say("x", "The install did not finish (see above).")
    return False


def confirm_installed(ip):
    """Install (or update) the screen saver on the router when it is missing or old."""
    if Config.dry_run or not quiet_login():
        return
    code, out = run_ssh(ip, "cat /etc/be3600-screen/version 2>/dev/null; echo; "
                            "command -v be3600-anim >/dev/null && echo HAVE-ANIM; "
                            "be3600-anim list --plain >/dev/null 2>&1 && echo LIST-OK")
    if code != 0:
        return
    have, current = "HAVE-ANIM" in out, "LIST-OK" in out
    version = next((l.strip() for l in out.splitlines() if l.strip() and l.strip() not in ("HAVE-ANIM", "LIST-OK")), "")
    path, pid = get_payload()
    try:
        if path is None:
            return
        if current and (pid == "dev" or version == pid):
            return                                        # already there and up to date
        if not have:
            headline, ask = "This router does not have the screen saver yet.", "Put it on the router now?"
        elif not current:
            headline, ask = "The screen saver on this router is an older version.", "Update it now?"
        else:
            headline, ask = "A newer version of the screen saver is available.", "Update it now?"
        print("\n  %s" % headline, flush=True)
        if yes_no(ask):
            install_on_router(ip, path, pid)
        else:
            say("!", "Skipped. Motion Studio can only show and change animations once it is installed.")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def offer_remember(ip):
    """After a password login: offer to keep a key so the password is never asked again.
    A remembered key that stopped working is replaced, not left in the way for ever."""
    if Config.dry_run or Config.key or Config.password is None:
        return
    if os.path.exists(KEY_FILE) and not Config.stale_key:
        return
    if vault_kind() is None:
        say("!", "This computer has no keychain (macOS Keychain, or 'secret-tool' on Linux), so it cannot remember "
                 "the login safely. You will type the password each time.")
        return
    print("\n  Remember this computer?\n"
          "  Studio Link keeps a private key on this computer, locked with a passphrase that\n"
          "  the system keychain holds for you, so you never type the password again. Anyone who\n"
          "  can unlock your keychain (you, signed in) can reach the router.\n"
          "  Undo any time:  python3 studio-link.py --forget", flush=True)
    if not yes_no("Remember it?"):
        return
    problem = create_remembered_key(ip)
    if problem is None:
        say("ok", "Remembered. Next time there is nothing to type.")
    else:
        say("!", "Could not set that up (%s). Nothing is lost; you will just be asked for the password each time." % problem)


def create_remembered_key(ip):
    """Make this computer's key (locked with a passphrase the keychain keeps), add it to the
    router and switch to it. -> None when it works, else the reason in a few words (and
    nothing is left behind: key files, keychain entry and the password are as they were)."""
    global KEY_COMMENT
    if Config.stale_key:
        vault_clear()                                    # the old key's passphrase, under its own label
        for p in (KEY_FILE, KEY_FILE + ".pub"):
            try:
                os.unlink(p)
            except OSError:
                pass
        KEY_COMMENT = fresh_key_comment()
        Config.stale_key = False
    try:
        os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
        phrase = secrets.token_hex(24)
        vault_store(phrase)                                  # kept first, so the key can never be locked out
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", phrase, "-C", KEY_COMMENT, "-f", KEY_FILE],
                       check=True, capture_output=True)
        os.chmod(KEY_FILE, 0o600)
        with open(KEY_FILE + ".pub", "rb") as f:
            pub = f.read()
        pub_tmp = KEY_FILE + ".send"
        with open(pub_tmp, "wb") as f:
            f.write(pub)
        code, out = run_ssh(ip, "mkdir -p /etc/dropbear && touch %s && chmod 600 %s && sed -i '/ %s$/d' %s && cat >> %s"
                            % (AUTH_KEYS, AUTH_KEYS, KEY_COMMENT, AUTH_KEYS, AUTH_KEYS), stdin_path=pub_tmp)
        os.unlink(pub_tmp)
        if code != 0:
            raise RuntimeError(last_line(out) or "the router refused the key")
        pw, Config.password = Config.password, None
        Config.key, Config.key_pass = KEY_FILE, phrase
        if test_login(ip):
            return None
        Config.key, Config.key_pass, Config.password = None, None, pw
        raise RuntimeError("the router did not accept the key")
    except (OSError, subprocess.SubprocessError, RuntimeError) as e:
        for p in (KEY_FILE, KEY_FILE + ".pub"):
            try:
                os.unlink(p)
            except OSError:
                pass
        vault_clear()
        return str(e) or e.__class__.__name__


def forget(ip):
    """Take this computer's key off the router and delete it here."""
    if ip and quiet_login():
        code, out = run_ssh(ip, "sed -i '/ %s$/d' %s" % (KEY_COMMENT, AUTH_KEYS))
        if code == 0:
            say("ok", "This computer's key was removed from the router.")
        else:
            say("!", "Could not remove the key from the router (%s)." % (last_line(out) or "no reply"))
    for p in (KEY_FILE, KEY_FILE + ".pub"):
        try:
            os.unlink(p)
        except OSError:
            pass
    vault_clear()
    say("ok", "Forgotten. Studio Link will ask for your password again.")


# ---------------------------------------------------------------------------------------
# Install and Uninstall: the guided flow behind the one-click downloads
#
# The same steps, sentences and choices as tools/studio-link.ps1 (-Install / -Uninstall);
# tests/setup_flow/ holds the exact conversations both must produce.
# ---------------------------------------------------------------------------------------

HELP_SSH = "ssh root@%s"
RETRY_WORDS = {2: "2 tries left", 1: "1 try left"}


class Quit(Exception):
    """The person chose to stop (Q, or closing the input)."""


_answers = None


def _scripted():
    """Test hook: answers come from a file (one per line) instead of the keyboard."""
    global _answers
    path = os.environ.get("BE3600_ANSWERS") if TESTING else None
    if not path:
        return None
    if _answers is None:
        with open(path) as f:
            _answers = f.read().splitlines()
    return _answers


def _color(code, text):
    return "\033[%sm%s\033[0m" % (code, text) if sys.stdout.isatty() and _scripted() is None else text


def flow_say(text=""):
    print(("     " + text) if text else "", flush=True)


def flow_title(text):
    print("\n  " + _color("1", text), flush=True)


def flow_step(n, total, text):
    print("\n  " + _color("36", "Step %d of %d: %s" % (n, total, text)), flush=True)


def flow_end(text, good):
    print("\n  " + _color("32" if good else "31", text), flush=True)


def flow_ask(prompt, secret=False):
    """Show the prompt, read one line. Raises Quit when the input ends."""
    sys.stdout.write("     " + prompt)
    sys.stdout.flush()
    scripted = _scripted()
    if scripted is not None:
        if not scripted:
            print(flush=True)
            raise Quit()
        answer = scripted.pop(0)
        print("" if secret else answer, flush=True)
        return answer
    try:
        return getpass.getpass("") if secret else input()
    except (EOFError, KeyboardInterrupt):
        print(flush=True)
        raise Quit()


def flow_close():
    """Double-clicked windows close by themselves on some systems; leave the result on screen."""
    if TESTING or not sys.stdin.isatty():
        return
    try:
        input("\n  Press Enter to close this window.")
    except (EOFError, KeyboardInterrupt):
        pass


ADDRESS_PROMPT = "Press Enter to try again, or type your router's address (like 192.168.8.1), or Q to quit: "


def flow_find(skip):
    """Step 1: the last address that worked, this computer's gateway, then 192.168.8.1; if none
    answers, ask. Addresses in skip (found not to be a GL-BE3600) are passed over.
    -> the router's address. Raises Quit."""
    look = True
    while True:
        if look:
            for ip in ([Config.router] if Config.router else []) + router_candidates():   # --router first
                if ip not in skip and ssh_open(ip):
                    flow_say("Found your router at %s." % ip)
                    return ip
            flow_say("We can't find your router. Make sure this computer is on the router's Wi-Fi (or plugged into it), "
                     "then press Enter to try again.")
        look = False
        answer = flow_ask(ADDRESS_PROMPT).strip()
        if answer.lower() in ("q", "quit"):
            raise Quit()
        if not answer:
            flow_say("Trying again...")
            look = True
            continue
        if not valid_host(answer):
            flow_say("That doesn't look like a router address. It should look like 192.168.8.1.")
            continue
        if not is_home_address(answer):
            flow_say("%s isn't an address on a home or office network, and your password would be sent there." % answer)
            if flow_ask("Use it anyway? [y/N] ").strip().lower() not in ("y", "yes"):
                continue
        if answer in skip:
            flow_say("%s isn't a GL-BE3600 (Slate 7). Type the address of your GL-BE3600." % answer)
            continue
        if ssh_open(answer):
            flow_say("Found your router at %s." % answer)
            return answer
        flow_say("Nothing answered at %s. Check the address, and that this computer is on the router's Wi-Fi." % answer)


def try_login(ip):
    """-> 'ok', 'denied' (wrong password or key) or 'unreachable'."""
    code, out = run_ssh(ip, "echo studio-ok")
    if code == 0 and "studio-ok" in out:
        return "ok"
    return "denied" if (login_failed(out) or code != 255) else "unreachable"


def flow_login(ip):
    """Step 2: the saved login, or the password (3 tries). -> 'ok', 'failed' or 'unreachable'.
    Config.password is set afterwards only if the password was what got in."""
    Config.password = None
    Config.key = Config.key_pass = None
    Config.stale_key = False
    if os.path.exists(KEY_FILE):
        Config.key, Config.key_pass = KEY_FILE, vault_load()
        result = try_login(ip)
        if result == "ok":
            flow_say("Using the login saved on this computer.")
            return "ok"
        Config.key = Config.key_pass = None
        if result == "unreachable":
            return "unreachable"
        Config.stale_key = True
        flow_say("The saved login doesn't work any more (maybe the router was reset). Please type the password instead.")
    if not Config.askpass:
        Config.askpass = make_askpass()
    flow_say("Type your router's admin password (the one for its admin web page) and press Enter.")
    flow_say("Nothing will show while you type - that's normal.")
    tries = 3
    while tries:
        pw = flow_ask("Password: ", secret=True)
        if not pw:
            flow_say("Nothing was typed. Type the password, then press Enter.")
            continue
        Config.password = pw
        result = try_login(ip)
        if result == "ok":
            flow_say("Logged in.")
            return "ok"
        Config.password = None
        if result == "unreachable":
            return "unreachable"
        tries -= 1
        if tries:
            flow_say("That password didn't work. Please try again (%s)." % RETRY_WORDS[tries])
    return "failed"


def flow_save_login(ip):
    """After a password login: keep a key (never the password) so it is not asked again."""
    if vault_kind() is None:
        flow_say("This computer has no safe place to keep a login, so you'll type the password next time.")
        return
    print(flush=True)
    answer = flow_ask("Save this login so you don't have to type the password next time? [Y/n] ").strip().lower()
    if answer in ("n", "no"):
        flow_say("OK. You'll type the password next time.")
        return
    problem = create_remembered_key(ip)
    if problem is None:
        flow_say("Saved. Next time you won't need to type the password.")
    else:
        flow_say("The login couldn't be saved this time (%s). That's fine - you'll type the password next time." % problem)


# No double quotes: on Windows this travels inside one quoted cmd.exe argument.
BE3600_CHECK = ('S=$(cat /sys/class/graphics/fb0/virtual_size 2>/dev/null); '
                '[ -x /etc/init.d/gl_screen ] && [ x$S = x76,284 ] && echo be3600-yes; '
                'command -v be3600-uninstall >/dev/null 2>&1 && echo be3600-installed; true')


def router_facts(ip):
    """-> (is a GL-BE3600, has the screen saver), or None if the router stopped answering."""
    code, out = run_ssh(ip, BE3600_CHECK)
    if code != 0:
        return None
    return "be3600-yes" in out, "be3600-installed" in out


def not_be3600(ip):
    flow_say("The device at %s isn't a GL-BE3600 (Slate 7) - it may be your internet provider's modem. "
             "Nothing was changed on it." % ip)


def flow_connect(offer_save, total):
    """Steps 1 and 2, until the address is a GL-BE3600 we are logged in to; then, after a
    password login, the offer to save it (only ever to a GL-BE3600). -> (ip, has the screen saver)."""
    skip = set()
    while True:
        flow_step(1, total, "Finding your router")
        ip = flow_find(skip)
        flow_step(2, total, "Logging in to your router")
        result = flow_login(ip)
        if result == "failed":
            raise FlowFailed("That password didn't work 3 times. Check it's the password for the router's admin web "
                             "page, then run this again.")
        facts = router_facts(ip) if result == "ok" else None
        if facts is None:
            raise FlowFailed("Your router stopped answering. Check this computer is still on its Wi-Fi, then run this again.")
        if not facts[0]:
            not_be3600(ip)
            skip.add(ip)
            Config.password = None
            Config.key = Config.key_pass = None
            continue
        if offer_save and Config.password is not None:
            flow_save_login(ip)
        return ip, facts[1]


class FlowFailed(Exception):
    """Something went wrong: the message says what, and what to try."""


def run_install():
    flow_title("GL.iNet Router Screen Saver - Install")
    flow_say("This puts the animated screen saver on your GL.iNet GL-BE3600 (Slate 7).")
    code = 1
    try:
        ip, _ = flow_connect(offer_save=True, total=3)
        flow_step(3, 3, "Installing the screen saver")
        flow_say("This takes about a minute. Please keep this window open.")
        path, version = get_payload()
        if path is None:
            raise FlowFailed("This file is missing the screen saver's files. Download it again, then run it.")
        try:
            remote = ("D=$(mktemp -d /tmp/be3600-setup.XXXXXX) && cd $D && gunzip -c | tar xf - && "
                      "BE3600_VERSION=%s sh setup/router-install.sh; R=$?; cd /; rm -rf $D; exit $R" % version)
            rc, out = run_ssh(ip, remote, stdin_path=path)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        if rc == 3:                                  # the router's own check (the one above should catch it first)
            raise FlowFailed("The device at %s isn't a GL-BE3600 (Slate 7), so nothing was installed. "
                             "Run this again and type your GL-BE3600's address." % ip)
        if rc != 0:
            why = last_line(out).replace("ERROR:", "").strip() or "no reason given"
            raise FlowFailed("The install didn\'t finish on the router: %s. Run this again; if it keeps failing, "
                             "restart the router and try once more." % why.rstrip("."))
        flow_say("Installed.")
        remember_router(ip)
        flow_end("All done! Your router's screen will show the animation after a few idle seconds.", True)
        code = 0
    except Quit:
        flow_end("Nothing was changed. Run this again when this computer is on the router's Wi-Fi.", False)
    except FlowFailed as e:
        flow_end(str(e), False)
    flow_close()
    return code


def forget_this_computer():
    """The saved router address, the saved login's key files and its keychain entry."""
    vault_clear()
    for p in (KEY_FILE, KEY_FILE + ".pub", STATE_FILE):
        try:
            os.unlink(p)
        except OSError:
            pass
    try:
        os.rmdir(os.path.dirname(STATE_FILE))
    except OSError:
        pass


def run_uninstall():
    flow_title("GL.iNet Router Screen Saver - Uninstall")
    flow_say("This removes the screen saver and puts your router's normal screen back.")
    code = 1
    ip = None
    try:
        ip, installed = flow_connect(offer_save=False, total=3)
        flow_step(3, 3, "Removing the screen saver")
        if installed:
            rc, out = run_ssh(ip, "/usr/sbin/be3600-uninstall --purge --forget-keys")
            if rc != 0:
                why = last_line(out).replace("ERROR:", "").strip() or "no reason given"
                raise FlowFailed("The router couldn\'t remove it: %s. Run this again, or remove it on the router itself: "
                                 "connect with %s, then type be3600-uninstall --purge" % (why.rstrip("."), HELP_SSH % ip))
            flow_say("Removed it from the router. GL.iNet's normal screen is back.")
        else:
            run_ssh(ip, "sed -i '/ be3600-studio-link-[A-Za-z0-9_-]*$/d' %s 2>/dev/null; true" % AUTH_KEYS)
            flow_say("The screen saver wasn't on this router, so there was nothing to remove there.")
        forget_this_computer()
        flow_say("Removed the saved login and router address from this computer.")
        flow_end("Your router is back to normal. You can delete this file.", True)
        code = 0
    except Quit:
        flow_end("We couldn't reach your router, so nothing was changed.", False)
        flow_say("To remove it on the router itself: connect with %s, log in with the router's admin password,"
                 % (HELP_SSH % (ip or "192.168.8.1")))
        flow_say("then type: be3600-uninstall --purge")
    except FlowFailed as e:
        flow_end(str(e), False)
    flow_close()
    return code


def use_local_studio(choice, platform=None):
    """Open the Motion Studio served here (True) or the website (False)?"""
    if choice in ("local", "web"):
        return choice == "local"
    if os.environ.get("STUDIO_LINK_URL"):
        return False                                   # someone chose a Motion Studio address on purpose
    return (platform or sys.platform) == "darwin"


def studio_url():
    """The Motion Studio address to open (without the #link= pairing part)."""
    return studio_address(Config.port) if Config.local_studio else STUDIO_URL


def main():
    ap = argparse.ArgumentParser(description="Studio Link: lets Motion Studio send animations to your router.")
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--router")
    ap.add_argument("--key")
    ap.add_argument("--dry-run", action="store_true", help="check files but send nothing (for testing)")
    ap.add_argument("--no-browser", action="store_true", help="do not open Motion Studio automatically")
    ap.add_argument("--forget", action="store_true", help="remove the remembered login from the router and this computer")
    ap.add_argument("--studio", choices=("auto", "local", "web"), default="auto",
                    help="which Motion Studio to open: the copy served by Studio Link on this computer (local), "
                         "or the website (web). auto = local on a Mac, where Safari cannot reach Studio Link "
                         "from the website; web elsewhere")
    ap.add_argument("--install", action="store_true", help="put the screen saver on your router, step by step")
    ap.add_argument("--uninstall", action="store_true", help="take it off your router and this computer, step by step")
    a = ap.parse_args()
    if a.router and not valid_host(a.router):
        sys.exit("--router must be an address like 192.168.8.1 (letters, digits, dots and dashes only).")
    Config.router, Config.key, Config.dry_run = a.router, a.key, a.dry_run
    Config.token = secrets.token_urlsafe(24)
    Config.port = a.port
    if a.install or ACTION == "install":
        sys.exit(run_install())
    if a.uninstall or ACTION == "uninstall":
        sys.exit(run_uninstall())
    Config.local_studio = use_local_studio(a.studio)

    print("\n  GL.iNet Router Screen Saver (BE3600) - Studio Link\n", flush=True)

    if a.forget:
        forget(log_in())
        return

    try:
        server = make_server(a.port)
    except OSError:
        sys.exit("Could not start listening on port %d (is Studio Link already running?)." % a.port)

    if a.dry_run:
        say("!", "DRY RUN: files are checked but nothing is sent.")
        say("!", "To pair a page by hand, open: %s#link=%s" % (studio_url(), Config.token))
    else:
        ip = log_in()
        if ip and quiet_login():
            confirm_installed(ip)
            offer_remember(ip)
    print("\n  Studio Link is running. Leave this window open.\n"
          "  Motion Studio connects to it by itself.\n"
          "  Listening on this computer only: 127.0.0.1:%d\n"
          "  Press Ctrl+C to stop.\n" % a.port, flush=True)
    if Config.local_studio:
        print("  Motion Studio is served from this computer: %s\n"
              "  (Safari cannot connect the website to Studio Link, so use this address.)\n" % studio_url(), flush=True)

    if TESTING:
        say("!", "TEST pairing link: %s#link=%s" % (studio_url(), Config.token))

    def open_studio():
        if not Config.paired:                              # no page has paired yet: open one, with the token
            import webbrowser
            say("ok", "Opening Motion Studio in your browser" + (" (served by Studio Link: %s)" % studio_url()
                                                                  if Config.local_studio else ""))
            webbrowser.open(studio_url() + "#link=" + Config.token)
    if not (a.no_browser or a.dry_run or os.environ.get("BE3600_NO_BROWSER")):
        t = threading.Timer(6.0, open_studio)
        t.daemon = True
        t.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Bye!\n")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
