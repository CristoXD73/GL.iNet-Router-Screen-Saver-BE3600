#!/usr/bin/env python3
"""Studio Link for macOS and Linux (Windows: Studio-Link.cmd). Standard library only.

Lets Motion Studio, in your browser, send an animation straight to your router.
Start it with ./studio-link.sh and leave the terminal open.

It listens on THIS computer only (127.0.0.1), so nothing else on your network can
reach it. Motion Studio's drop zone posts a .bea file to it, and it does what
set-animation.sh does: check the file, then send it to the router over SSH. You
type your router admin password in THIS terminal; the web page never sees it.

    ./studio-link.sh [--port 8791] [--router 192.168.8.1] [--key FILE] [--dry-run]

Only pages served from the Motion Studio site, a local file, or localhost are
accepted. To allow another site (for example your own fork's GitHub Pages address)
set STUDIO_LINK_ORIGINS to a comma-separated list before starting it.
"""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bea2  # noqa: E402  (same folder)

FRAME_BYTES = 43168
MAX_BODY = 32 * 1024 * 1024
MAX_SECONDS = 25

ALLOWED_ORIGINS = {"https://cristoxd73.github.io", "null"}
ALLOWED_ORIGINS.update(o.strip() for o in os.environ.get("STUDIO_LINK_ORIGINS", "").split(",") if o.strip())
LOCAL_ORIGIN = re.compile(r"^http://(localhost|127\.0\.0\.1)(:\d+)?$")
LOCAL_HOST = re.compile(r"^(localhost|127\.0\.0\.1)(:\d+)?$")

STATE_FILE = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                          "be3600-screensaver", "router")


class Config:
    router = None
    key = None
    dry_run = False
    ssh = os.environ.get("BE3600_SSH", "ssh")      # test hook: a stand-in for ssh
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
        msg = "Could not find your router. Start Studio Link with its address, for example: ./studio-link.sh --router 192.168.8.1"
        say("x", msg)
        return 502, "Bad Gateway", {"ok": False, "message": msg}

    if Config.dry_run:
        say("!", "Dry run: nothing was sent.")
        return 200, "OK", {"ok": True, "message": "Dry run: the file is valid, nothing was sent.",
                           "name": name, "seconds": round(seconds, 1), "dryRun": True}

    say("send", "Sending it to %s" % ip)
    args = [Config.ssh]
    if Config.key:
        args += ["-i", Config.key, "-o", "BatchMode=yes"]
    else:
        print("\n  Type your router admin password here when asked.\n"
              "  (Nothing shows while you type - that is normal.)\a\n", flush=True)
    args += ["-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10", "root@" + ip,
             "cat > /tmp/be3600-new.bea && be3600-anim set /tmp/be3600-new.bea %s && rm -f /tmp/be3600-new.bea" % name]

    fd, tmp = tempfile.mkstemp(suffix=".bea")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        with open(tmp, "rb") as stdin:
            code = subprocess.run(args, stdin=stdin).returncode
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
    if code == 255:
        Config.found = None
        msg = "Could not connect to the router or log in. Check the connection and the password, then try again."
        say("x", "Could not connect or log in.")
        return 502, "Bad Gateway", {"ok": False, "message": msg}
    msg = ("The router did not accept that file. The usual reason: it already holds 3 animations. "
           "Give the file a name you already use to replace one, or remove one with "
           "\"be3600-anim remove NAME\". The router's own message is in the Studio Link window.")
    say("x", "The router did not accept that file (see the message above).")
    return 422, "Unprocessable Entity", {"ok": False, "message": msg}


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

    def do_OPTIONS(self):
        cors = self._gate()
        if cors is not None:
            self._reply(204, "No Content", None, cors or "*")

    def do_GET(self):
        cors = self._gate()
        if cors is None:
            return
        if urlsplit(self.path).path == "/ping":
            self._reply(200, "OK", {"ok": True, "app": "be3600-studio-link", "version": 1,
                                    "dryRun": Config.dry_run, "sent": Config.sent}, cors)
        else:
            self._reply(404, "Not Found", {"ok": False, "message": "Not found."}, cors)

    def do_POST(self):
        cors = self._gate()
        if cors is None:
            return
        parts = urlsplit(self.path)
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

        name = lib_name((parse_qs(parts.query).get("name") or ["animation"])[0])
        if not Config.lock.acquire(blocking=False):
            self._reply(409, "Conflict", {"ok": False, "message": "One at a time: another animation is still being sent."}, cors)
            return
        try:
            status, reason, out = send_file(data, name)
        finally:
            Config.lock.release()
        self._reply(status, reason, out, cors)


def make_server(port):
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


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

    print("\n  GL.iNet Router Screen Saver (BE3600) - Studio Link\n\n"
          "  Studio Link is running. Leave this window open, then use the drop zone in\n"
          "  Motion Studio to send animations to your router.\n\n"
          "  Listening on this computer only: 127.0.0.1:%d\n"
          "  Press Ctrl+C to stop.\n" % a.port, flush=True)
    if a.dry_run:
        say("!", "DRY RUN: files are checked but nothing is sent.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Bye!\n")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
