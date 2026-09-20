#!/usr/bin/env python3
"""Tests the native player's touch handling (tap, double-tap, drag/swipe, slide) on a PC.

The player is started with --gestures against a fake touchscreen (a FIFO that this script
writes evdev events into) and an ordinary file standing in for the framebuffer. Every
picture is checked pixel for pixel. Usage:  python3 tests/gestures_test.py PATH/TO/PLAYER
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
ROWS, COLS, FRAME = 284, 76, 43168
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
ABS_X, ABS_Y, TRACKING = 0, 1, 57

passed = failed = 0


def check(ok, name, extra=""):
    global passed, failed
    if ok:
        passed += 1
        print("  ok    " + name)
    else:
        failed += 1
        print("  FAIL  " + name + (("  (" + extra + ")") if extra else ""))


def frame_of(k):
    """Animation k: every pixel in row r has the value k*0x2000 + r, so a row says where it came from."""
    return b"".join(struct.pack("<H", k * 0x2000 + r) * COLS for r in range(ROWS))


def write_bea(path, k):
    with open(path, "wb") as f:
        f.write(b"BEA1" + struct.pack("<HHI", 10, 2, FRAME))
        for _ in range(2):
            f.write(struct.pack("<H", 5) + frame_of(k))


def expected(cur_k, other_k, off):
    """What the screen must show with the current picture moved off rows toward 'next'."""
    out = []
    for r in range(ROWS):
        s = r - off
        if off >= 0:
            v = cur_k * 0x2000 + s if s >= 0 else other_k * 0x2000 + s + ROWS
        else:
            v = cur_k * 0x2000 + s if s < ROWS else other_k * 0x2000 + s - ROWS
        out.append(struct.pack("<H", v) * COLS)
    return b"".join(out)


def row0(fbdata):
    return struct.unpack("<H", fbdata[:2])[0]


class Rig:
    def __init__(self, names, extra=(), active="a"):
        self.dir = tempfile.mkdtemp(prefix="be3600-gest-")
        lib = os.path.join(self.dir, "lib")
        os.mkdir(lib)
        for i, n in enumerate(names):
            write_bea(os.path.join(lib, n + ".bea"), i)
        self.active = os.path.join(self.dir, "active.bea")
        shutil.copy(os.path.join(lib, active + ".bea"), self.active)
        self.fb = os.path.join(self.dir, "fb")
        self.fifo = os.path.join(self.dir, "touch")
        os.mkfifo(self.fifo)
        self.w = os.open(self.fifo, os.O_RDWR)
        env = dict(os.environ, BE3600_TESTING="1", BE3600_FB=self.fb)
        env.pop("TOUCH_DEVICE", None)
        self.log = open(os.path.join(self.dir, "log"), "w+")
        self.p = subprocess.Popen(
            [PLAYER, "--gestures", "--touch", self.fifo, "--lib", lib, "--slide", "200"] + list(extra) + [self.active],
            env=env, stderr=self.log)
        time.sleep(0.3)

    def send(self, typ, code, val):
        os.write(self.w, struct.pack("<qqHHi", 0, 0, typ, code, val))

    def report(self, x, y, track=None):
        self.send(EV_ABS, ABS_X, x)
        self.send(EV_ABS, ABS_Y, y)
        if track is not None:
            self.send(EV_ABS, TRACKING, track)
        self.send(EV_SYN, 0, 0)

    def down(self, x, y): self.report(x, y, 0)
    def move(self, x, y): self.report(x, y)
    def up(self):
        self.send(EV_ABS, TRACKING, -1)
        self.send(EV_SYN, 0, 0)

    def tap(self, x=38, y=140, hold=0.05):
        self.down(x, y)
        time.sleep(hold)
        self.up()

    def drag(self, y0, y1, x=38, step=8, dt=0.016):
        """Finger down at y0, dragged to y1 (does not lift)."""
        self.down(x, y0)
        y = y0
        while y != y1:
            y += max(-step, min(step, y1 - y))
            time.sleep(dt)
            self.move(x, y)

    def screen(self):
        with open(self.fb, "rb") as f:
            return f.read()

    def active_k(self):
        with open(self.active, "rb") as f:
            d = f.read()
        return (struct.unpack("<H", d[12 + 2:12 + 4])[0]) // 0x2000

    def alive(self):
        return self.p.poll() is None

    def wait_exit(self, timeout=1.5):
        try:
            return self.p.wait(timeout)
        except subprocess.TimeoutExpired:
            return None

    def text(self):
        self.log.flush()
        self.log.seek(0)
        return self.log.read()

    def close(self):
        if self.p.poll() is None:
            self.p.send_signal(signal.SIGTERM)
            try:
                self.p.wait(2)
            except subprocess.TimeoutExpired:
                self.p.kill()
        os.close(self.w)
        shutil.rmtree(self.dir, ignore_errors=True)


def run(name, fn):
    print("== " + name + " ==")
    try:
        fn()
    except Exception as e:  # a crash in one scenario must not hide the others
        global failed
        failed += 1
        print("  FAIL  scenario crashed: %r" % (e,))


def t_baseline():
    r = Rig(["a", "b", "c"])
    try:
        check(r.screen() == frame_of(0), "the first animation is on screen")
        check(r.alive(), "the player keeps running with the touchscreen open")
    finally:
        r.close()


def t_tap_next():
    r = Rig(["a", "b", "c"])
    try:
        r.tap()
        t0 = time.time()
        time.sleep(0.15)
        check(r.screen() == frame_of(0), "right after a tap nothing changes yet (it waits to see if a second tap follows)")
        seen = []
        while time.time() - t0 < 1.2:
            seen.append(r.screen())
            time.sleep(0.008)
        check(seen[-1] == frame_of(1), "a lone tap ends on the next animation")
        offs = []
        for s in seen:
            if len(s) == FRAME and s[:2] and (row0(s) // 0x2000) == 1 and s != frame_of(1):
                offs.append(ROWS - (row0(s) & 0x1FFF))
        check(len(offs) >= 4, "the switch is a slide with several steps in between", "%d steps" % len(offs))
        check(offs == sorted(offs), "the slide only ever moves one way")
        check(r.active_k() == 1, "the new animation became the active one on disk")
        check("switched to b.bea" in r.text(), "the switch is logged")
        check(r.alive(), "still playing afterwards")
    finally:
        r.close()


def t_tap_wraps():
    r = Rig(["a", "b", "c"], active="c")
    try:
        r.tap()
        time.sleep(0.9)
        check(r.screen() == frame_of(0), "after the last animation a tap wraps to the first")
    finally:
        r.close()


def t_double_tap():
    r = Rig(["a", "b", "c"])
    try:
        r.tap()
        time.sleep(0.12)
        r.tap()
        rc = r.wait_exit()
        check(rc == 10, "a double-tap leaves (exit code 10)", "got %r" % (rc,))
        check(r.screen() == frame_of(0), "and nothing was switched")
        check(r.active_k() == 0, "the active animation is unchanged")
    finally:
        r.close()


def t_two_slow_taps():
    r = Rig(["a", "b", "c"])
    try:
        r.tap()
        time.sleep(0.9)
        r.tap()
        time.sleep(0.9)
        check(r.alive(), "two taps far apart are two switches, not a double-tap")
        check(r.screen() == frame_of(2), "each one moved to the next animation")
    finally:
        r.close()


def t_drag_follows_and_commits():
    r = Rig(["a", "b", "c"])
    try:
        r.drag(100, 190)
        time.sleep(0.15)
        check(r.screen() == expected(0, 1, 78), "while dragging, the picture follows the finger (78 rows, next coming in)")
        r.move(38, 120)
        time.sleep(0.15)
        check(r.screen() == expected(0, 1, 8), "and follows it back")
        r.move(38, 200)
        time.sleep(0.15)
        check(r.screen() == expected(0, 1, 88), "and forward again")
        r.up()
        time.sleep(0.6)
        check(r.screen() == frame_of(1), "released past a quarter of the strip it slides on to the next animation")
        check(r.active_k() == 1, "and that animation is now the active one")
    finally:
        r.close()


def t_drag_snaps_back():
    r = Rig(["a", "b", "c"])
    try:
        r.drag(100, 130)
        time.sleep(0.2)
        r.up()
        time.sleep(0.5)
        check(r.screen() == frame_of(0), "a short slow drag springs back to the same animation")
        check(r.active_k() == 0, "and nothing was switched")
        check(r.alive(), "still playing")
    finally:
        r.close()


def t_fling():
    r = Rig(["a", "b", "c"])
    try:
        r.down(38, 100)
        time.sleep(0.012)
        r.move(38, 108)
        time.sleep(0.012)
        r.move(38, 122)
        time.sleep(0.012)
        r.move(38, 130)
        r.up()
        time.sleep(0.6)
        check(r.screen() == frame_of(1), "a short quick flick still switches")
    finally:
        r.close()


def t_previous():
    r = Rig(["a", "b", "c"])
    try:
        r.drag(200, 100)
        time.sleep(0.15)
        check(r.screen() == expected(0, 2, -88), "dragging the other way brings in the previous animation (the last one)")
        r.up()
        time.sleep(0.6)
        check(r.screen() == frame_of(2), "and it becomes the active one")
    finally:
        r.close()


def t_invert():
    r = Rig(["a", "b", "c"], extra=["--swipe-invert"])
    try:
        r.drag(100, 200)
        time.sleep(0.15)
        check(r.screen() == expected(0, 2, -88), "--swipe-invert swaps the directions")
        r.up()
    finally:
        r.close()


def t_swipe_across():
    r = Rig(["a", "b", "c"])
    try:
        r.down(10, 140)
        time.sleep(0.02)
        r.move(30, 141)
        time.sleep(0.02)
        r.move(50, 140)
        rc = r.wait_exit()
        check(rc == 10, "a swipe across the strip leaves (exit code 10)", "got %r" % (rc,))
    finally:
        r.close()


def t_long_press():
    r = Rig(["a", "b", "c"])
    try:
        r.down(38, 140)
        time.sleep(0.7)
        r.up()
        time.sleep(0.8)
        check(r.alive() and r.screen() == frame_of(0), "a long press does nothing")
    finally:
        r.close()


def t_jitter_is_a_tap():
    r = Rig(["a", "b", "c"])
    try:
        r.down(38, 140)
        time.sleep(0.02)
        r.move(39, 144)
        time.sleep(0.02)
        r.move(37, 138)
        time.sleep(0.02)
        r.up()
        time.sleep(0.9)
        check(r.screen() == frame_of(1), "a tap that wobbles a few pixels is still a tap")
    finally:
        r.close()


def t_single_animation():
    r = Rig(["a"])
    try:
        r.drag(100, 200)
        time.sleep(0.15)
        shown = r.screen()
        check(shown != frame_of(0) and shown[:2] != b"", "with nothing to switch to, the picture stretches a little")
        r.up()
        time.sleep(0.5)
        check(r.screen() == frame_of(0), "and springs back")
        r.tap()
        time.sleep(0.9)
        check(r.screen() == frame_of(0) and r.alive(), "a tap does not break anything")
    finally:
        r.close()


def t_touch_missing():
    d = tempfile.mkdtemp(prefix="be3600-gest-")
    try:
        write_bea(os.path.join(d, "a.bea"), 0)
        env = dict(os.environ, BE3600_TESTING="1", BE3600_FB=os.path.join(d, "fb"))
        p = subprocess.run([PLAYER, "--gestures", "--touch", os.path.join(d, "nope"), os.path.join(d, "a.bea")],
                           env=env, stderr=subprocess.DEVNULL, timeout=5)
        check(p.returncode == 11, "no usable touchscreen: exit code 11, so the caller can fall back", "got %d" % p.returncode)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_sigterm():
    r = Rig(["a", "b", "c"])
    try:
        r.p.send_signal(signal.SIGTERM)
        rc = r.wait_exit()
        check(rc == 0, "SIGTERM stops it cleanly (exit 0)", "got %r" % (rc,))
    finally:
        r.close()


def t_sigterm_mid_drag():
    r = Rig(["a", "b", "c"])
    try:
        r.drag(100, 180)
        time.sleep(0.05)
        r.p.send_signal(signal.SIGTERM)
        rc = r.wait_exit()
        check(rc == 0, "SIGTERM while a finger is dragging still stops it", "got %r" % (rc,))
    finally:
        r.close()


def t_event_flood():
    r = Rig(["a", "b", "c"])
    try:
        r.down(38, 100)
        for i in range(400):
            r.move(38, 100 + (i % 7))
        r.up()
        time.sleep(0.9)
        check(r.alive(), "a burst of 400 touch reports is handled")
    finally:
        r.close()


def t_broken_bea_neighbour():
    r = Rig(["a", "b", "c"])
    try:
        with open(os.path.join(r.dir, "lib", "b.bea"), "wb") as f:
            f.write(b"NOPE" + b"\0" * 40)
        r.tap()
        time.sleep(0.9)
        check(r.alive() and r.screen() == frame_of(2), "an unreadable neighbour is skipped")
    finally:
        r.close()


for name, fn in [
    ("playing with the touchscreen open", t_baseline),
    ("a lone tap slides to the next animation", t_tap_next),
    ("after the last one a tap wraps around", t_tap_wraps),
    ("a double-tap leaves", t_double_tap),
    ("two slow taps are not a double-tap", t_two_slow_taps),
    ("dragging follows the finger and commits", t_drag_follows_and_commits),
    ("a short drag springs back", t_drag_snaps_back),
    ("a quick flick switches", t_fling),
    ("dragging the other way goes to the previous one", t_previous),
    ("--swipe-invert", t_invert),
    ("a swipe across the strip leaves", t_swipe_across),
    ("a long press does nothing", t_long_press),
    ("a wobbly tap is still a tap", t_jitter_is_a_tap),
    ("only one saved animation", t_single_animation),
    ("no touchscreen", t_touch_missing),
    ("stopping", t_sigterm),
    ("stopping mid-drag", t_sigterm_mid_drag),
    ("a flood of touch reports", t_event_flood),
    ("a broken neighbour file", t_broken_bea_neighbour),
]:
    run(name, fn)

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
