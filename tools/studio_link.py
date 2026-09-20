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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bea2  # noqa: E402  (same folder)

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
KEY_COMMENT = "be3600-studio-link-" + re.sub(r"[^A-Za-z0-9_-]+", "-", socket.gethostname() or "computer")[:30]
VAULT_SERVICE = "be3600-studio-link"
AUTH_KEYS = "/etc/dropbear/authorized_keys"
DEFAULT_STUDIO_URL = "https://cristoxd73.github.io/GL.iNet-Router-Screen-Saver-BE3600/studio/"
STUDIO_URL = os.environ.get("STUDIO_LINK_URL") or DEFAULT_STUDIO_URL
if not re.match(r"^(https://[A-Za-z0-9.-]+(:\d+)?/[^\s#]*|http://(localhost|127\.0\.0\.1)(:\d+)?/[^\s#]*)$", STUDIO_URL):
    STUDIO_URL = DEFAULT_STUDIO_URL                 # never open file:, javascript: or anything odd

# In the single-file download the files the router needs are packed in here as
# (version id, base64 of a .tar.gz); from a clone of the repo they are packed on the fly.
PAYLOAD = None  # __PAYLOAD__


class Config:
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
    # Anything that is not a plain address or name (for example text that looks like an ssh
    # option) is dropped, whether it came from the saved file or from the network.
    for c in dict.fromkeys(x for x in cands if valid_host(x)):
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
        if path == "/ping":
            # Without the token this only says "I am Studio Link, and I need pairing": no router
            # address, nothing else. With it, the page gets the full picture.
            if not token_ok(self.headers.get(TOKEN_HEADER)):
                self._reply(200, "OK", {"ok": True, "app": "be3600-studio-link", "version": 3, "needToken": True}, cors)
                return
            Config.paired = True
            self._reply(200, "OK", {"ok": True, "app": "be3600-studio-link", "version": 3, "authed": True,
                                    "dryRun": Config.dry_run, "sent": Config.sent,
                                    "router": Config.router or Config.found, "loggedIn": quiet_login()}, cors)
        elif path == "/library":
            if self._authed(cors):
                self._locked(cors, get_library)
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
    """False only for a plain IP address that is not on a home/office network."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return True                                          # a name: cannot tell, so do not nag
    return a.is_private or a.is_link_local or a.is_loopback


# ---- the remembered key's passphrase lives in the system keychain -----------------------
# The key file on disk is passphrase-protected; the passphrase itself is kept by the
# operating system (macOS Keychain, or libsecret's secret-tool on Linux), so copying the key
# file elsewhere gets an attacker nothing.

def vault_kind():
    if sys.platform == "darwin" and shutil.which("security"):
        return "keychain"
    if shutil.which("secret-tool"):
        return "libsecret"
    return None


def vault_store(secret):
    k = vault_kind()
    if k == "keychain":
        subprocess.run(["security", "add-generic-password", "-U", "-s", VAULT_SERVICE, "-a", KEY_COMMENT, "-w", secret],
                       check=True, capture_output=True)
    elif k == "libsecret":
        subprocess.run(["secret-tool", "store", "--label=BE3600 Studio Link", "service", VAULT_SERVICE,
                        "account", KEY_COMMENT], input=secret, text=True, check=True, capture_output=True)
    else:
        raise RuntimeError("this computer has no keychain (macOS Keychain or secret-tool)")


def vault_load():
    k = vault_kind()
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
            t.add(os.path.join(root, d), arcname=d, filter=lambda i: None if "__pycache__" in i.name else i)
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
    """After a password login: offer to keep a key so the password is never asked again."""
    if Config.dry_run or Config.key or Config.password is None or os.path.exists(KEY_FILE):
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
            say("ok", "Remembered. Next time there is nothing to type.")
            return
        Config.key, Config.key_pass, Config.password = None, None, pw
        raise RuntimeError("the router did not accept the key")
    except (OSError, subprocess.SubprocessError, RuntimeError) as e:
        say("!", "Could not set that up (%s). Nothing is lost; you will just be asked for the password each time." % e)
        for p in (KEY_FILE, KEY_FILE + ".pub"):
            try:
                os.unlink(p)
            except OSError:
                pass
        vault_clear()


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


def main():
    ap = argparse.ArgumentParser(description="Studio Link: lets Motion Studio send animations to your router.")
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--router")
    ap.add_argument("--key")
    ap.add_argument("--dry-run", action="store_true", help="check files but send nothing (for testing)")
    ap.add_argument("--no-browser", action="store_true", help="do not open Motion Studio automatically")
    ap.add_argument("--forget", action="store_true", help="remove the remembered login from the router and this computer")
    a = ap.parse_args()
    if a.router and not valid_host(a.router):
        sys.exit("--router must be an address like 192.168.8.1 (letters, digits, dots and dashes only).")
    Config.router, Config.key, Config.dry_run = a.router, a.key, a.dry_run
    Config.token = secrets.token_urlsafe(24)

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
        say("!", "To pair a page by hand, open: %s#link=%s" % (STUDIO_URL, Config.token))
    else:
        ip = log_in()
        if ip and quiet_login():
            confirm_installed(ip)
            offer_remember(ip)
    print("\n  Studio Link is running. Leave this window open.\n"
          "  Motion Studio connects to it by itself.\n"
          "  Listening on this computer only: 127.0.0.1:%d\n"
          "  Press Ctrl+C to stop.\n" % a.port, flush=True)

    if TESTING:
        say("!", "TEST pairing link: %s#link=%s" % (STUDIO_URL, Config.token))

    def open_studio():
        if not Config.paired:                              # no page has paired yet: open one, with the token
            import webbrowser
            say("ok", "Opening Motion Studio in your browser")
            webbrowser.open(STUDIO_URL + "#link=" + Config.token)
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
