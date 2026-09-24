#!/bin/sh
#
# Completely remove the BE3600 animation screensaver and restore the stock
# screen. Installed on the router as:  be3600-uninstall
#
#   be3600-uninstall                  remove it, keep /etc/be3600-screen (your .bea + config)
#   be3600-uninstall --purge          also delete /etc/be3600-screen
#   be3600-uninstall --purge --forget-keys
#                                     and the logins computers saved with "Save this login"
#                                     (what the Uninstall download runs: the router as it was)

die() { echo "  ERROR: $*" >&2; exit 1; }

[ "$(id -u)" = "0" ] || die "must run as root"

PURGE=0; FORGET_KEYS=0
for A in "$@"; do
    case "$A" in
        --purge) PURGE=1 ;;
        --forget-keys) FORGET_KEYS=1 ;;
        *) die "unknown option $A (use --purge and/or --forget-keys)" ;;
    esac
done

# A fan chime or "be3600-fan spin" may be holding the fan; give it back to the router first.
if [ -f /tmp/be3600-fan.hold ] && [ -x /usr/sbin/be3600-fan ]; then
    /usr/sbin/be3600-fan spin auto >/dev/null 2>&1
fi

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
for PID in $(ps w | grep -E '[b]e3600-player|[b]e3600-wait-touch.lua|[b]e3600-widgetd' | awk '{print $1}'); do
    kill "$PID" 2>/dev/null
done
sleep 1
rm -rf /tmp/be3600-screen.lock /tmp/be3600-widgets
rm -f /tmp/be3600-screen.pid /tmp/be3600-player.log /tmp/be3600-preview.log \
      /tmp/be3600-screen.hello-done /tmp/be3600-screen.show-now /tmp/be3600-fan.hold /tmp/be3600-fan.lock

echo "Removing files..."
rm -f /usr/bin/be3600-screensaver \
      /usr/bin/be3600-player \
      /usr/bin/be3600-player.lua \
      /usr/bin/be3600-wait-touch.lua \
      /usr/bin/be3600-bea-check.lua \
      /usr/bin/be3600-widgetd \
      /usr/sbin/be3600-anim \
      /usr/sbin/be3600-widget-action \
      /usr/sbin/be3600-fan \
      /usr/sbin/be3600-uninstall \
      /etc/init.d/be3600-screensaver \
      /etc/rc.d/S81be3600-screensaver \
      /etc/rc.d/K10be3600-screensaver

# Older versions started at a different point in the boot; leave none of them behind.
rm -f /etc/rc.d/S[0-9][0-9]be3600-screensaver
rm -rf /usr/lib/be3600

echo "Removing entries from /etc/sysupgrade.conf..."
if [ -f /etc/sysupgrade.conf ]; then
    grep -vxF -e "# be3600-screensaver" \
              -e "/usr/bin/be3600-screensaver" \
              -e "/usr/bin/be3600-player" \
              -e "/usr/bin/be3600-player.lua" \
              -e "/usr/bin/be3600-wait-touch.lua" \
              -e "/usr/bin/be3600-bea-check.lua" \
              -e "/usr/bin/be3600-widgetd" \
              -e "/usr/sbin/be3600-anim" \
              -e "/usr/sbin/be3600-widget-action" \
              -e "/usr/sbin/be3600-fan" \
              -e "/usr/lib/be3600" \
              -e "/usr/sbin/be3600-uninstall" \
              -e "/etc/init.d/be3600-screensaver" \
              -e "/etc/be3600-screen" \
              -e "/etc/rc.d/S81be3600-screensaver" \
              -e "/etc/rc.d/S99be3600-screensaver" \
              -e "/etc/rc.d/K10be3600-screensaver" \
              /etc/sysupgrade.conf > /tmp/sysupgrade.conf.new
    cat /tmp/sysupgrade.conf.new > /etc/sysupgrade.conf
    rm -f /tmp/sysupgrade.conf.new
fi

if [ "$PURGE" = 1 ]; then
    rm -rf /etc/be3600-screen
    echo "Deleted /etc/be3600-screen."
else
    echo "Kept /etc/be3600-screen (your animation and config). Use --purge to delete it."
fi

# The keys Studio Link and the installer add for "Save this login" end in a be3600-studio-link-...
# label; only those lines go, any other key on the router stays.
if [ "$FORGET_KEYS" = 1 ] && [ -f /etc/dropbear/authorized_keys ]; then
    grep -v ' be3600-studio-link-[A-Za-z0-9_-]*$' /etc/dropbear/authorized_keys > /tmp/be3600-keys.new
    cat /tmp/be3600-keys.new > /etc/dropbear/authorized_keys
    rm -f /tmp/be3600-keys.new
    echo "Removed the logins saved by computers."
fi

# Make sure the stock screen is up.
if ! ps w | grep -q '[g]l_screen'; then
    /etc/init.d/gl_screen start >/dev/null 2>&1
fi

echo "Done. The stock GL.iNet screen is back."
