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
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ["BE3600_TESTING"] = "1"        # the test hooks (fake ssh, auto-yes) only work with this
import studio_link as sl  # noqa: E402

TOKEN = "T" * 32
sl.Config.token = TOKEN

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


def req(method, path, body=None, headers=None, token=True):
    """token=True sends this run's secret token (as the paired page does); False sends none."""
    h = dict(headers or {})
    if token is True:
        h["X-Studio-Token"] = TOKEN
    elif token:
        h["X-Studio-Token"] = token
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=15)
    c.request(method, path, body=body, headers=h)
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
s, h, d = req("POST", "/remove?name=default", headers={"Origin": "null"})
check(s == 403 and "access-control-allow-origin" not in h,
      "a sandboxed page (Origin: null, which ANY website can produce) is refused, with no CORS headers")
check(req("GET", "/ping", headers={"Origin": "null"})[0] == 403, "and it cannot even ping")
check(req("GET", "/ping", headers={"Origin": "http://localhost:8123"})[0] == 200, "a page on localhost is allowed")
check(req("GET", "/ping", headers={"Origin": "http://localhost.evil.example"})[0] == 403, "localhost.evil.example is not localhost")
s, h, d = req("GET", "/ping", headers={"Origin": "https://evil.example"})
check(s == 403 and "access-control-allow-origin" not in h, "another website is refused, with no CORS headers")
check(req("GET", "/ping", headers={"Host": "evil.example", "Origin": ORIGIN})[0] == 403, "a wrong Host header is refused (DNS tricks)")
s, h, d = req("OPTIONS", "/send", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"})
check(s == 204 and h.get("access-control-allow-origin") == ORIGIN, "the browser's preflight is answered")
check(req("GET", "/nothing", headers={"Origin": ORIGIN})[0] == 404, "an unknown path is a 404")

print("== the secret token (other programs and other users cannot drive it) ==")
sl.Config.paired = False
s, h, d = req("GET", "/ping", headers={"Origin": ORIGIN}, token=False)
check(s == 200 and d == {"ok": True, "app": "be3600-studio-link", "version": 3, "needToken": True},
      "without the token, ping only says 'I am Studio Link, pair with me': no router address, nothing else")
check(sl.Config.paired is False, "and that does not count as paired")
s, h, d = req("GET", "/ping", headers={"Origin": ORIGIN}, token="wrong-" + TOKEN[6:])
check(s == 200 and d.get("needToken") and "router" not in d, "a wrong token is treated as no token")
for method, path in (("GET", "/library"), ("POST", "/use?name=default"), ("POST", "/remove?name=default"), ("POST", "/send?name=x")):
    s, h, d = req(method, path, body=b"x" if method == "POST" else None, headers={"Origin": ORIGIN}, token=False)
    check(s == 401 and d.get("needToken"), "%s %s without the token is refused (401)" % (method, path.split("?")[0]))
s, h, d = req("GET", "/library", headers={}, token=False)
check(s == 401, "a plain program with no Origin (curl, another user) is refused too")
s, h, d = req("POST", "/remove?name=default", headers={"Origin": ORIGIN}, token="a" * 32)
check(s == 401, "a wrong token cannot remove anything")
s, h, d = req("GET", "/ping", headers={"Origin": ORIGIN})
check(s == 200 and d.get("authed") and d["version"] == 3 and "router" in d, "with the token, ping gives the full picture")
check(sl.Config.paired is True, "and only then counts as paired (so Studio Link does not open another tab)")
s, h, d = req("OPTIONS", "/send", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST",
                                            "Access-Control-Request-Headers": "x-studio-token"}, token=False)
check(s == 204 and "X-Studio-Token" in h.get("access-control-allow-headers", ""), "the browser's preflight allows the token header")
check(sl.token_ok(TOKEN) and not sl.token_ok("") and not sl.token_ok(None) and not sl.token_ok(TOKEN + "x"), "the token check is exact")

print("== Motion Studio served from this computer (Safari on a Mac cannot use the website) ==")


def raw_get(path, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=15)
    c.request("GET", path, headers=dict(headers or {}))
    r = c.getresponse()
    body = r.read()
    hdrs = {k.lower(): v for k, v in r.getheaders()}
    c.close()
    return r.status, hdrs, body


with open(os.path.join(ROOT, "studio", "index.html"), "rb") as f:
    INDEX = f.read()
s, h, b = raw_get("/")
check(s == 200 and b == INDEX and h.get("content-type", "").startswith("text/html"), "/ is Motion Studio, without any token")
check(h.get("cache-control") == "no-store" and h.get("x-content-type-options") == "nosniff" and h.get("x-frame-options") == "DENY",
      "served with no-store, nosniff and no framing")
s, h, b = raw_get("/vendor/three.min.js")
check(s == 200 and h.get("content-type", "").startswith("text/javascript") and len(b) > 100000, "its scripts are served")
check(raw_get("/fan.html")[0] == 200 and raw_get("/shared.css")[0] == 200 and raw_get("/shared.js")[0] == 200,
      "and Fan Studio with the shared files")
for bad in ("/../tools/studio_link.py", "/%2e%2e/README.md", "/get.html", "/downloads", "/vendor/", "/vendor/../index.html",
            "/tools/studio_link.py", "//etc/passwd", "/index.html/", "/studio-key"):
    check(raw_get(bad)[0] in (302, 404) and b"def " not in raw_get(bad)[2], "nothing outside it: %s" % bad)
s, h, b = raw_get("/downloads/studio-link.py")
check(s == 302 and h.get("location") == sl.DEFAULT_STUDIO_URL + "downloads/studio-link.py", "its download links go to the website")
check(raw_get("/", {"Host": "evil.example"})[0] == 403, "a wrong Host header is refused here too (DNS tricks)")
check(raw_get("/", {"Origin": "https://evil.example"})[0] == 403, "and another website cannot fetch it")
check(sl.use_local_studio("auto", "darwin") and not sl.use_local_studio("auto", "linux")
      and sl.use_local_studio("local", "linux") and not sl.use_local_studio("web", "darwin"),
      "a Mac opens the copy served here; elsewhere the website, unless told otherwise")
sl.Config.local_studio, sl.Config.port = True, 8791
check(sl.studio_url() == "http://127.0.0.1:8791/", "the address it opens on a Mac is http://127.0.0.1:8791/")
sl.Config.local_studio = False
check(sl.studio_url() == sl.STUDIO_URL, "and the website otherwise")
for page in ("index.html", "fan.html"):
    with open(os.path.join(ROOT, "studio", page), encoding="utf-8") as f:
        html = f.read()
    check("connect-src 'self' http://127.0.0.1:8791" in html and "location.origin" in html,
          "%s talks to the Studio Link that served it, and its CSP allows that" % page)

print("== sending an animation ==")
good = bea1()
set_rc(0)
forget_calls()
s, h, d = req("POST", "/send?name=Sunset%20Test", good, {"Origin": ORIGIN, "Content-Type": "application/octet-stream"})
check(s == 200 and d["ok"] and d["name"] == "Sunset-Test", "a valid animation is sent, named from the file")
a = ssh_args()
check(a is not None and "root@127.0.0.1" in a, "it is sent to the router's address over ssh")
check(a is not None and a[-1] == "T=$(mktemp /tmp/be3600-new.XXXXXX) && cat > $T && be3600-anim set $T Sunset-Test; R=$?; rm -f $T; exit $R",
      "the router is told to save it under that name, through a fresh private temp file")
with open(FAKE + ".stdin", "rb") as f:
    check(f.read() == good, "the file arrives byte for byte")

forget_calls()
req("POST", "/send?name=" + "..%2F..%2Fx%3B%20rm%20-rf%20~%20%24(id)%60id%60", good, {"Origin": ORIGIN})
a = ssh_args()
m = re.search(r"be3600-anim set \$T (\S+); R=", a[-1]) if a else None
check(bool(m) and re.fullmatch(r"[A-Za-z0-9._-]+", m.group(1)) is not None, "a hostile name is reduced to safe characters (%s)" % (m.group(1) if m else "?"))

forget_calls()
sl.Config.key = "/tmp/some-key"
req("POST", "/send?name=k", good, {"Origin": ORIGIN})
a = ssh_args() or []
check("-i" in a and "/tmp/some-key" in a and "BatchMode=yes" in a and "IdentitiesOnly=yes" in a,
      "with a key it logs in without a password prompt, using only that key")
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
sock.sendall(("POST /send?name=x HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nOrigin: %s\r\nX-Studio-Token: %s\r\nContent-Length: 99999999999\r\n\r\n" % (PORT, ORIGIN, TOKEN)).encode())
check(sock.recv(200).startswith(b"HTTP/1.1 413"), "an absurdly large upload is refused before it is read")
sock.close()
sock = socket.create_connection(("127.0.0.1", PORT), timeout=10)
sock.sendall(("POST /send?name=x HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nOrigin: %s\r\nContent-Length: 99999999999\r\n\r\n" % (PORT, ORIGIN)).encode())
check(sock.recv(200).startswith(b"HTTP/1.1 401"), "and without the token it is turned away before anything is read")
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
check(s == 502 and "out of date" in d["message"], "an old router is told to update, not shown an empty list")

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

print("== sending a fan chime ==")
set_out("saved 'mine'. Hear it with:  be3600-fan chime mine\n")
forget_calls()
s, h, d = req("POST", "/chime?name=mine", b"60:500 255:900", {"Origin": ORIGIN})
check(s == 200 and d["ok"] and ssh_args()[-1] == "be3600-fan save mine '60:500 255:900'",
      "a chime is saved on the router")
forget_calls()
s, h, d = req("POST", "/chime?name=wrap", b"  60:500\n255:900  \n", {"Origin": ORIGIN})
check(s == 200 and ssh_args()[-1] == "be3600-fan save wrap '60:500 255:900'", "line breaks and spare spaces are tidied")
forget_calls()
for name, body in (("a%3Bb", b"60:500"), ("..%2F..", b"60:500"), ("", b"60:500"),
                   ("ok", b"60:500; reboot"), ("ok", b"$(id)"), ("ok", b"60:500 '; rm -rf /"), ("ok", b"")):
    s, h, d = req("POST", "/chime?name=" + name, body, {"Origin": ORIGIN})
    check(s == 400, "a chime called %r with %r is refused" % (name, body))
check(ssh_args() is None, "and none of them reached ssh")
s, h, d = req("POST", "/chime?name=big", b"60:500 " * 600, {"Origin": ORIGIN})
check(s == 400, "and a body far too long for a chime is refused")
set_rc(1)
set_out("the router already keeps 8 chimes of your own.\n")
s, h, d = req("POST", "/chime?name=ninth", b"60:500", {"Origin": ORIGIN})
check(s == 422 and "already keeps 8" in d["message"], "the router's own reason is passed on")
set_rc(0)
set_out(None)

print("== choosing the screen pages (Motion Studio's widget list) ==")
sl.Config.key = "/nonexistent-key"                 # a quiet login, so the pages can be read
set_out("order\tanimations clock vitals\n*\tanimations\tyour saved animations\n*\tclock\tbig clock and date\n"
        "-\twifiqr\tQR code to join a Wi-Fi network\n*\tvitals\tCPU, memory\n")
forget_calls()
s, h, d = req("GET", "/pages", headers={"Origin": ORIGIN})
check(s == 200 and d["ok"] and d["order"] == ["animations", "clock", "vitals"] and
      [p["name"] for p in d["pages"]] == ["animations", "clock", "wifiqr", "vitals"] and
      [p["on"] for p in d["pages"]] == [True, True, False, True] and ssh_args()[-1] == "be3600-anim pages --plain",
      "the router's pages are listed, on and off, in order")
check(req("GET", "/pages", headers={"Origin": ORIGIN}, token=False)[0] == 401, "not without the token")
s, h, d = req("GET", "/ping", headers={"Origin": ORIGIN})
check(d.get("pages") is True, "a paired page is told this Studio Link can choose pages")
set_rc(1)
set_out("usage: be3600-anim pages [list | set ...]\n")
s, h, d = req("GET", "/pages", headers={"Origin": ORIGIN})
check(s == 502 and "too old" in d["message"], "a router with an older screen saver is told to update")
set_rc(0)
set_out("pages: animations clock\n")
forget_calls()
s, h, d = req("POST", "/pages", b"animations  clock\n", {"Origin": ORIGIN})
check(s == 200 and d["ok"] and ssh_args()[-1] == "be3600-anim pages set 'animations clock'", "a choice of pages is saved on the router")
forget_calls()
for body in (b"", b"clock; reboot", b"$(id)", b"clock 'x", b"a" * 41, b"x " * 60):
    s, h, d = req("POST", "/pages", body, {"Origin": ORIGIN})
    check(s == 400, "pages %r are refused" % body[:20])
check(ssh_args() is None, "and none of them reached ssh")
check(req("POST", "/pages", b"clock", {"Origin": "https://evil.example"})[0] == 403, "another website cannot change them")
set_rc(1)
set_out("unknown page 'nope'. Run: be3600-anim pages\n")
s, h, d = req("POST", "/pages", b"nope", {"Origin": ORIGIN})
check(s == 422 and "unknown page" in d["message"], "the router's own reason is passed on")
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

print("== putting the screen saver on the router ==")
import base64  # noqa: E402
import io  # noqa: E402
import tarfile  # noqa: E402

CALLS = []
_real_run_ssh = sl.run_ssh


def _recording_run_ssh(ip, remote, stdin_path=None):
    body = open(stdin_path, "rb").read() if stdin_path else None
    CALLS.append((remote, body))
    return _real_run_ssh(ip, remote, stdin_path)


sl.run_ssh = _recording_run_ssh
os.environ["BE3600_ASSUME_YES"] = "1"
sl.Config.key = "/tmp/some-key"

path, pid = sl.get_payload()
names = tarfile.open(path, "r:gz").getnames() if path else []
check(pid == "dev" and "setup/router-install.sh" in names and "router/usr/sbin/be3600-anim" in names
      and "animations/default.bea.gz" in names, "from a clone it packs router/, setup/ and animations/")
os.unlink(path)

buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:gz") as t:
    ti = tarfile.TarInfo("setup/router-install.sh")
    ti.size = 2
    t.addfile(ti, io.BytesIO(b"#\n"))
packed = buf.getvalue()

set_rc(0)
set_out("HAVE-ANIM\nLIST-OK\n")                     # installed, no version file (an older install)
CALLS.clear()
sl.confirm_installed("10.0.0.1")
check(len(CALLS) == 1, "from a clone, an installed router is left alone")

sl.PAYLOAD = ("abc123abc123", base64.b64encode(packed).decode())
set_out("abc123abc123\nHAVE-ANIM\nLIST-OK\n")
CALLS.clear()
sl.confirm_installed("10.0.0.1")
check(len(CALLS) == 1, "a router with the same version is left alone")

set_out("oldversion\nHAVE-ANIM\nLIST-OK\n")
CALLS.clear()
sl.confirm_installed("10.0.0.1")
check(len(CALLS) == 2 and "BE3600_VERSION=abc123abc123 sh setup/router-install.sh" in CALLS[1][0]
      and CALLS[1][0].startswith("D=$(mktemp -d /tmp/be3600-setup.") and "rm -rf $D; exit $R" in CALLS[1][0] and "gunzip -c | tar xf -" in CALLS[1][0],
      "an older version is updated by unpacking the bundle and running the router installer")
check(CALLS[1][1] == packed, "the bundle arrives byte for byte")

set_out("HAVE-ANIM\n")                              # be3600-anim exists but is too old for --plain
CALLS.clear()
sl.confirm_installed("10.0.0.1")
check(len(CALLS) == 2, "a router whose software predates the plain list is updated")

set_out("")                                          # nothing there at all
CALLS.clear()
sl.confirm_installed("10.0.0.1")
check(len(CALLS) == 2 and "router-install.sh" in CALLS[1][0], "a router without the screen saver gets it installed")

del os.environ["BE3600_ASSUME_YES"]
set_out("")
CALLS.clear()
sl.confirm_installed("10.0.0.1")
check(len(CALLS) == 1, "without a person to say yes (no terminal), nothing is installed")

set_rc(3)
set_out("ERROR: no /dev/fb0\n")
check(sl.install_on_router("10.0.0.1", "/dev/null", "x") is False, "a router that is not a BE3600 is reported, not installed")
set_rc(0)
sl.PAYLOAD = None
sl.Config.key = None

print("== remembering this computer (the key is locked; its passphrase lives in the keychain) ==")
VAULT = {}
sl.vault_kind = lambda: "fake"
sl.vault_store = lambda s: VAULT.__setitem__("pw", s)
sl.vault_load = lambda: VAULT.get("pw")
sl.vault_clear = lambda: VAULT.clear()

sl.KEY_FILE = os.path.join(WORK, "studio-key")
os.environ["BE3600_ASSUME_YES"] = "1"
sl.Config.password = "hunter2"
sl.Config.askpass = sl.make_askpass()
set_rc(0)
set_out("studio-ok\n")
CALLS.clear()
sys.stdin = io.StringIO("")                              # no terminal: it must never wait for typing


def key_opens_with(phrase):
    return subprocess.run(["ssh-keygen", "-y", "-P", phrase, "-f", sl.KEY_FILE], capture_output=True).returncode == 0


if not shutil.which("ssh-keygen"):
    print("  skip  ssh-keygen is not installed here; the key tests are skipped")
else:
    sl.offer_remember("10.0.0.1")
    if os.path.exists(sl.KEY_FILE):
        phrase = VAULT.get("pw")
        add = next((c for c in CALLS if "authorized_keys" in c[0]), None)
        pub = open(sl.KEY_FILE + ".pub", "rb").read()
        check(add is not None and add[1] == pub, "it sends the public key (and only that) to the router")
        check(add is not None and "chmod 600" in add[0] and sl.KEY_COMMENT in add[0] and "sed -i" in add[0],
              "the router file is locked down, and an older key from this computer is replaced")
        check(sl.Config.key == sl.KEY_FILE and sl.Config.password is None and sl.Config.key_pass == phrase,
              "it switches to the key and lets go of the password")
        check(bool(phrase) and len(phrase) >= 32, "the key's passphrase is long and random")
        check(b"PRIVATE KEY" in open(sl.KEY_FILE, "rb").read() and os.stat(sl.KEY_FILE).st_mode & 0o077 == 0,
              "the private key stays on this computer, readable only by you")
        check(not key_opens_with("") and key_opens_with(phrase),
              "the key file is encrypted: it does not open without the passphrase held in the keychain")
        a = ssh_args() or []
        env = {}
        with open(FAKE + ".env") as f:
            for line in f:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    env[k] = v
        check("BatchMode=yes" not in a and "NumberOfPasswordPrompts=1" in a and "IdentitiesOnly=yes" in a,
              "with the locked key ssh is told to ask for its passphrase (and use only that key)")
        check(env.get("BE3600_STUDIO_PW") == phrase and env.get("SSH_ASKPASS_REQUIRE") == "force" and phrase not in " ".join(a),
              "the passphrase reaches ssh only through the environment of that one process, never a command line")

        sl.Config.key, sl.Config.key_pass = None, None
        forget_calls()
        CALLS.clear()
        check(sl.log_in() == "127.0.0.1" and sl.Config.key == sl.KEY_FILE and sl.Config.key_pass == phrase,
              "next time it logs in with the key and the keychain's passphrase, asking nothing")

        sl.forget("10.0.0.1")
        gone = next((c for c in CALLS if "sed -i" in c[0] and "authorized_keys" in c[0]), None)
        check(gone is not None and not os.path.exists(sl.KEY_FILE) and not os.path.exists(sl.KEY_FILE + ".pub") and not VAULT,
              "forgetting removes the key from the router, from this computer and from the keychain")
    else:
        check(False, "it creates a key file")

    # a key remembered by an earlier version has no passphrase: it is locked on the next start
    sl.Config.key, sl.Config.key_pass, sl.Config.password = None, None, None
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", sl.KEY_FILE], check=True, capture_output=True)
    set_out("studio-ok\n")
    check(key_opens_with(""), "(setup) an old-style key with no passphrase")
    sl.log_in()
    check(VAULT.get("pw") and not key_opens_with("") and key_opens_with(VAULT["pw"]) and sl.Config.key_pass == VAULT["pw"],
          "an older, unlocked key is locked in place on the next start, with its passphrase kept in the keychain")
    for p in (sl.KEY_FILE, sl.KEY_FILE + ".pub"):
        try:
            os.unlink(p)
        except OSError:
            pass
    VAULT.clear()

    sl.Config.key, sl.Config.key_pass, sl.Config.password = None, None, "hunter2"     # the router does not accept the key
    set_out("Permission denied\n")
    sl.offer_remember("10.0.0.1")
    check(sl.Config.key is None and sl.Config.password == "hunter2" and not os.path.exists(sl.KEY_FILE) and not VAULT,
          "if the key does not work it cleans up everything (key, passphrase) and keeps the password")

    # A remembered key that stopped working (a reset router, or on a Mac a key whose keychain
    # entry was filed under an older network name) is replaced after the password works,
    # instead of blocking "Remember this computer" for good.
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "old-phrase", "-C", "be3600-studio-link-Old-Name",
                    "-f", sl.KEY_FILE], check=True, capture_output=True)
    VAULT.clear()
    sl.KEY_COMMENT = sl.key_comment()
    check(sl.KEY_COMMENT == "be3600-studio-link-Old-Name", "the key's label is read back from the key itself")
    sl.Config.key, sl.Config.key_pass, sl.Config.password = None, None, "hunter2"
    set_out("Permission denied\n")
    sl.log_in()                                                  # the old key fails; there is no terminal
    check(sl.Config.stale_key is True and sl.Config.key is None, "(setup) the old key no longer gets in")
    sl.Config.password = "hunter2"
    set_out("studio-ok\n")
    sl.offer_remember("10.0.0.1")
    new_pub = open(sl.KEY_FILE + ".pub").read()
    check(sl.Config.key == sl.KEY_FILE and "Old-Name" not in new_pub and sl.KEY_COMMENT in new_pub
          and key_opens_with(VAULT.get("pw", "-")) and sl.Config.stale_key is False,
          "a key that stopped working is replaced by a new one, offered again after the password")
    for p in (sl.KEY_FILE, sl.KEY_FILE + ".pub"):
        os.unlink(p)
    VAULT.clear()
    sl.KEY_COMMENT = sl.key_comment()

    sl.vault_kind = lambda: None                                # a computer with no keychain
    sl.Config.key, sl.Config.password = None, "hunter2"
    set_out("studio-ok\n")
    CALLS.clear()
    sl.offer_remember("10.0.0.1")
    check(not os.path.exists(sl.KEY_FILE) and not CALLS, "with no keychain it does not remember (it will not store a key unlocked)")
    sl.vault_kind = lambda: "fake"

with open(sl.KEY_FILE, "w") as f:
    f.write("stale")
sl.Config.key, sl.Config.key_pass, sl.Config.password = None, None, None
set_out("Permission denied\n")
sl.Config.router = "127.0.0.1"
sl.log_in()
check(sl.Config.key is None and sl.Config.key_pass is None, "a remembered key the router no longer accepts is dropped (it falls back to the password)")
os.unlink(sl.KEY_FILE)
os.environ.pop("BE3600_ASSUME_YES", None)
sl.run_ssh = _real_run_ssh
sl.Config.password = None
set_out(None)

print("== on a Mac ==")
BIN = os.path.join(WORK, "macbin")
os.makedirs(BIN, exist_ok=True)
with open(os.path.join(BIN, "scutil"), "w") as f:
    f.write('#!/bin/sh\n[ "$2" = LocalHostName ] && echo "Jos\\xc3\\xa9s MacBook Pro" && exit 0\nexit 1\n')
os.chmod(os.path.join(BIN, "scutil"), 0o755)
old_path = os.environ["PATH"]
os.environ["PATH"] = BIN + os.pathsep + old_path
name = sl.fresh_key_comment("darwin")
os.environ["PATH"] = old_path
check(name.startswith("be3600-studio-link-Jos") and sl.KEY_COMMENT_SHAPE.match(name),
      "a Mac's key is labelled with its Sharing name (scutil), which does not change from network to network")
check(sl.KEY_COMMENT_SHAPE.match(sl.fresh_key_comment("linux")) is not None, "elsewhere with its host name, made safe")
pub = os.path.join(WORK, "evil.pub")
with open(pub, "w") as f:
    f.write("ssh-ed25519 AAAA be3600-studio-link-x/d;reboot\n")
check(sl.key_comment(pub, "linux") == sl.fresh_key_comment("linux"),
      "a label read back from a key file must still be plain (it ends up in a command on the router)")
check("Local Network" in sl.local_network_hint("darwin") and sl.local_network_hint("linux") == "",
      "a Mac that cannot find the router is pointed at the Local Network privacy switch")
names = []
for n in ("router/usr/bin/x", "router/.DS_Store", "router/._x", "setup/__pycache__/a.pyc", "animations/Thumbs.db"):
    ti = tarfile.TarInfo(n)
    ti.uid, ti.uname = 501, "me"
    r = sl.packable(ti)
    names.append(r.name if r else None)
    if r:
        check(r.uid == 0 and r.uname == "", "files go to the router owned by root, not by uid 501")
check(names == ["router/usr/bin/x", None, None, None, None], "Finder's .DS_Store and ._ files never go to the router")
said = []
real_say, sl.say = sl.say, lambda *a: said.append(a)
for exc in (BrokenPipeError(), ConnectionResetError(), socket.timeout()):
    try:
        raise exc
    except Exception:
        server.handle_error(None, ("127.0.0.1", 1))
check(not said, "a page hanging up mid-request is not reported (no tracebacks in the window)")
try:
    raise ValueError("boom")
except Exception:
    server.handle_error(None, ("127.0.0.1", 1))
check(len(said) == 1 and "boom" in str(said[0]), "anything else is reported in one line")
sl.say = real_say

print("== router addresses can never act as options or commands ==")
check(all(sl.valid_host(h) for h in ("192.168.8.1", "router.lan", "my-router.example.com")), "ordinary addresses and names pass")
check(not any(sl.valid_host(h) for h in ("", "-oProxyCommand=x", "-l", "a b", "1.2.3.4;id", "1.2.3.4 & calc", "$(id)", "a\nb", "1.2.3.4|x", "../x")),
      "option-like or command-like text is refused")
STATE_TMP = os.path.join(WORK, "router-state")
with open(STATE_TMP, "w") as f:
    f.write("-oProxyCommand=touch /tmp/pwned\n")
_probed = []
_save = (sl.STATE_FILE, sl.default_gateway, sl.ssh_open, sl.Config.router, sl.Config.found)
sl.STATE_FILE = STATE_TMP
sl.default_gateway = lambda: "1.2.3.4; rm -rf /"
sl.ssh_open = lambda ip, timeout=1.5: (_probed.append(ip), False)[1]
sl.Config.router, sl.Config.found = None, None
check(sl.find_router() is None and _probed == ["192.168.8.1"],
      "a hostile saved-address file or gateway text is never probed or passed to ssh (only the factory address was tried)")
sl.STATE_FILE, sl.default_gateway, sl.ssh_open, sl.Config.router, sl.Config.found = _save
check(sl.is_home_address("192.168.8.1") and sl.is_home_address("10.1.2.3") and sl.is_home_address("172.16.0.5")
      and sl.is_home_address("router.lan") and not sl.is_home_address("8.8.8.8") and not sl.is_home_address("172.32.0.1"),
      "a password is only sent without a warning to a home/office address")

print("== test hooks, origins and addresses only from safe settings ==")


def in_fresh_python(env_extra, code):
    env = {k: v for k, v in os.environ.items() if k != "BE3600_TESTING"}
    env.update(env_extra)
    r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); import studio_link as sl\n%s" % (os.path.join(ROOT, "tools"), code)],
                       capture_output=True, text=True, env=env)
    return r.stdout.strip()


check(in_fresh_python({"BE3600_SSH": "/tmp/evil", "BE3600_ASSUME_YES": "1"},
                      "print(sl.Config.ssh, sl.yes_no('x'))") == "ssh False",
      "without BE3600_TESTING a stand-in ssh and an automatic 'yes' are ignored")
check(in_fresh_python({"BE3600_TESTING": "1", "BE3600_SSH": "/tmp/fake"}, "print(sl.Config.ssh)") == "/tmp/fake",
      "with BE3600_TESTING they work (that is how these tests run)")
check(in_fresh_python({"STUDIO_LINK_ORIGINS": "null, https://a.example, javascript:x, bad origin, http://[::1]"},
                      "print(sorted(sl.ALLOWED_ORIGINS))") == "['https://a.example', 'https://cristoxd73.github.io', 'null']",
      "extra origins must look like origins; 'null' has to be asked for by name")
check(in_fresh_python({}, "print('null' in sl.ALLOWED_ORIGINS)") == "False", "'null' is not allowed by default")
for bad in ("javascript:alert(1)", "file:///C:/x.html", "http://evil.example/x/", "https://evil.example/x y", "data:text/html,x"):
    check(in_fresh_python({"STUDIO_LINK_URL": bad}, "print(sl.STUDIO_URL == sl.DEFAULT_STUDIO_URL)") == "True",
          "the page address to open ignores %r" % bad)
check(in_fresh_python({"STUDIO_LINK_URL": "https://example.github.io/fork/studio/"}, "print(sl.STUDIO_URL)") == "https://example.github.io/fork/studio/",
      "a fork's https address is accepted")

print("== a flood of connections cannot exhaust this computer ==")
held = []
for _ in range(12):
    sl.BoundedServer._slots.acquire()
    held.append(1)
try:
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=5)
    c.request("GET", "/ping", headers={"Origin": ORIGIN, "X-Studio-Token": TOKEN})
    check(c.getresponse().status == 503, "when all its connection slots are busy, more are turned away (503)")
    c.close()
finally:
    for _ in held:
        sl.BoundedServer._slots.release()
check(req("GET", "/ping", headers={"Origin": ORIGIN})[0] == 200, "and it serves normally again straight after")

server.shutdown()
print("\n" + ("%d Studio Link test(s) FAILED." % FAILS if FAILS else "All Studio Link tests passed."))
sys.exit(1 if FAILS else 0)
