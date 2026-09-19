#!/usr/bin/env python3
"""Studio Link for macOS and Linux (Windows: Studio-Link.cmd). Standard library only.

Lets Motion Studio, in your browser, send animations to your router and show what is
on it. Start it, type your router's admin password once when it asks, and leave the
terminal open.

It listens on THIS computer only (127.0.0.1), so nothing else on your network can
reach it. The password is kept in memory only while this window is open (never written
anywhere, never given to the web page) and is handed to ssh through a small helper
program, so nothing asks again.

    python3 studio-link.py [--port 8791] [--router 192.168.8.1] [--key FILE] [--dry-run]

Only pages served from the Motion Studio site, a local file, or localhost are accepted.
To allow another site (for example your own fork's GitHub Pages address) set
STUDIO_LINK_ORIGINS to a comma-separated list before starting it.
"""
import argparse
import atexit
import getpass
import json
import os
import re
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

ALLOWED_ORIGINS = {"https://cristoxd73.github.io", "null"}
ALLOWED_ORIGINS.update(o.strip() for o in os.environ.get("STUDIO_LINK_ORIGINS", "").split(",") if o.strip())
LOCAL_ORIGIN = re.compile(r"^http://(localhost|127\.0\.0\.1)(:\d+)?$")
LOCAL_HOST = re.compile(r"^(localhost|127\.0\.0\.1)(:\d+)?$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,40}$")

STATE_FILE = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                          "be3600-screensaver", "router")


class Config:
    router = None
    key = None
    dry_run = False
    ssh = os.environ.get("BE3600_SSH", "ssh")      # test hook: a stand-in for ssh
    password = None                                # kept in memory only
    askpass = None
    sent = 0
    lock = threading.Lock()
    found = None


def origin_allowed(origin):
    if not origin:
        return True                                  # not a browser (curl and friends)
    return origin in ALLOWED_ORIGINS or bool(LOCAL_ORIGIN.match(origin))


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
    try:
        with socket.create_connection((ip, 22), timeout=timeout):
            return True
    except OSError:
        return False


def default_gateway():
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
    cands = []
    try:
        with open(STATE_FILE) as f:
            cands.append(f.readline().strip())
    except OSError:
        pass
    cands += [default_gateway(), "192.168.8.1"]
    for c in dict.fromkeys(x for x in cands if x):
        if ssh_open(c):
            Config.found = c
            return c
    return None


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
    Otherwise ssh asks for the password on this terminal, as Set-Animation does."""
    args = [Config.ssh]
    env = dict(os.environ)
    quiet = quiet_login()
    if Config.key:
        args += ["-i", Config.key, "-o", "BatchMode=yes"]
    elif Config.password is not None:
        args += ["-o", "NumberOfPasswordPrompts=1"]
        env.update(SSH_ASKPASS=Config.askpass, SSH_ASKPASS_REQUIRE="force",
                   BE3600_STUDIO_PW=Config.password)
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
        return 502, "Bad Gateway", {"ok": False, "message": "The router's software is older than this Studio Link. "
                                    "Run Install.cmd (or ./install.sh) again to update it."}
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
              "  (Nothing shows while you type - that is normal.)\a\n", flush=True)
    remote = "cat > /tmp/be3600-new.bea && be3600-anim set /tmp/be3600-new.bea %s && rm -f /tmp/be3600-new.bea" % name

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
    return 422, "Unprocessable Entity", {"ok": False, "message": msg, "detail": (out or "").strip()[:600]}


# ---------------------------------------------------------------------------------------
# The little web server
# ---------------------------------------------------------------------------------------

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
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
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
        if path == "/ping":
            self._reply(200, "OK", {"ok": True, "app": "be3600-studio-link", "version": 2, "dryRun": Config.dry_run,
                                    "sent": Config.sent, "router": Config.router or Config.found,
                                    "loggedIn": quiet_login()}, cors)
        elif path == "/library":
            self._locked(cors, get_library)
        else:
            self._reply(404, "Not Found", {"ok": False, "message": "Not found."}, cors)

    def do_POST(self):
        cors = self._gate()
        if cors is None:
            return
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)

        if parts.path in ("/use", "/remove"):
            name = (query.get("name") or [""])[0]
            self._locked(cors, lambda: router_command(parts.path[1:], name))
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


def make_server(port):
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def log_in():
    """Ask for the router password once and check it. The password stays in memory."""
    ip = find_router()
    if not ip:
        say("!", "Could not find your router. Start Studio Link with --router 192.168.x.x")
        return
    say("ok", "Router found at %s" % ip)
    if Config.key:
        say("ok", "Using your key file; no password needed.")
        return
    if not sys.stdin.isatty():
        return
    Config.askpass = make_askpass()
    for attempt in range(3):
        try:
            pw = getpass.getpass("  Router admin password (kept in memory only; just Enter to be asked each time): ")
        except (EOFError, KeyboardInterrupt):
            break
        if not pw:
            say("!", "No password held: you will be asked in this window for every send, and Motion Studio "
                     "cannot show your animations.")
            return
        Config.password = pw
        code, out = run_ssh(ip, "be3600-anim list --plain")
        if code == 0 and parse_library(out)[0] is not None:
            say("ok", "Logged in.")
            return
        Config.password = None
        if code == 0:
            say("!", "Logged in, but the router's software is older than this Studio Link; run Install.cmd again.")
            Config.password = pw
            return
        say("x", "That did not work (%s)." % (last_line(out) or "no reply"))
    Config.password = None


def main():
    ap = argparse.ArgumentParser(description="Studio Link: lets Motion Studio send animations to your router.")
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--router")
    ap.add_argument("--key")
    ap.add_argument("--dry-run", action="store_true", help="check files but send nothing (for testing)")
    a = ap.parse_args()
    Config.router, Config.key, Config.dry_run = a.router, a.key, a.dry_run

    try:
        server = make_server(a.port)
    except OSError:
        sys.exit("Could not start listening on port %d (is Studio Link already running?)." % a.port)

    print("\n  GL.iNet Router Screen Saver (BE3600) - Studio Link\n", flush=True)
    if a.dry_run:
        say("!", "DRY RUN: files are checked but nothing is sent.")
    else:
        log_in()
    print("\n  Studio Link is running. Leave this window open, then use Motion Studio's drop zone.\n"
          "  Listening on this computer only: 127.0.0.1:%d\n"
          "  Press Ctrl+C to stop.\n" % a.port, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Bye!\n")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
