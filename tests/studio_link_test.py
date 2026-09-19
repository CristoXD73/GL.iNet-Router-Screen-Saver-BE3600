#!/usr/bin/env python3
"""Tests for tools/studio_link.py, the local helper behind Motion Studio's drop zone.

Runs the real server on an ephemeral loopback port and talks to it over HTTP, with a
fake "ssh" standing in for the router, so nothing needs a network or a router.
"""
import gzip
import http.client
import json
import os
import re
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import studio_link as sl  # noqa: E402

FAILS = 0
ORIGIN = "https://cristoxd73.github.io"


def check(cond, name):
    global FAILS
    print(("  ok    " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS += 1


def bea1(fps=8, run=8, frames=1):
    data = b"BEA1" + struct.pack("<HHI", fps, frames, sl.FRAME_BYTES)
    for i in range(frames):
        data += struct.pack("<H", run) + bytes([i % 251]) * sl.FRAME_BYTES
    return data


WORK = tempfile.mkdtemp()
FAKE = os.path.join(WORK, "fake-ssh")
with open(FAKE, "w") as f:
    f.write('#!/bin/sh\n'
            'printf "%s\\n" "$@" > "$0.args"\n'
            'env > "$0.env"\n'
            'cat > "$0.stdin"\n'
            '[ -f "$0.out" ] && cat "$0.out"\n'
            'exit "$(cat "$0.rc")"\n')
os.chmod(FAKE, os.stat(FAKE).st_mode | stat.S_IEXEC)


def set_rc(n):
    with open(FAKE + ".rc", "w") as f:
        f.write(str(n))


def forget_calls():
    for ext in (".args", ".stdin", ".env"):
        try:
            os.unlink(FAKE + ext)
        except OSError:
            pass


def set_out(text):
    """What the fake router prints (None = nothing)."""
    try:
        os.unlink(FAKE + ".out")
    except OSError:
        pass
    if text is not None:
        with open(FAKE + ".out", "w") as f:
            f.write(text)


def ssh_args():
    try:
        with open(FAKE + ".args") as f:
            return f.read().split("\n")[:-1]
    except OSError:
        return None


sl.Config.ssh = FAKE
sl.Config.router = "127.0.0.1"
sl.Config.key = None
sl.Config.dry_run = False
sl.say = lambda *a: None                 # keep the test output tidy
server = sl.make_server(0)
PORT = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()


def req(method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=15)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    raw = r.read()
    hdrs = {k.lower(): v for k, v in r.getheaders()}
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        data = None
    c.close()
    return r.status, hdrs, data


print("== who may talk to Studio Link ==")
s, h, d = req("GET", "/ping", headers={"Origin": ORIGIN})
check(s == 200 and d["app"] == "be3600-studio-link", "ping answers")
check(h.get("access-control-allow-origin") == ORIGIN, "the allowed page's origin is echoed back")
check(h.get("access-control-allow-private-network") == "true", "it answers the browser's local-network permission check")
check(req("GET", "/ping", headers={"Origin": "null"})[0] == 200, "a page opened from a local file is allowed")
check(req("GET", "/ping", headers={"Origin": "http://localhost:8123"})[0] == 200, "a page on localhost is allowed")
s, h, d = req("GET", "/ping", headers={"Origin": "https://evil.example"})
check(s == 403 and "access-control-allow-origin" not in h, "another website is refused, with no CORS headers")
check(req("GET", "/ping", headers={"Host": "evil.example", "Origin": ORIGIN})[0] == 403, "a wrong Host header is refused (DNS tricks)")
s, h, d = req("OPTIONS", "/send", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"})
check(s == 204 and h.get("access-control-allow-origin") == ORIGIN, "the browser's preflight is answered")
check(req("GET", "/nothing", headers={"Origin": ORIGIN})[0] == 404, "an unknown path is a 404")

print("== sending an animation ==")
good = bea1()
set_rc(0)
forget_calls()
s, h, d = req("POST", "/send?name=Sunset%20Test", good, {"Origin": ORIGIN, "Content-Type": "application/octet-stream"})
check(s == 200 and d["ok"] and d["name"] == "Sunset-Test", "a valid animation is sent, named from the file")
a = ssh_args()
check(a is not None and "root@127.0.0.1" in a, "it is sent to the router's address over ssh")
check(a is not None and a[-1] == "cat > /tmp/be3600-new.bea && be3600-anim set /tmp/be3600-new.bea Sunset-Test && rm -f /tmp/be3600-new.bea",
      "the router is told to save it under that name")
with open(FAKE + ".stdin", "rb") as f:
    check(f.read() == good, "the file arrives byte for byte")

forget_calls()
req("POST", "/send?name=" + "..%2F..%2Fx%3B%20rm%20-rf%20~%20%24(id)%60id%60", good, {"Origin": ORIGIN})
a = ssh_args()
m = re.search(r"be3600-anim set /tmp/be3600-new\.bea (\S+) && rm", a[-1]) if a else None
check(bool(m) and re.fullmatch(r"[A-Za-z0-9._-]+", m.group(1)) is not None, "a hostile name is reduced to safe characters (%s)" % (m.group(1) if m else "?"))

forget_calls()
sl.Config.key = "/tmp/some-key"
req("POST", "/send?name=k", good, {"Origin": ORIGIN})
a = ssh_args() or []
check("-i" in a and "/tmp/some-key" in a and "BatchMode=yes" in a, "with a key it logs in without a password prompt")
sl.Config.key = None

set_rc(255)
s, h, d = req("POST", "/send?name=x", good, {"Origin": ORIGIN})
check(s == 502 and not d["ok"], "a failed login or connection is reported as such")
set_rc(1)
s, h, d = req("POST", "/send?name=x", good, {"Origin": ORIGIN})
check(s == 422 and "3 animations" in d["message"], "a refusal (library full) explains the usual reason")
set_rc(0)

print("== files that must not be sent ==")
forget_calls()
s, h, d = req("POST", "/send?name=x", b"\0" * 5000, {"Origin": ORIGIN})
check(s == 400 and "not a .bea" in d["message"], "something that is not an animation is refused")
s, h, d = req("POST", "/send?name=x", bea1(fps=8, run=240), {"Origin": ORIGIN})
check(s == 400 and "limit is 25" in d["message"], "a loop over 25 seconds is refused")
s, h, d = req("POST", "/send?name=x", good[:-1], {"Origin": ORIGIN})
check(s == 400, "a truncated animation is refused")
s, h, d = req("POST", "/send?name=x", b"", {"Origin": ORIGIN})
check(s == 400, "an empty upload is refused")
check(ssh_args() is None, "none of those ever reached ssh")

sock = socket.create_connection(("127.0.0.1", PORT), timeout=10)
sock.sendall(("POST /send?name=x HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nOrigin: %s\r\nContent-Length: 99999999999\r\n\r\n" % (PORT, ORIGIN)).encode())
check(sock.recv(200).startswith(b"HTTP/1.1 413"), "an absurdly large upload is refused before it is read")
sock.close()

print("== dry run and the real animation ==")
sl.Config.dry_run = True
forget_calls()
s, h, d = req("POST", "/send?name=x", good, {"Origin": ORIGIN})
check(s == 200 and d.get("dryRun") and ssh_args() is None, "dry run checks the file but sends nothing")
sl.Config.dry_run = False

with gzip.open(os.path.join(ROOT, "animations", "default.bea.gz")) as f:
    bundled = f.read()
problem, seconds = sl.check_bea(bundled)
check(problem is None and abs(seconds - 25.0) < 0.01, "the bundled animation passes the check (25.0 s)")
check(sl.lib_name("Sunset Test.bea") == "Sunset-Test" and sl.lib_name("") == "animation" and len(sl.lib_name("a" * 99)) == 40,
      "names are cleaned the same way as everywhere else")

print("== showing what is on the router ==")
LIB_OK = "-\tPoison-Pikmin\t839177\t25.0\n*\tdefault\t661381\t25.0\nlimits\t3\t25\n"
sl.Config.key = "/tmp/some-key"                 # a key means: log in quietly and capture the reply
set_rc(0)
set_out(LIB_OK)
forget_calls()
s, h, d = req("GET", "/library", headers={"Origin": ORIGIN})
check(s == 200 and d["ok"] and d["max"] == 3 and d["maxSeconds"] == 25, "the library reports its limits")
check([i["name"] for i in d["items"]] == ["Poison-Pikmin", "default"], "it lists the animations on the router")
check([i["active"] for i in d["items"]] == [False, True], "it says which one is playing")
check(ssh_args()[-1] == "be3600-anim list --plain", "it asks the router for the plain list")

set_out("- \tfoo\t1\t1.0\n")                       # a router from before --plain existed
s, h, d = req("GET", "/library", headers={"Origin": ORIGIN})
check(s == 502 and "older" in d["message"], "an old router is told to update, not shown an empty list")

set_rc(255)
set_out("root@10.0.0.1: Permission denied (publickey,password).\n")
s, h, d = req("GET", "/library", headers={"Origin": ORIGIN})
check(s == 502 and "log in" in d["message"], "a failed login is reported")
set_rc(0)

set_out(None)
sl.Config.key = None
forget_calls()
s, h, d = req("GET", "/library", headers={"Origin": ORIGIN})
check(s == 200 and d.get("needPassword") and ssh_args() is None, "without a password it says so instead of prompting")
check(req("GET", "/ping", headers={"Origin": ORIGIN})[2]["loggedIn"] is False, "ping says whether Studio Link can log in")

sl.Config.dry_run = True
s, h, d = req("GET", "/library", headers={"Origin": ORIGIN})
check(s == 200 and d["dryRun"] and d["items"] == [], "dry run shows three empty slots and never calls ssh")
sl.Config.dry_run = False

print("== playing and removing ==")
sl.Config.key = "/tmp/some-key"
set_out("now playing 'default'.\n")
forget_calls()
s, h, d = req("POST", "/use?name=default", headers={"Origin": ORIGIN})
check(s == 200 and d["ok"] and ssh_args()[-1] == "be3600-anim use default", "play switches to that animation")
forget_calls()
s, h, d = req("POST", "/remove?name=Purple-silk-25s", headers={"Origin": ORIGIN})
check(s == 200 and ssh_args()[-1] == "be3600-anim remove Purple-silk-25s", "remove deletes that animation")
forget_calls()
for bad in ("a%3Bb", "x%20y", "..%2F..", "%24(id)", ""):
    s, h, d = req("POST", "/remove?name=" + bad, headers={"Origin": ORIGIN})
    check(s == 400, "a name like %r is refused" % bad)
check(ssh_args() is None, "and none of them reached ssh")
set_rc(1)
set_out("no animation called 'zzz'. You have:\n")
s, h, d = req("POST", "/use?name=zzz", headers={"Origin": ORIGIN})
check(s == 422 and "no animation called" in d["message"], "the router's own reason is passed on")
set_rc(0)
set_out(None)
sl.Config.key = None

print("== the router password ==")
PW = "p@ss w0rd $HOME `id` \"q\" 'x' ; \\ %s"
sl.Config.password = PW
sl.Config.askpass = sl.make_askpass()
set_out(LIB_OK)
forget_calls()
s, h, d = req("GET", "/library", headers={"Origin": ORIGIN})
env = {}
with open(FAKE + ".env") as f:
    for line in f:
        if "=" in line:
            k, v = line.rstrip("\n").split("=", 1)
            env[k] = v
check(s == 200 and env.get("SSH_ASKPASS") == sl.Config.askpass and env.get("SSH_ASKPASS_REQUIRE") == "force",
      "ssh is told to ask the helper program, not a terminal")
check(PW not in " ".join(ssh_args()), "the password is never on ssh's command line")
with open(sl.Config.askpass) as f:
    check(PW not in f.read(), "the password is never written into the helper program")
answer = subprocess.run([sl.Config.askpass], env=dict(os.environ, BE3600_STUDIO_PW=PW), capture_output=True, text=True).stdout
check(answer == PW + "\n", "the helper program hands ssh the password exactly, special characters and all")
check(os.stat(sl.Config.askpass).st_mode & 0o077 == 0, "only you can read or run that helper program")
check("NumberOfPasswordPrompts=1" in ssh_args(), "a wrong password is tried once, not repeatedly")
sl.Config.password = None
set_out(None)

print("== one job at a time ==")
sl.Config.key = "/tmp/some-key"
sl.Config.lock.acquire()
s, h, d = req("GET", "/library", headers={"Origin": ORIGIN})
check(s == 409 and d["busy"], "while a send is running, other jobs are told to wait")
sl.Config.lock.release()
sl.Config.key = None

server.shutdown()
print("\n" + ("%d Studio Link test(s) FAILED." % FAILS if FAILS else "All Studio Link tests passed."))
sys.exit(1 if FAILS else 0)
