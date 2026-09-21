#!/usr/bin/env python3
"""Tests the touch-driven behaviour of the screen pages against a fake touchscreen: page actions (tap, hold),
swiping between pages, alerts, autoplay, the schedule and night mode.

    python3 tests/pages_touch_test.py PATH/TO/PLAYER
"""
import os
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time

PLAYER = os.path.abspath(sys.argv[1])
COLS, ROWS, FRAME = 76, 284, 43168
NOW0 = 1789900000
EV_SYN, EV_ABS = 0, 3
passed = failed = 0


def check(ok, name, extra=""):
    global passed, failed
    if ok:
        passed += 1
        print("  ok    " + name)
    else:
        failed += 1
        print("  FAIL  " + name + (("  (" + extra + ")") if extra else ""))


def w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def frame_of(k):
    return b"".join(struct.pack("<H", k * 0x2000 + r) * COLS for r in range(ROWS))


class Rig:
    def __init__(self, pages, config="", now=NOW0, extra=(), files=None, lib=("a",)):
        self.root = tempfile.mkdtemp(prefix="be3600-ptest-")
        r = self.root
        w(r + "/proc/stat", "cpu  4000 10 1500 92000 300 20 80 0 0 0\n")
        w(r + "/proc/meminfo", "MemTotal: 900000 kB\nMemAvailable: 500000 kB\n")
        w(r + "/proc/uptime", "1000.0 900.0\n")
        w(r + "/sys/class/backlight/soc:backlight/brightness", "5\n")
        w(r + "/etc/TZ", "UTC0\n")
        w(r + "/etc/be3600-screen/config", 'PAGES="%s"\n%s\n' % (pages, config))
        self.data = r + "/data"
        os.makedirs(self.data)
        for path, text in (files or {}).items():
            w(r + path, text)
        self.lib = r + "/lib"
        os.makedirs(self.lib)
        for i, n in enumerate(lib):
            with open("%s/%s.bea" % (self.lib, n), "wb") as f:
                f.write(b"BEA1" + struct.pack("<HHI", 10, 1, FRAME) + struct.pack("<H", 5) + frame_of(i))
        shutil.copy(self.lib + "/" + lib[0] + ".bea", r + "/active.bea")
        self.fb = r + "/fb"
        self.fifo = r + "/touch"
        os.mkfifo(self.fifo)
        self.w = os.open(self.fifo, os.O_RDWR)
        env = dict(os.environ, BE3600_TESTING="1", BE3600_ROOT=r, BE3600_NOW=str(now), BE3600_FB=self.fb)
        env.pop("TZ", None)
        self.log = open(r + "/log", "w+")
        self.p = subprocess.Popen([PLAYER, "--gestures", "--touch", self.fifo, "--lib", self.lib, "--data", self.data,
                                   "--slide", "150", "--config", "/etc/be3600-screen/config"] + list(extra) + [r + "/active.bea"],
                                  env=env, stderr=self.log)
        time.sleep(0.5)

    def send(self, t, c, v):
        os.write(self.w, struct.pack("<qqHHi", 0, 0, t, c, v))

    def report(self, x, y, track=None):
        self.send(EV_ABS, 0, x)
        self.send(EV_ABS, 1, y)
        if track is not None:
            self.send(EV_ABS, 57, track)
        self.send(EV_SYN, 0, 0)

    def down(self, x=38, y=140): self.report(x, y, 0)
    def up(self):
        self.send(EV_ABS, 57, -1)
        self.send(EV_SYN, 0, 0)

    def tap(self):
        self.down()
        time.sleep(0.05)
        self.up()

    def hold(self, secs):
        self.down()
        time.sleep(secs)
        self.up()

    def swipe(self, y0, y1):
        self.down(38, y0)
        y = y0
        while y != y1:
            y += max(-10, min(10, y1 - y))
            time.sleep(0.016)
            self.report(38, y)
        time.sleep(0.03)
        self.up()

    def state(self, name):
        try:
            with open(self.data + "/" + name) as f:
                return dict(l.strip().split("=", 1) for l in f if "=" in l)
        except OSError:
            return {}

    def text(self):
        self.log.flush()
        self.log.seek(0)
        return self.log.read()

    def screen(self):
        with open(self.fb, "rb") as f:
            return f.read()

    def alive(self): return self.p.poll() is None

    def close(self):
        if self.p.poll() is None:
            self.p.send_signal(signal.SIGTERM)
            try:
                self.p.wait(2)
            except subprocess.TimeoutExpired:
                self.p.kill()
        os.close(self.w)
        shutil.rmtree(self.root, ignore_errors=True)


def run(name, fn):
    print("== " + name + " ==")
    try:
        fn()
    except Exception as e:
        global failed
        failed += 1
        print("  FAIL  scenario crashed: %r" % (e,))


def t_pomodoro():
    r = Rig("pomodoro clock")
    try:
        r.tap()
        time.sleep(0.9)
        s = r.state("pomodoro.state")
        check(s.get("state") == "1", "a tap on the timer page starts it", str(s))
        check(int(s.get("end", 0)) - NOW0 == 25 * 60, "it counts 25 minutes")
        r.tap()
        time.sleep(0.9)
        check(r.state("pomodoro.state").get("state") == "2", "a second tap pauses it")
        r.tap()
        time.sleep(0.9)
        check(r.state("pomodoro.state").get("state") == "1", "and a third resumes it")
        r.hold(1.3)
        time.sleep(0.5)
        s = r.state("pomodoro.state")
        check(s.get("state") == "0" and s.get("mode") == "0", "holding a finger down resets it", str(s))
        check(r.alive(), "the player is still running")
    finally:
        r.close()


def t_pomodoro_rings():
    r = Rig("clock pomodoro", files={"/data/pomodoro.state": "mode=0\nstate=1\ncycles=0\nend=%d\nleft=0\n" % (NOW0 - 5)})
    try:
        time.sleep(2.3)
        check("alert: Focus done" in r.text(), "a timer that has run out raises an alert even when another page is showing", r.text()[-200:])
        s = r.state("pomodoro.state")
        check(s.get("state") == "0" and s.get("mode") == "1", "and moves on to the break", str(s))
    finally:
        r.close()


def t_stopwatch():
    r = Rig("stopwatch clock")
    try:
        r.tap()
        time.sleep(0.9)
        check(r.state("stopwatch.state").get("state") == "1", "a tap starts the stopwatch")
        time.sleep(0.6)
        r.tap()
        time.sleep(0.9)
        s = r.state("stopwatch.state")
        check(s.get("state") == "0" and int(s.get("acc", 0)) > 1000, "the next tap stops it with the time kept", str(s))
        r.hold(1.3)
        time.sleep(0.4)
        check(r.state("stopwatch.state").get("acc") == "0", "holding resets it")
    finally:
        r.close()


def t_swipe_pages():
    r = Rig("clock vitals info")
    try:
        before = r.screen()
        r.swipe(100, 200)
        time.sleep(0.7)
        check("switched to vitals" in r.text(), "a swipe moves from the clock to the next page", r.text()[-160:])
        r.swipe(200, 100)
        time.sleep(0.7)
        check(r.text().count("switched to clock") >= 1, "and swiping back returns")
        r.swipe(200, 100)
        time.sleep(0.7)
        check("switched to info" in r.text(), "swiping the other way from the first page wraps to the last")
        r.tap()
        time.sleep(0.9)
        check(r.text().count("switched to clock") >= 2, "a tap on a plain page goes to the next one")
        check(open(r.data + "/page").read().strip() == "clock", "the page that is showing is remembered")
        check(r.screen() != b"" and len(r.screen()) == FRAME, "the screen holds a full picture")
    finally:
        r.close()


def t_double_tap_exit():
    r = Rig("pomodoro clock")
    try:
        r.tap()
        time.sleep(0.12)
        r.tap()
        try:
            rc = r.p.wait(1.5)
        except subprocess.TimeoutExpired:
            rc = None
        check(rc == 10, "a double-tap on an interactive page still leaves (exit code 10)", repr(rc))
        check(r.state("pomodoro.state").get("state") != "1", "and does not also start the timer")
    finally:
        r.close()


def t_start_page_remembered():
    r = Rig("clock vitals", files={"/data/page": "vitals\n"})
    try:
        time.sleep(0.4)
        r.tap()
        time.sleep(0.9)
        check("switched to clock" in r.text(), "the player starts on the page that was showing last time (a tap goes on to the next)", r.text()[-160:])
    finally:
        r.close()


def t_autoplay():
    r = Rig("clock vitals", config="AUTOPLAY_SECONDS=2")
    try:
        time.sleep(4.8)
        check("autoplay: next page" in r.text(), "with autoplay on, it moves to the next page by itself", r.text()[-200:])
        n = r.text().count("autoplay: next page")
        r.tap()
        time.sleep(0.5)
        check(r.alive(), "a touch is handled while autoplay runs")
        time.sleep(1.2)
        check(r.text().count("autoplay: next page") == n, "a touch holds autoplay off for its whole interval")
    finally:
        r.close()


def t_schedule():
    r = Rig("clock vitals", config='SCHEDULE="23:00=vitals 07:00=clock"', now=NOW0)          # 10:26: the 07:00 entry applies
    try:
        time.sleep(0.6)
        check(open(r.data + "/page").read().strip() == "clock", "between 07:00 and 23:00 the schedule starts on the clock page")
    finally:
        r.close()
    r = Rig("clock vitals", config='SCHEDULE="23:00=vitals 07:00=clock"', now=NOW0 + 13 * 3600)   # 23:26
    try:
        time.sleep(0.6)
        check(open(r.data + "/page").read().strip() == "vitals", "after 23:00 it starts on the page it names")
    finally:
        r.close()

def t_alerts():
    r = Rig("clock vitals")
    try:
        time.sleep(1.4)
        w(r.data + "/events", "%d|bad|Internet is down\n" % NOW0)
        time.sleep(1.8)
        check("alert: Internet is down" in r.text(), "a line in the helper's event file becomes a banner", r.text()[-200:])
        time.sleep(0.5)
        r.tap()
        time.sleep(1.0)
        w(r.data + "/events", "%d|bad|Internet is down\n%d|good|Back online\n" % (NOW0, NOW0))
        time.sleep(1.8)
        check("alert: Back online" in r.text(), "later events show too")
        check(r.text().count("alert: Internet is down") == 1, "an event is shown once")
        r.tap()
        time.sleep(0.5)
        check(r.alive(), "a touch dismisses a banner without harm")
    finally:
        r.close()


def t_night():
    r = Rig("clock", config='NIGHT_START=09:00\nNIGHT_END=12:00\nNIGHT_BRIGHTNESS=1', now=NOW0)   # 10:26: night
    try:
        time.sleep(21.5)
        bl = open(r.root + "/sys/class/backlight/soc:backlight/brightness").read().strip()
        check(bl == "1", "in the night hours the backlight is turned down (after 20 s without a touch)", bl)
        r.tap()
        time.sleep(1.0)
        bl = open(r.root + "/sys/class/backlight/soc:backlight/brightness").read().strip()
        check(bl == "5", "a touch wakes it to the normal level", bl)
        check("tap: next" not in r.text() and "tap: page" not in r.text(), "and that touch does nothing else")
    finally:
        r.p.send_signal(signal.SIGTERM)
        r.p.wait(3)
        bl = open(r.root + "/sys/class/backlight/soc:backlight/brightness").read().strip()
        check(bl == "5", "when the player stops it leaves the backlight as it found it", bl)
        r.close()


def t_guest_action():
    script = tempfile.mktemp(suffix=".sh")
    out = tempfile.mktemp()
    with open(script, "w") as f:
        f.write("#!/bin/sh\necho \"$@\" > %s\n" % out)
    os.chmod(script, 0o755)
    r = Rig("guest", files={"/data/guest.txt": "state=on\nssid=Guest\n"})
    try:
        r.p.send_signal(signal.SIGTERM)
        r.p.wait(2)
        env = dict(os.environ, BE3600_TESTING="1", BE3600_ROOT=r.root, BE3600_NOW=str(NOW0), BE3600_FB=r.fb, BE3600_ACTION=script)
        env.pop("TZ", None)
        r.p = subprocess.Popen([PLAYER, "--gestures", "--touch", r.fifo, "--lib", r.lib, "--data", r.data, "--config", "/etc/be3600-screen/config", r.root + "/active.bea"],
                               env=env, stderr=r.log)
        time.sleep(0.5)
        r.tap()
        time.sleep(0.9)
        check(not os.path.exists(out), "a plain tap on the guest page does not switch the network")
        r.hold(1.4)
        time.sleep(0.6)
        got = open(out).read().strip() if os.path.exists(out) else ""
        check(got == "guest toggle", "holding on the guest page runs the switch action", repr(got))
    finally:
        r.close()
        for p in (script, out):
            if os.path.exists(p):
                os.unlink(p)


def with_action(pages, config="", files=None, lib=("a",)):
    """A rig whose be3600-widget-action is a script that just records what it was asked for."""
    script = tempfile.mktemp(suffix=".sh")
    out = tempfile.mktemp()
    with open(script, "w") as f:
        f.write("#!/bin/sh\necho \"$@\" >> %s\n" % out)
    os.chmod(script, 0o755)
    r = Rig(pages, config=config, files=files, lib=lib)
    r.p.send_signal(signal.SIGTERM)
    r.p.wait(2)
    env = dict(os.environ, BE3600_TESTING="1", BE3600_ROOT=r.root, BE3600_NOW=str(NOW0), BE3600_FB=r.fb,
               BE3600_ACTION=script)
    env.pop("TZ", None)
    r.p = subprocess.Popen([PLAYER, "--gestures", "--touch", r.fifo, "--lib", r.lib, "--data", r.data,
                            "--config", "/etc/be3600-screen/config", r.root + "/active.bea"],
                           env=env, stderr=r.log)
    time.sleep(0.5)

    def asked():
        return [ln.strip() for ln in open(out)] if os.path.exists(out) else []

    def clean():
        r.close()
        for p in (script, out):
            if os.path.exists(p):
                os.unlink(p)
    return r, asked, clean


CHIMES = "state\tready\nping\t60:500 255:800\nup\t60:600 255:600\nrev\t255:2200 60:550 255:900\n"


def t_chimes_page():
    r, asked, clean = with_action("chimes", config='FAN_CHIME_TAPS=""', files={"/data/chimes.txt": CHIMES})
    try:
        r.tap()
        time.sleep(0.9)
        check(asked() == ["chime up"], "a tap moves to the next chime and plays it", str(asked()))
        r.tap()
        time.sleep(0.9)
        check(asked()[-1] == "chime rev", "the next tap moves on again", str(asked()))
        r.hold(1.4)
        time.sleep(0.6)
        check(asked()[-1] == "chime rev", "holding repeats the one you are on", str(asked()))
        check(r.alive(), "the player is still running")
    finally:
        clean()


def t_five_taps():
    r, asked, clean = with_action("clock analog aurora", config='FAN_CHIME_TAPS="rev"')
    try:
        for _ in range(4):
            r.tap()
            time.sleep(0.5)
        check(asked() == [], "four taps are just four page changes", str(asked()))
        r.tap()
        time.sleep(0.8)
        check(asked() == ["chime rev"], "the fifth tap in a row revs the fan", str(asked()))
        for _ in range(4):
            r.tap()
            time.sleep(0.5)
        check(asked() == ["chime rev"], "and the count starts again, not every tap after it", str(asked()))
    finally:
        clean()


def t_five_taps_off():
    r, asked, clean = with_action("clock analog", config='FAN_CHIME_TAPS=""')
    try:
        for _ in range(6):
            r.tap()
            time.sleep(0.5)
        check(asked() == [], "with FAN_CHIME_TAPS empty, tapping stays silent", str(asked()))
    finally:
        clean()


def t_slots_page():
    anims = "*\tsunset\t41000\t12.4\n-\teyes\t8200\t4.0\nlimits\t3\t25\n"
    r, asked, clean = with_action("slots", config='FAN_CHIME_TAPS=""',
                                  files={"/data/anims.txt": anims}, lib=("sunset", "eyes"))
    try:
        r.hold(1.4)
        time.sleep(0.6)
        check(asked() == ["anim remove:sunset"], "holding on a slot removes that animation", str(asked()))
        r.tap()
        time.sleep(0.9)
        r.hold(1.4)
        time.sleep(0.6)
        check(asked() == ["anim remove:sunset"], "a tap moves to the empty slot, and holding there does nothing",
              str(asked()))
        check(r.alive(), "the player is still running")
    finally:
        clean()


for name, fn in [
    ("Pomodoro timer", t_pomodoro),
    ("a finished timer rings from any page", t_pomodoro_rings),
    ("stopwatch", t_stopwatch),
    ("swiping between pages", t_swipe_pages),
    ("double-tap on a page with a tap action", t_double_tap_exit),
    ("the page you were on is remembered", t_start_page_remembered),
    ("autoplay", t_autoplay),
    ("schedule", t_schedule),
    ("alerts", t_alerts),
    ("night mode", t_night),
    ("guest Wi-Fi switch", t_guest_action),
    ("the fan chimes, tapped through", t_chimes_page),
    ("five taps rev the fan", t_five_taps),
    ("five taps, turned off", t_five_taps_off),
    ("the animation slots page", t_slots_page),
]:
    run(name, fn)

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
