#!/usr/bin/env python3
"""Renders every screen widget through the real player against a fake router tree and checks the result.

    python3 tests/widgets_test.py PATH/TO/PLAYER [CONTACT_SHEET.png]

Each page is drawn once with `--render` (a fixed clock, fake /proc and /sys, sample data files). The checks are:
it draws something, drawing twice gives the same picture, the pages that show data show different pictures for
different data, and the Wi-Fi QR code decodes back to the network's details (when OpenCV is available).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

PLAYER = os.path.abspath(sys.argv[1])
SHEET = sys.argv[2] if len(sys.argv) > 2 else None
COLS, ROWS, FRAME = 76, 284, 43168
NOW = 1789900000          # a fixed moment (a Saturday afternoon)

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
    os.utime(path, (NOW, NOW))          # as fresh as the fake clock, so the pages do not call it stale


def make_tree():
    root = tempfile.mkdtemp(prefix="be3600-wtest-")
    w(root + "/proc/stat", "cpu  4000 10 1500 92000 300 20 80 0 0 0\n")
    w(root + "/proc/meminfo", "MemTotal:         908468 kB\nMemFree:          262980 kB\nMemAvailable:     413360 kB\n")
    w(root + "/proc/loadavg", "0.42 0.30 0.25 1/300 12345\n")
    w(root + "/proc/uptime", "300000.55 200000.10\n")
    w(root + "/proc/net/route",
      "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
      "sta1\t00000000\t0100A8C0\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
      "br-lan\t0014A8C0\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n")
    w(root + "/proc/net/dev",
      "Inter-|   Receive                                                |  Transmit\n"
      " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
      "  sta1: 987654321  1000 0 0 0 0 0 0 123456789 900 0 0 0 0 0 0\n"
      "    lo: 100 1 0 0 0 0 0 0 100 1 0 0 0 0 0 0\n")
    w(root + "/sys/class/thermal/thermal_zone0/temp", "58300\n")
    w(root + "/sys/class/thermal/thermal_zone1/temp", "61200\n")
    w(root + "/sys/class/backlight/soc:backlight/brightness", "5\n")
    w(root + "/etc/glversion", "4.8.3\n")
    w(root + "/tmp/sysinfo/model", "GL.iNet BE3600, Inc. IPQ5332\n")
    w(root + "/etc/TZ", "UTC0\n")
    w(root + "/tmp/dhcp.leases",
      "1789907279 aa:ef:d7:9b:62:9c 192.168.20.227 iPhone 01:aa\n1789907064 20:c1:9b:09:9a:b1 192.168.20.210 CyberDeck 01:20\n")
    w(root + "/etc/be3600-screen/message", "!yellow Back at 5 pm, kettle is on\n")
    w(root + "/etc/be3600-screen/config", 'PAGES="animations clock"\nCLOCK_24H=1\nDATA_CAP_GB=50\n')
    d = root + "/data"
    w(d + "/ping.txt", "target=1.1.1.1\nms=12 14 13 11 15 -1 13 12 40 90 22 14 13 12 11 12 13 14 15 16 12 11 12 13 12 14 13 12 11 12\n")
    w(d + "/wifi.txt", "count=5\nclient|iPhone|192.168.20.227|-48|5G\nclient|CyberDeck|192.168.20.210|-61|5G\nclient|Living room TV|192.168.20.55|-72|2.4G\n"
                       "client|Kitchen speaker|192.168.20.61|-80|2.4G\nclient|Printer|192.168.20.90|-58|2.4G\n")
    w(d + "/vpn.txt", "wg0|up|42\nwg1|down|-1\n")
    w(d + "/guest.txt", "state=on\nssid=Guest-Wifi\n")
    w(d + "/wifiqr.txt", "ssid=Guest-Wifi\nenc=WPA\nkey=Tesla1234;x\n")
    w(d + "/weather.txt", "temp=14.6\ncode=61\nwind=18\nhi=17\nlo=9\nunit=C\nplace=Toronto\n")
    w(d + "/path.txt", "gw=192.168.0.1\ngw_ms=3.0\nnet_ms=18.2\ndns_ms=42\n")
    w(d + "/talkers.txt", "dev|iPhone|5200000|240000\ndev|CyberDeck|910000|80000\ndev|Living room TV|420000|12000\ndev|Printer|9000|1200\n")
    w(d + "/custom-solar.txt", "title: Solar\nbig: 3.2 kW\nline: today 14.1 kWh\nline: battery 78%\nbar: 78\nspark: 1 2 4 6 5 7 9 8 6 5\ncolor: green\n")
    w(d + "/chimes.txt", "state\tready\nping\t60:500 255:800\nup\t60:600 255:600 60:450 255:1700\n"
                         "down\t255:1500 150:500 100:500 60:700\nalert\t60:450 255:550 60:400 255:550 60:400 255:900\n"
                         "rev\t255:2200 60:550 255:900 36:500 255:1000 90:200 255:2600\nmine\t60:400 255:1200\n")
    w(d + "/anims.txt", "*\tsunset-drift\t41008\t12.4\n-\trobot-eyes\t8210\t4.0\nlimits\t3\t25\n")
    return root


def render(root, page, extra_env=None, data=None):
    fb = tempfile.mktemp(prefix="be3600-fb-")
    env = dict(os.environ, BE3600_TESTING="1", BE3600_ROOT=root, BE3600_NOW=str(NOW), BE3600_FB=fb, BE3600_DEMO="1")
    env.pop("TZ", None)
    if extra_env:
        env.update(extra_env)
    r = subprocess.run([PLAYER, "--render", page, "--data", data or root + "/data", "--config", "/etc/be3600-screen/config"],
                       env=env, capture_output=True, timeout=20)
    if r.returncode != 0:
        return None, r.stderr.decode(errors="replace")
    with open(fb, "rb") as f:
        d = f.read()
    os.unlink(fb)
    return d, ""


def to_image(fbdata):
    """The display's layout back into the picture you see: image x = 283 - row, image y = column."""
    from PIL import Image
    im = Image.new("RGB", (ROWS, COLS))
    px = im.load()
    for x in range(ROWS):
        row = ROWS - 1 - x
        for y in range(COLS):
            v = fbdata[row * COLS * 2 + y * 2] | (fbdata[row * COLS * 2 + y * 2 + 1] << 8)
            px[x, y] = (((v >> 11) & 31) * 255 // 31, ((v >> 5) & 63) * 255 // 63, (v & 31) * 255 // 31)
    return im


PAGES = ["clock", "analog", "aurora", "netspeed", "talkers", "vitals", "info", "clients", "internet", "doctor",
         "usage", "vpn", "health", "pomodoro", "stopwatch", "message", "guest", "wifiqr", "weather",
         "slots", "chimes", "custom:solar"]


def main():
    root = make_tree()
    shots = []
    print("== every page draws")
    for p in PAGES:
        d, err = render(root, p)
        check(d is not None and len(d) == FRAME, "page '%s' draws a full picture" % p, err.strip()[:120])
        if d is None:
            continue
        check(any(d[i] or d[i + 1] for i in range(0, FRAME, 2)), "page '%s' is not blank" % p)
        d2, _ = render(root, p)
        check(d2 == d, "page '%s' draws the same picture twice" % p)
        shots.append((p, d))

    print("== pages react to their data")
    a, _ = render(root, "guest")
    w(root + "/data/guest.txt", "state=off\nssid=Guest-Wifi\n")
    b, _ = render(root, "guest")
    check(a != b, "the guest Wi-Fi page shows whether the network is on or off")
    a, _ = render(root, "internet")
    w(root + "/data/ping.txt", "target=1.1.1.1\nms=-1 -1 -1 -1 -1\n")
    b, _ = render(root, "internet")
    check(a != b, "the internet page changes when replies stop")
    w(root + "/data/vpn.txt", "")
    c, _ = render(root, "vpn")
    check(c is not None, "the VPN page copes with an empty list")
    os.unlink(root + "/data/wifi.txt")
    c, _ = render(root, "clients")
    check(c is not None, "the clients page falls back to the DHCP leases without helper data")
    os.unlink(root + "/data/weather.txt")
    c, _ = render(root, "weather")
    check(c is not None, "the weather page copes with no data")
    d, _ = render(root, "clock", {"BE3600_NOW": str(NOW + 3600)})
    e, _ = render(root, "clock")
    check(d != e, "the clock changes with the time")
    w(root + "/etc/be3600-screen/message", "")
    m, _ = render(root, "message")
    check(m is not None, "the message page copes with no message")
    m2, err = render(root, "nonesuch")
    check(m2 is None, "an unknown page is refused")

    a, _ = render(root, "doctor")
    w(root + "/data/path.txt", "gw=192.168.0.1\ngw_ms=3.0\nnet_ms=-1\ndns_ms=-1\n")
    b, _ = render(root, "doctor")
    check(a != b, "the doctor page changes when the internet stops answering")
    a, _ = render(root, "chimes")
    w(root + "/data/chimes.txt", "state\tquiet\nping\t60:500 255:800\n")
    b, _ = render(root, "chimes")
    check(a != b, "the chimes page says when quiet hours mean nothing would play")
    w(root + "/data/chimes.txt", "state\tnofan\n")
    c, _ = render(root, "chimes")
    check(c is not None and c != b, "and says so on a router with no fan")
    a, _ = render(root, "slots")
    w(root + "/data/anims.txt", "limits\t3\t25\n")
    b, _ = render(root, "slots")
    check(a != b, "the slots page shows three empty slots when nothing is saved")
    a, _ = render(root, "talkers")
    w(root + "/data/talkers.txt", "")
    b, _ = render(root, "talkers")
    check(a != b, "the in-use board changes when nothing is talking")

    print("== the welcome the router gives itself once it is installed")

    def hello(at_ms):
        fb = tempfile.mktemp(prefix="be3600-fb-")
        env = dict(os.environ, BE3600_TESTING="1", BE3600_ROOT=root, BE3600_NOW=str(NOW), BE3600_FB=fb)
        env.pop("TZ", None)
        r = subprocess.run([PLAYER, "--hello", str(at_ms * 2)], env=env, capture_output=True, timeout=20)
        if r.returncode != 0:
            return None
        with open(fb, "rb") as f:
            d = f.read()
        os.unlink(fb)
        return d

    # 2380 and 3820 are the cuts in the rev, where the eyes blink; 2900 and 4300 are the stabs
    shut, wide, blink, ready = hello(150), hello(2900), hello(2380), hello(7800)
    check(all(x is not None and len(x) == FRAME for x in (shut, wide, blink, ready)),
          "it draws a full frame, and needs no animation to do it")
    check(len({shut, wide, blink, ready}) == 4, "the eyes open, look about, blink and settle")
    check(hello(2900) != hello(4300), "the two stabs of the rev do not look the same")
    if wide and blink:
        check(sum(wide) > sum(blink), "a blink really does put less on the screen than open eyes")
    if ready:
        im = to_image(ready)
        lit = sum(1 for x in range(150, ROWS) for y in range(COLS) if sum(im.getpixel((x, y))) > 90)
        check(lit > 150, "and at the end it says it is ready, beside the eyes", str(lit))

    print("== the Wi-Fi QR code")
    zbar = shutil.which("zbarimg")
    if not zbar:
        print("  skip  zbarimg (ZBar) is not installed, so the QR code is not decoded")
    else:
        def decode(page_root):
            d, _ = render(page_root, "wifiqr")
            png = tempfile.mktemp(suffix=".png")
            im = to_image(d)
            im.resize((im.width * 4, im.height * 4)).save(png)
            out = subprocess.run([zbar, "--quiet", "--raw", png], capture_output=True, text=True).stdout.rstrip("\n")
            os.unlink(png)
            return out

        cases = [("Guest-Wifi", "WPA", "Tesla1234;x", "WIFI:T:WPA;S:Guest-Wifi;P:Tesla1234\\;x;;"),
                 ("A", "WPA", "b", "WIFI:T:WPA;S:A;P:b;;"),
                 ("Home Network 5G", "WPA", "correct horse battery staple", "WIFI:T:WPA;S:Home Network 5G;P:correct horse battery staple;;"),
                 ("N" * 20, "WPA", "p" * 40, "WIFI:T:WPA;S:%s;P:%s;;" % ("N" * 20, "p" * 40)),
                 ("Open Cafe", "nopass", "", "WIFI:T:nopass;S:Open Cafe;;")]
        for ssid, enc, key, want in cases:
            w(root + "/data/wifiqr.txt", "ssid=%s\nenc=%s\nkey=%s\n" % (ssid, enc, key))
            got = decode(root)
            check(got == want, "the QR code for '%s' decodes to the network details" % ssid[:20], repr(got))
        w(root + "/data/wifiqr.txt", "ssid=%s\nenc=WPA\nkey=%s\n" % ("x" * 32, "y" * 63))
        d, _ = render(root, "wifiqr")
        check(d is not None, "a name and password too long for the screen give a message instead of a broken code")
    if SHEET and shots:
        from PIL import Image, ImageDraw
        sheet = Image.new("RGB", (ROWS * 2 + 30, (COLS + 26) * ((len(shots) + 1) // 2) + 10), (40, 40, 46))
        dr = ImageDraw.Draw(sheet)
        for i, (name, d) in enumerate(shots):
            x = 10 + (i % 2) * (ROWS + 10)
            y = 6 + (i // 2) * (COLS + 26)
            dr.text((x, y), name, fill=(200, 200, 200))
            sheet.paste(to_image(d), (x, y + 14))
        sheet.save(SHEET)
        print("  (contact sheet written to %s)" % SHEET)

    shutil.rmtree(root, ignore_errors=True)
    print("\n%d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
