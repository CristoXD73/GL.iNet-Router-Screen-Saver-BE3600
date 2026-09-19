#!/bin/sh
#
# Runs ON THE ROUTER. Installs the screensaver from the folder it was unpacked
# into. You normally never run this by hand: Install.cmd / install.sh on your
# computer copy the project over and call it.
#
# Environment:  FORCE=1   install even if the display doesn't look like a BE3600's

set -e

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
SRC="$ROOT/router"
BEA="/etc/be3600-screen/active.bea"
DEFAULT_GZ="$ROOT/animations/default.bea.gz"

die() { echo "  ERROR: $*" >&2; exit 1; }

# Exit code 3 = "this is not a router with the BE3600's front display". The
# desktop installer uses it to ask for a different address instead of failing.
notbe() { echo "  ERROR: $*" >&2; exit 3; }

echo
echo "[1/4] Checking this router"

[ "$(id -u)" = "0" ]         || die "must run as root"
[ -d "$SRC" ]                || die "can't find $SRC"
command -v lua >/dev/null    || die "no lua interpreter found (try: opkg install lua)"
[ -e /dev/fb0 ]              || notbe "no /dev/fb0 -- this device has no front display"
[ -e /dev/input/event0 ]     || notbe "no /dev/input/event0 -- no touchscreen found"
[ -x /etc/init.d/gl_screen ] || notbe "no /etc/init.d/gl_screen -- not a GL.iNet router with a front display"

MODEL="$(cat /tmp/sysinfo/model 2>/dev/null)"
FBNAME="$(cat /sys/class/graphics/fb0/name 2>/dev/null)"
FBSIZE="$(cat /sys/class/graphics/fb0/virtual_size 2>/dev/null)"
FBBPP="$(cat /sys/class/graphics/fb0/bits_per_pixel 2>/dev/null)"
echo "  router:  ${MODEL:-unknown model}"
echo "  display: $FBNAME, $FBSIZE, ${FBBPP}bpp"

if [ "$FBSIZE" != "76,284" ] || [ "$FBBPP" != "16" ]; then
    echo "  warning: expected a 76x284, 16bpp display (fb_st7789p3) like the GL-BE3600's."
    [ "$FORCE" = "1" ] || notbe "only tested on the GL-BE3600 display. Re-run with FORCE=1 to install anyway."
fi
echo "  ok"

echo
echo "[2/4] Installing the screensaver"

# An older version may be running (upgrade). Stop it BEFORE replacing its files:
# a shell reads its script incrementally, so rewriting the file under a running
# supervisor can send it off into the middle of the new file.
if [ -x /etc/init.d/be3600-screensaver ]; then
    /etc/init.d/be3600-screensaver stop >/dev/null 2>&1 || true
    sleep 1
fi

put() {   # put MODE RELATIVE_PATH   (atomic: copy beside it, then rename into place)
    mkdir -p "$(dirname "/$2")"
    cp "$SRC/$2" "/$2.new"
    chmod "$1" "/$2.new"
    mv "/$2.new" "/$2"
}

put 755 usr/bin/be3600-screensaver
put 755 usr/bin/be3600-player.lua
put 755 usr/bin/be3600-wait-touch.lua
put 755 usr/bin/be3600-bea-check.lua
put 755 usr/sbin/be3600-anim
put 755 etc/init.d/be3600-screensaver

# The native player (exact timing, almost no CPU) is a static aarch64 binary. The
# Lua player above is the fallback for anything else, or if the binary won't run.
if [ "$(uname -m)" = "aarch64" ] && [ -f "$SRC/usr/bin/be3600-player" ]; then
    put 755 usr/bin/be3600-player
    if /usr/bin/be3600-player --version >/dev/null 2>&1; then
        echo "  installed the native player"
    else
        rm -f /usr/bin/be3600-player
        echo "  the native player does not run on this router; using the Lua player"
    fi
else
    rm -f /usr/bin/be3600-player
    echo "  using the Lua player (the native one is for aarch64 routers)"
fi

# The removal script, so uninstalling later is just:  be3600-uninstall
cp "$HERE/router-uninstall.sh" /usr/sbin/be3600-uninstall.new
chmod 755 /usr/sbin/be3600-uninstall.new
mv /usr/sbin/be3600-uninstall.new /usr/sbin/be3600-uninstall

mkdir -p /etc/be3600-screen
if [ -f /etc/be3600-screen/config ]; then
    echo "  kept your existing settings (/etc/be3600-screen/config)"
else
    put 644 etc/be3600-screen/config
fi
echo "  installed be3600-anim, be3600-uninstall and the screensaver service"

# Keep everything across a "keep settings" firmware upgrade (standard OpenWrt list).
touch /etc/sysupgrade.conf
KEEP="/usr/bin/be3600-screensaver /usr/bin/be3600-player /usr/bin/be3600-player.lua/usr/bin/be3600-wait-touch.lua /usr/bin/be3600-bea-check.lua /usr/sbin/be3600-anim /usr/sbin/be3600-uninstall /etc/init.d/be3600-screensaver /etc/be3600-screen /etc/rc.d/S99be3600-screensaver /etc/rc.d/K10be3600-screensaver"
grep -qxF "# be3600-screensaver" /etc/sysupgrade.conf || echo "# be3600-screensaver" >> /etc/sysupgrade.conf
for P in $KEEP; do
    grep -qxF "$P" /etc/sysupgrade.conf || echo "$P" >> /etc/sysupgrade.conf
done

echo
echo "[3/4] Setting up the animation"

if [ -f "$BEA" ] && lua /usr/bin/be3600-bea-check.lua "$BEA" >/dev/null 2>&1; then
    echo "  keeping the animation already on this router"
elif [ -f "$DEFAULT_GZ" ]; then
    gunzip -c "$DEFAULT_GZ" > "$BEA.new"
    mv "$BEA.new" "$BEA"
    mkdir -p /etc/be3600-screen/animations
    cp "$BEA" /etc/be3600-screen/animations/default.bea
    echo "  installed the bundled animation (kept in the library as 'default')"
else
    echo "  no animation available (none bundled, none on the router)"
fi
[ -f "$BEA" ] && lua /usr/bin/be3600-bea-check.lua "$BEA" | sed 's/^OK /  /'

echo
echo "[4/4] Starting"

if [ -f "$BEA" ] && lua /usr/bin/be3600-bea-check.lua "$BEA" >/dev/null 2>&1; then
    /usr/sbin/be3600-anim on >/dev/null
    echo "  screensaver is ON: it starts at boot and appears after the idle time."
else
    echo "  installed but NOT started: there is no valid animation yet."
    echo "  Give it one with Set-Animation (drag a .bea onto it) or: be3600-anim set FILE.bea"
fi

echo
echo "Done."
