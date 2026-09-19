#!/bin/sh
#
# Project tests. Needs: sh, python3 and a Lua 5.1 interpreter (the router runs 5.1).
# Run from anywhere:   sh tests/run.sh          (LUA=lua5.1 sh tests/run.sh on some systems)
#
# Everything runs on an ordinary computer; no router is needed.

set -u
cd "$(dirname "$0")/.." || exit 1

LUA="${LUA:-lua}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAILS=0

pass() { printf '  ok    %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }
CHECK="router/usr/bin/be3600-bea-check.lua"


echo "== sample animations are accepted by the checker =="
python3 tools/make-sample-bea.py colors "$TMP/colors.bea" >/dev/null
python3 tools/make-sample-bea.py scroll "$TMP/scroll.bea" >/dev/null
for f in colors scroll; do
    if "$LUA" "$CHECK" "$TMP/$f.bea" >/dev/null 2>&1; then pass "$f.bea accepted"; else fail "$f.bea rejected"; fi
done


echo "== corrupted animations are rejected =="
python3 - "$TMP" <<'PY'
import struct, sys
d = sys.argv[1]
good = open(d + '/colors.bea', 'rb').read()
def w(name, data):
    open(d + '/bad_' + name + '.bea', 'wb').write(data)
w('magic', b'XXXX' + good[4:])
w('fps_zero', good[:4] + struct.pack('<H', 0) + good[6:])
w('fps_25', good[:4] + struct.pack('<H', 25) + good[6:])
w('frame_bytes', good[:8] + struct.pack('<I', 43169) + good[12:])
w('records_plus_one', good[:6] + struct.pack('<H', 5) + good[8:])
w('records_zero', good[:6] + struct.pack('<H', 0) + good[8:12])
w('truncated', good[:-1])
w('trailing_byte', good + b'\x00')
w('header_only', good[:12])
w('tiny', good[:5])
w('empty', b'')
PY
for f in "$TMP"/bad_*.bea; do
    n="$(basename "$f" .bea)"
    if "$LUA" "$CHECK" "$f" >/dev/null 2>&1; then fail "$n was accepted"; else pass "$n rejected"; fi
done
if "$LUA" "$CHECK" "$TMP/does-not-exist.bea" >/dev/null 2>&1; then fail "missing file was accepted"; else pass "missing file rejected"; fi


echo "== the checker reports the right loop length =="
OUT="$("$LUA" "$CHECK" "$TMP/colors.bea" 2>&1)"
case "$OUT" in
    *"4 frames, 8 ticks at 2 fps = 4.0 s per loop"*) pass "colors.bea is 4.0 s" ;;
    *) fail "unexpected checker output: $OUT" ;;
esac


echo "== the Lua player writes real frames to the framebuffer =="
: > "$TMP/fb"
BE3600_FB="$TMP/fb" BE3600_LOOPS=1 "$LUA" router/usr/bin/be3600-player.lua "$TMP/colors.bea" >/dev/null 2>&1
if python3 - "$TMP/fb" <<'PY'
import sys
data = open(sys.argv[1], 'rb').read()
white = b'\xff\xff' * (76 * 284)          # the last frame of colors.bea
sys.exit(0 if data == white else 1)
PY
then pass "one pass ends on the last frame (white, 43168 bytes)"; else fail "framebuffer content is wrong"; fi

if BE3600_FB="$TMP/fb" BE3600_LOOPS=1 "$LUA" router/usr/bin/be3600-player.lua "$TMP/bad_magic.bea" >/dev/null 2>&1; then
    fail "the player accepted a corrupt animation"
else
    pass "the player refuses a corrupt animation"
fi


echo "== touchscreen autodetection =="
mkdir -p "$TMP/input/event0/device" "$TMP/input/event1/device" "$TMP/input/event3/device"
printf 'gpio-keys\n'                      > "$TMP/input/event0/device/name"
printf 'Some Vendor Keyboard\n'           > "$TMP/input/event1/device/name"
printf 'Hynitron CST816X Touchscreen\n'   > "$TMP/input/event3/device/name"
WHICH="$(BE3600_SYS_INPUT="$TMP/input" "$LUA" router/usr/bin/be3600-wait-touch.lua --which)"
case "$WHICH" in "/dev/input/event3 "*) pass "finds the touchscreen on event3 ($WHICH)" ;; *) fail "autodetect gave: $WHICH" ;; esac
WHICH="$(TOUCH_DEVICE=/dev/input/event9 BE3600_SYS_INPUT="$TMP/input" "$LUA" router/usr/bin/be3600-wait-touch.lua --which)"
case "$WHICH" in "/dev/input/event9 "*) pass "TOUCH_DEVICE overrides autodetection" ;; *) fail "override gave: $WHICH" ;; esac
WHICH="$(BE3600_SYS_INPUT="$TMP/nowhere" "$LUA" router/usr/bin/be3600-wait-touch.lua --which)"
case "$WHICH" in "/dev/input/event0 "*) pass "falls back to event0 when nothing matches" ;; *) fail "fallback gave: $WHICH" ;; esac


echo "== line endings: nothing the router reads may contain a carriage return =="
BADCR="$(grep -rlI "$(printf '\r')" router setup tests tools/*.py tools/make-sample-bea.py install.sh set-animation.sh docs README.md 2>/dev/null)"
if [ -z "$BADCR" ]; then pass "no CR characters in router/, setup/, scripts or docs"; else fail "CR found in: $BADCR"; fi


echo "== shell syntax =="
for f in install.sh set-animation.sh setup/router-install.sh setup/router-uninstall.sh \
         router/usr/bin/be3600-screensaver router/usr/sbin/be3600-anim router/etc/init.d/be3600-screensaver; do
    if sh -n "$f" 2>/dev/null; then pass "$f"; else fail "$f does not parse"; fi
done


echo
if [ "$FAILS" -eq 0 ]; then echo "All tests passed."; else echo "$FAILS test(s) FAILED."; fi
exit "$FAILS"
