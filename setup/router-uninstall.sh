#!/bin/sh
#
# Completely remove the BE3600 animation screensaver and restore the stock
# screen. Installed on the router as:  be3600-uninstall
#
#   be3600-uninstall            remove it, keep /etc/be3600-screen (your .bea + config)
#   be3600-uninstall --purge    also delete /etc/be3600-screen

die() { echo "  ERROR: $*" >&2; exit 1; }

[ "$(id -u)" = "0" ] || die "must run as root"

echo "Stopping and disabling the service..."
/etc/init.d/be3600-screensaver stop    >/dev/null 2>&1
/etc/init.d/be3600-screensaver disable >/dev/null 2>&1

# Belt and braces: make sure nothing is still drawing to the display.
# Only kill the PID in the lock if it really is a be3600-screensaver (a stale
# lock could hold a PID that has since been recycled by another process).
LOCKPID="$(cat /tmp/be3600-screen.lock/pid 2>/dev/null)"
if [ -n "$LOCKPID" ] && tr '\0' ' ' < "/proc/$LOCKPID/cmdline" 2>/dev/null | grep -q 'be3600-screensaver'; then
    kill "$LOCKPID" 2>/dev/null
fi
for PID in $(ps w | grep -E '[b]e3600-player|[b]e3600-wait-touch.lua' | awk '{print $1}'); do
    kill "$PID" 2>/dev/null
done
sleep 1
rm -rf /tmp/be3600-screen.lock
rm -f /tmp/be3600-screen.pid /tmp/be3600-player.log

echo "Removing files..."
rm -f /usr/bin/be3600-screensaver \
      /usr/bin/be3600-player.lua \
      /usr/bin/be3600-wait-touch.lua \
      /usr/bin/be3600-bea-check.lua \
      /usr/sbin/be3600-anim \
      /usr/sbin/be3600-uninstall \
      /etc/init.d/be3600-screensaver \
      /etc/rc.d/S99be3600-screensaver \
      /etc/rc.d/K10be3600-screensaver

echo "Removing entries from /etc/sysupgrade.conf..."
if [ -f /etc/sysupgrade.conf ]; then
    grep -vxF -e "# be3600-screensaver" \
              -e "/usr/bin/be3600-screensaver" \
              -e "/usr/bin/be3600-player.lua" \
              -e "/usr/bin/be3600-wait-touch.lua" \
              -e "/usr/bin/be3600-bea-check.lua" \
              -e "/usr/sbin/be3600-anim" \
              -e "/usr/sbin/be3600-uninstall" \
              -e "/etc/init.d/be3600-screensaver" \
              -e "/etc/be3600-screen" \
              -e "/etc/rc.d/S99be3600-screensaver" \
              -e "/etc/rc.d/K10be3600-screensaver" \
              /etc/sysupgrade.conf > /tmp/sysupgrade.conf.new
    cat /tmp/sysupgrade.conf.new > /etc/sysupgrade.conf
    rm -f /tmp/sysupgrade.conf.new
fi

if [ "$1" = "--purge" ]; then
    rm -rf /etc/be3600-screen
    echo "Deleted /etc/be3600-screen."
else
    echo "Kept /etc/be3600-screen (your animation and config). Use --purge to delete it."
fi

# Make sure the stock screen is up.
if ! ps w | grep -q '[g]l_screen'; then
    /etc/init.d/gl_screen start >/dev/null 2>&1
fi

echo "Done. The stock GL.iNet screen is back."
