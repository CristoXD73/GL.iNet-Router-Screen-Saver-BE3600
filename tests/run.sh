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


echo "== BEA2 (changes-only format) =="
python3 - "$TMP" <<'PY'
import struct, sys
sys.path.insert(0, 'tools')
import bea2
d = sys.argv[1]
FB = bea2.FRAME_BYTES
# 6 frames: a mostly-static picture with a small square that moves. (Identical neighbouring
# frames would be merged into one longer hold by the encoder, so they are not used here.)
def frame(x):
    buf = bytearray(b'\x11\x22' * (FB // 2))
    for row in range(20):
        base = (100 + row) * 152 + x * 2
        buf[base:base + 20] = b'\xff\xff' * 10
    return bytes(buf)
frames = [(1, frame(i * 4)) for i in range(5)] + [(3, frame(24))]
with open(d + '/motion.bea', 'wb') as f:
    f.write(b'BEA1' + struct.pack('<HHI', 8, len(frames), FB))
    for run, data in frames:
        f.write(struct.pack('<H', run) + data)
PY
python3 tools/bea2.py encode "$TMP/motion.bea" "$TMP/motion2.bea" >/dev/null
python3 tools/bea2.py decode "$TMP/motion2.bea" "$TMP/motion_back.bea" >/dev/null
if cmp -s "$TMP/motion.bea" "$TMP/motion_back.bea"; then pass "encode then decode gives the original bytes"; else fail "BEA2 round trip changed the animation"; fi
A="$(wc -c < "$TMP/motion.bea")"; B="$(wc -c < "$TMP/motion2.bea")"
if [ "$B" -lt $((A / 4)) ]; then pass "BEA2 is much smaller ($A -> $B bytes)"; else fail "BEA2 did not shrink ($A -> $B)"; fi
if "$LUA" "$CHECK" "$TMP/motion2.bea" >/dev/null 2>&1; then pass "checker accepts the BEA2 file"; else fail "checker rejected a valid BEA2 file"; fi

python3 - "$TMP" <<'PY'
import struct, sys
d = sys.argv[1]
good = open(d + '/motion2.bea', 'rb').read()
FB = 43168
def w(name, data): open(d + '/bad2_' + name + '.bea', 'wb').write(data)
w('truncated', good[:-1])
w('trailing', good + b'\x00')
# first record is a full frame (7-byte record header + FB); make it a hold instead
w('first_not_full', good[:12] + good[12:14] + b'\x02' + good[15:])
# corrupt the second record (the first delta): put a span past the end of the frame
second = 12 + 7 + FB
run, kind, plen = struct.unpack_from('<HBI', good, second)
assert kind == 1
bad = bytearray(good)
struct.pack_into('<I', bad, second + 7 + 2, FB - 1)          # span offset FB-1, length > 1
struct.pack_into('<H', bad, second + 7 + 2 + 4, 50)
w('span_outside', bytes(bad))
bad = bytearray(good)
bad[second + 2] = 9                                            # unknown record kind
w('unknown_kind', bytes(bad))
bad = bytearray(good)
struct.pack_into('<I', bad, second + 3, plen + 5)              # payload length lies
w('length_lies', bytes(bad))
PY
for f in "$TMP"/bad2_*.bea; do
    n="$(basename "$f" .bea)"
    if "$LUA" "$CHECK" "$f" >/dev/null 2>&1; then fail "$n was accepted"; else pass "$n rejected"; fi
done

# The macOS/Linux drag-and-drop tool checks files itself before sending them.
sed -n '/^check_bea() {/,/^}/p' set-animation.sh > "$TMP/check_bea.sh"
# shellcheck disable=SC2034  # read by the check_bea function sourced below
FRAME_BYTES=43168
# shellcheck source=/dev/null
. "$TMP/check_bea.sh"
if check_bea "$TMP/motion2.bea" >/dev/null 2>&1; then pass "set-animation.sh accepts BEA2"; else fail "set-animation.sh rejected a valid BEA2 file"; fi
if check_bea "$TMP/bad2_truncated.bea" >/dev/null 2>&1; then fail "set-animation.sh accepted a truncated BEA2 file"; else pass "set-animation.sh rejects a truncated BEA2 file"; fi
if check_bea "$TMP/bad2_trailing.bea" >/dev/null 2>&1; then fail "set-animation.sh accepted a BEA2 file with trailing bytes"; else pass "set-animation.sh rejects trailing bytes"; fi

: > "$TMP/fb1"; : > "$TMP/fb2"
BE3600_FB="$TMP/fb1" BE3600_LOOPS=1 "$LUA" router/usr/bin/be3600-player.lua "$TMP/motion.bea"  >/dev/null 2>&1
BE3600_FB="$TMP/fb2" BE3600_LOOPS=1 "$LUA" router/usr/bin/be3600-player.lua "$TMP/motion2.bea" >/dev/null 2>&1
if cmp -s "$TMP/fb1" "$TMP/fb2" && [ -s "$TMP/fb1" ]; then pass "player shows the same final picture from BEA1 and BEA2"; else fail "BEA2 playback differs from BEA1"; fi


echo "== native player (same source, built for this computer) =="
if command -v cc >/dev/null 2>&1; then
    if cc -O2 -Wall -Wextra -Werror -o "$TMP/player-native" native/be3600-player.c 2>"$TMP/cc.log"; then
        pass "compiles with no warnings"
        : > "$TMP/fb3"; : > "$TMP/fb4"
        BE3600_FB="$TMP/fb3" BE3600_LOOPS=1 "$TMP/player-native" "$TMP/motion.bea"  >/dev/null 2>&1
        BE3600_FB="$TMP/fb4" BE3600_LOOPS=1 "$TMP/player-native" "$TMP/motion2.bea" >/dev/null 2>&1
        if cmp -s "$TMP/fb1" "$TMP/fb3"; then pass "BEA1: same picture as the Lua player"; else fail "native BEA1 output differs from Lua"; fi
        if cmp -s "$TMP/fb2" "$TMP/fb4"; then pass "BEA2: same picture as the Lua player"; else fail "native BEA2 output differs from Lua"; fi
        for f in "$TMP"/bad_*.bea "$TMP"/bad2_*.bea; do
            n="$(basename "$f" .bea)"
            if BE3600_FB="$TMP/fb_bad" BE3600_LOOPS=1 "$TMP/player-native" "$f" >/dev/null 2>&1; then fail "native player accepted $n"; else pass "native player rejects $n"; fi
        done
        if "$TMP/player-native" --version >/dev/null 2>&1; then pass "--version works"; else fail "--version failed"; fi
    else
        fail "native player does not compile cleanly: $(cat "$TMP/cc.log")"
    fi
else
    echo "  skip  no C compiler found"
fi


echo "== the bundled animation =="
gunzip -c animations/default.bea.gz > "$TMP/default.bea"
if "$LUA" "$CHECK" "$TMP/default.bea" >/dev/null 2>&1; then pass "default.bea.gz unpacks to a valid animation"; else fail "the bundled animation is invalid"; fi
python3 tools/bea2.py decode "$TMP/default.bea" "$TMP/default_full.bea" >/dev/null
SUM="$(sha256sum "$TMP/default_full.bea" | cut -d' ' -f1)"
if [ "$SUM" = "17c7943efae58bfb87c2d03605a1802e674768c8547dd1f428178bd3151524e3" ]; then pass "it decodes to the original full-frame animation (SHA-256 matches)"; else fail "bundled animation no longer matches the original ($SUM)"; fi


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
