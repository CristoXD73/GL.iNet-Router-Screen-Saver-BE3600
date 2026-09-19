#!/bin/sh
#
# Install the BE3600 animation screensaver.
# Run as root ON THE ROUTER, from the directory this file is in.
#
# Environment:  FORCE=1   install even if the display doesn't look like a BE3600's

set -e

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/router"

die() { echo "error: $*" >&2; exit 1; }

[ "$(id -u)" = "0" ]         || die "run as root"
[ -d "$SRC" ]                || die "can't find $SRC -- run install.sh from the repo directory"
command -v lua >/dev/null    || die "no lua interpreter found"
[ -e /dev/fb0 ]              || die "no /dev/fb0"
[ -e /dev/input/event0 ]     || die "no /dev/input/event0"
[ -x /etc/init.d/gl_screen ] || die "no /etc/init.d/gl_screen -- is this a GL.iNet router with a front display?"

FBNAME="$(cat /sys/class/graphics/fb0/name 2>/dev/null)"
FBSIZE="$(cat /sys/class/graphics/fb0/virtual_size 2>/dev/null)"
FBBPP="$(cat /sys/class/graphics/fb0/bits_per_pixel 2>/dev/null)"

if [ "$FBSIZE" != "76,284" ] || [ "$FBBPP" != "16" ]; then
    echo "warning: display is '$FBNAME' $FBSIZE at ${FBBPP}bpp; this project expects 76,284 at 16bpp (fb_st7789p3)."
    [ "$FORCE" = "1" ] || die "only tested on the GL-BE3600 display. Re-run with FORCE=1 to install anyway."
fi

put() {   # put MODE RELATIVE_PATH
    mkdir -p "$(dirname "/$2")"
    cp "$SRC/$2" "/$2"
    chmod "$1" "/$2"
    echo "  installed /$2"
}

echo "Installing files..."
put 755 usr/bin/be3600-screensaver
put 755 usr/bin/be3600-player.lua
put 755 usr/bin/be3600-wait-touch.lua
put 755 usr/bin/be3600-bea-check.lua
put 755 usr/sbin/be3600-anim
put 755 etc/init.d/be3600-screensaver

# The removal script, so uninstalling later is just:  be3600-uninstall
cp "$HERE/uninstall.sh" /usr/sbin/be3600-uninstall
chmod 755 /usr/sbin/be3600-uninstall
echo "  installed /usr/sbin/be3600-uninstall"

mkdir -p /etc/be3600-screen
if [ -f /etc/be3600-screen/config ]; then
    echo "  kept your existing /etc/be3600-screen/config"
else
    put 644 etc/be3600-screen/config
fi

echo "Adding files to /etc/sysupgrade.conf (kept across a 'keep settings' firmware upgrade)..."
touch /etc/sysupgrade.conf
KEEP="/usr/bin/be3600-screensaver /usr/bin/be3600-player.lua /usr/bin/be3600-wait-touch.lua /usr/bin/be3600-bea-check.lua /usr/sbin/be3600-anim /usr/sbin/be3600-uninstall /etc/init.d/be3600-screensaver /etc/be3600-screen /etc/rc.d/S99be3600-screensaver /etc/rc.d/K10be3600-screensaver"
grep -qxF "# be3600-screensaver" /etc/sysupgrade.conf || echo "# be3600-screensaver" >> /etc/sysupgrade.conf
for P in $KEEP; do
    grep -qxF "$P" /etc/sysupgrade.conf || echo "$P" >> /etc/sysupgrade.conf
done

echo
if [ -f /etc/be3600-screen/active.bea ] && lua /usr/bin/be3600-bea-check.lua /etc/be3600-screen/active.bea >/dev/null; then
    /usr/sbin/be3600-anim on
else
    echo "Installed, but there is no valid animation yet, so it was NOT started."
    echo "Install one and start it with:"
    echo "    be3600-anim set /path/to/animation.bea"
    echo "    be3600-anim on"
fi
