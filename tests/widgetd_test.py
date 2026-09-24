#!/usr/bin/env python3
"""Tests be3600-widgetd (the collector for the screen pages) and be3600-widget-action with fake router commands.

    python3 tests/widgetd_test.py            (needs a POSIX sh; runs anywhere the shell tests run)
"""
import os
import stat
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WIDGETD = os.path.join(HERE, "..", "router", "usr", "bin", "be3600-widgetd")
ACTION = os.path.join(HERE, "..", "router", "usr", "sbin", "be3600-widget-action")
passed = failed = 0


def check(ok, name, extra=""):
    global passed, failed
    if ok:
        passed += 1
        print("  ok    " + name)
    else:
        failed += 1
        print("  FAIL  " + name + (("  (" + extra + ")") if extra else ""))


def script(path, body):
    with open(path, "w") as f:
        f.write("#!/bin/sh\n" + body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


def make_env(pages, extra_conf="", ping_ok=False):
    root = tempfile.mkdtemp(prefix="be3600-wdtest-")
    bin_ = root + "/bin"
    os.makedirs(bin_)
    data = root + "/data"
    state = root + "/uci.state"
    with open(state, "w") as f:
        f.write("wireless.guest2g.guest=1\nwireless.guest2g.ssid=Guest-Wifi\nwireless.guest2g.key=Tesla1234\n"
                "wireless.guest2g.encryption=psk2+ccmp\nwireless.wifi5g.ssid=Home\nwireless.wifi5g.key=secret\n")
    # ping: the first reply works, then three are lost, then it works again (ping_ok: always works)
    if ping_ok:
        script(bin_ + "/ping", 'echo "64 bytes from x: icmp_seq=1 ttl=57 time=12.5 ms"\n')
    else:
     script(bin_ + "/ping", 'C=$(cat %s/pingcount 2>/dev/null || echo 0); echo $((C+1)) > %s/pingcount\n'
                           'if [ $C -ge 1 ] && [ $C -le 3 ]; then exit 1; fi\necho "64 bytes from 1.1.1.1: icmp_seq=1 ttl=57 time=12.5 ms"\n' % (root, root))
    script(bin_ + "/iw", 'case "$*" in\n"dev") printf "phy#1\\n\\tInterface wlan1\\n\\t\\ttype AP\\n";;\n'
                         '"dev wlan1 info") echo "channel 36 (5180 MHz)";;\n'
                         '"dev wlan1 station dump") printf "Station aa:ef:d7:9b:62:9c (on wlan1)\\n\\tsignal:  \\t-48 [-50, -49] dBm\\n'
                         'Station 20:c1:9b:09:9a:b1 (on wlan1)\\n\\tsignal:  \\t-61 [-62, -64] dBm\\n";;\nesac\n')
    script(bin_ + "/wg", 'case "$1 $2" in\n"show interfaces") echo wg0;;\n"show wg0") printf "peerkey\\t%d\\n" $(($(date +%s) - 42));;\nesac\n')
    script(bin_ + "/uci", 'S=' + state + '\nQ=""; [ "$1" = -q ] && shift\ncase "$1" in\n'
           'show) sed "s/^/wireless./;s/^wireless\\.wireless\\./wireless./" $S | sed "s/=\\(.*\\)/=\x27\\1\x27/";;\n'
           'get) K="$2"; grep "^$K=" $S | head -n 1 | cut -d= -f2-;;\n'
           'set) K="${2%%=*}"; V="${2#*=}"; grep -v "^$K=" $S > $S.n; echo "$K=$V" >> $S.n; mv $S.n $S;;\n'
           'commit) :;;\nesac\n')
    script(bin_ + "/wifi", 'echo reload >> %s/wifi.log\n' % root)
    script(bin_ + "/ip", 'if [ "$1 $2 $3" = "route show default" ]; then echo "default via 192.168.0.1 dev sta1 proto static"; fi\n')
    script(bin_ + "/dig", 'echo ";; Query time: 42 msec"\n')
    script(bin_ + "/curl", 'echo \'{"current":{"temperature_2m":14.6,"weather_code":61,"wind_speed_10m":18},"daily":{"temperature_2m_max":[17],"temperature_2m_min":[9]}}\'\n')
    script(bin_ + "/jsonfilter", 'J="$2"; E="$4"\ncase "$E" in\n'
           "'@.current.temperature_2m') echo 14.6;;\n'@.current.weather_code') echo 61;;\n'@.current.wind_speed_10m') echo 18;;\n"
           "'@.daily.temperature_2m_max[0]') echo 17;;\n'@.daily.temperature_2m_min[0]') echo 9;;\nesac\n")
    os.makedirs(root + "/widgets.d")
    with open(root + "/widgets.d/solar.sh", "w") as f:
        f.write("# interval: 10\necho 'title: Solar'\necho 'big: 3.2 kW'\n")
    with open(root + "/leases", "w") as f:
        f.write("1789907279 aa:ef:d7:9b:62:9c 192.168.20.227 iPhone 01:aa\n1789907064 20:c1:9b:09:9a:b1 192.168.20.210 * 01:20\n")
    with open(root + "/config", "w") as f:
        f.write('PAGES="%s"\nPING_INTERVAL=1\n%s\n' % (pages, extra_conf))
    env = dict(os.environ, PATH=bin_ + ":" + os.environ["PATH"], BE3600_CONF=root + "/config", BE3600_DATA=data,
               BE3600_WIDGETS_D=root + "/widgets.d", BE3600_LEASES=root + "/leases", BE3600_ONCE="1")
    return root, data, env


CT_A = ("ipv4     2 tcp      6 431999 ESTABLISHED src=192.168.20.227 dst=93.184.216.34 sport=50546 dport=443 "
        "packets=15 bytes=1000 src=93.184.216.34 dst=192.168.0.15 sport=443 dport=50546 packets=20 bytes=50000 mark=0 use=1\n"
        "ipv4     2 udp      17 29 src=192.168.20.210 dst=8.8.8.8 sport=5353 dport=53 packets=2 bytes=200 "
        "src=8.8.8.8 dst=192.168.0.15 sport=53 dport=5353 packets=2 bytes=400 mark=0 use=1\n"
        "ipv4     2 tcp      6 431999 ESTABLISHED src=192.168.20.210 dst=192.168.20.5 sport=22 dport=41000 "
        "packets=5 bytes=900000 src=192.168.20.5 dst=192.168.20.210 sport=41000 dport=22 packets=5 bytes=900000 mark=0 use=1\n")
# a second later: the first device pulled 5 MB more, the second a little, the LAN-only flow moved a lot (ignored)
CT_B = CT_A.replace("packets=20 bytes=50000", "packets=999 bytes=5050000").replace("packets=2 bytes=400", "packets=9 bytes=9400")


def run_widgetd(env, **more):
    e = dict(env, **more)
    return subprocess.run(["sh", WIDGETD], env=e, capture_output=True, text=True, timeout=30)


def read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def main():
    print("== ping and internet alerts")
    root, data, env = make_env("internet clock", "PING_TARGET=1.1.1.1\n")
    r = run_widgetd(env)
    ping = read(data + "/ping.txt") or ""
    check("target=1.1.1.1" in ping and "ms=" in ping, "ping.txt names the target and lists the round trips", ping)
    ms = ping.split("ms=")[-1].split()
    check(ms[:1] == ["12.5"] and ms.count("-1") == 3, "lost replies are recorded as -1", str(ms))
    ev = read(data + "/events") or ""
    check("|bad|Internet is down" in ev, "three lost replies in a row raise an 'Internet is down' event", ev)
    check("|good|Internet is back" in ev, "and the next reply raises 'Internet is back'", ev)
    check(read(data + "/wifi.txt") is not None, "(alerts on) the client list is collected for new-device alerts")
    check(read(data + "/vpn.txt") is None and read(data + "/guest.txt") is None, "pages that are not enabled are not collected")

    print("== Wi-Fi clients")
    root, data, env = make_env("clients")
    run_widgetd(env)
    wifi = read(data + "/wifi.txt") or ""
    check("count=2" in wifi, "two connected clients are counted", wifi)
    check("client|iPhone|192.168.20.227|-48|5G" in wifi, "a client gets its name from the DHCP leases, with address, signal and band", wifi)
    check("client|20:c1:9b:09:9a:b1|192.168.20.210|-61|5G" in wifi, "a client with no name shows its MAC address", wifi)

    print("== VPN")
    root, data, env = make_env("vpn health")
    run_widgetd(env)
    vpn = read(data + "/vpn.txt") or ""
    check(vpn.startswith("wg0|up|4"), "a WireGuard tunnel with a recent handshake is up, with its age", vpn)

    print("== guest Wi-Fi and its switch")
    root, data, env = make_env("guest")
    run_widgetd(env)
    g = read(data + "/guest.txt") or ""
    check("state=on" in g and "ssid=Guest-Wifi" in g, "the guest network is reported on, with its name", g)
    r = subprocess.run(["sh", ACTION, "guest", "toggle"], env=env, capture_output=True, text=True)
    g = read(data + "/guest.txt") or ""
    check(r.returncode == 0 and "state=off" in g, "the action switches it off and says so at once", r.stderr + g)
    check("wireless.guest2g.disabled=1" in (read(root + "/uci.state") or ""), "it changes the wireless settings")
    time.sleep(0.6)                      # the reload runs in the background
    check("reload" in (read(root + "/wifi.log") or ""), "and applies them")
    r = subprocess.run(["sh", ACTION, "guest", "toggle"], env=env, capture_output=True, text=True)
    check("state=on" in (read(data + "/guest.txt") or ""), "toggling again switches it back on")
    r = subprocess.run(["sh", ACTION, "guest", "explode"], env=env, capture_output=True, text=True)
    check(r.returncode != 0, "an unknown action is refused")
    r = subprocess.run(["sh", ACTION, "rm", "-rf"], env=env, capture_output=True, text=True)
    check(r.returncode != 0, "an unknown command is refused")

    print("== Wi-Fi QR details")
    root, data, env = make_env("wifiqr")
    run_widgetd(env)
    q = read(data + "/wifiqr.txt") or ""
    check("ssid=Guest-Wifi" in q and "key=Tesla1234" in q and "enc=WPA" in q, "the guest network's name and password are collected", q)
    mode = stat.S_IMODE(os.stat(data + "/wifiqr.txt").st_mode)
    check(mode & 0o077 == 0, "the file with the password can only be read by its owner", oct(mode))
    root, data, env = make_env("wifiqr", 'WIFI_QR_SECTION=wifi5g\n')
    run_widgetd(env)
    check("ssid=Home" in (read(data + "/wifiqr.txt") or ""), "WIFI_QR_SECTION picks another network")

    print("== weather")
    root, data, env = make_env("weather", 'WEATHER_LAT=43.7\nWEATHER_LON=-79.4\nWEATHER_PLACE=Toronto\n')
    run_widgetd(env)
    wx = read(data + "/weather.txt") or ""
    check("temp=14.6" in wx and "code=61" in wx and "hi=17" in wx and "place=Toronto" in wx, "the forecast is collected", wx)
    root, data, env = make_env("weather")
    run_widgetd(env)
    check(read(data + "/weather.txt") is None, "without a location nothing is fetched (nothing leaves the router)")

    print("== your own scripts")
    root, data, env = make_env("custom")
    r = run_widgetd(env, BE3600_ONCE_N="6")
    c = read(data + "/custom-solar.txt") or ""
    check("title: Solar" in c and "big: 3.2 kW" in c, "a script in widgets.d is run and its output kept", c)
    # The script's name once took over the loop's counter: BusyBox read it back as 0 and started
    # over every 5 s (so nothing slower ever ran), dash stopped, bash never finished.
    check(r.returncode == 0, "running your scripts does not upset the loop's count", r.stderr.strip())

    print("== network doctor")
    root, data, env = make_env("doctor", "PING_TARGET=1.1.1.1\n", ping_ok=True)
    run_widgetd(env)
    p = read(data + "/path.txt") or ""
    check("gw=192.168.0.1" in p, "the default gateway is found", p)
    check("gw_ms=12.5" in p and "net_ms=12.5" in p, "each stop is timed on its own", p)
    check("dns_ms=42" in p, "a name lookup is timed", p)

    print("== who is using the internet")
    root, data, env = make_env("talkers")
    ct = root + "/conntrack"
    with open(ct, "w") as f:
        f.write(CT_A)
    e = dict(env, BE3600_CONNTRACK=ct, BE3600_ONCE_N="0")   # one pass per call, so each has real time since the last
    run_widgetd(e)
    check((read(data + "/talkers.txt") or "") == "", "the first round only remembers; it reports nothing yet")
    check(os.path.exists(data + "/.flows"), "the byte counts of every connection are remembered")
    time.sleep(1.1)
    with open(ct, "w") as f:
        f.write(CT_B)
    run_widgetd(e)
    t = (read(data + "/talkers.txt") or "").strip().split("\n")
    check(len(t) == 2 and t[0].startswith("dev|iPhone|"), "the busiest device is listed first, by name", str(t))
    r0 = float(t[0].split("|")[2]) if t and "|" in t[0] else 0
    r1 = float(t[1].split("|")[2]) if len(t) > 1 and "|" in t[1] else 0
    check(r0 > 1e6 and r0 > 20 * r1, "the rates reflect what each one actually moved", "%.0f vs %.0f" % (r0, r1))
    check("192.168.20.5" not in (read(data + "/talkers.txt") or ""), "traffic that never leaves the house is not counted")
    check(not any(x.startswith("dev|192.168.20.210|") and float(x.split("|")[2]) > 1e6 for x in t if "|" in x),
          "a device that only did a little is not inflated", str(t))

    print("\n%d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
