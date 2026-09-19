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

        # --fade: crossfade from what is on the screen into the first frame. The
        # "screen" is an ordinary file pre-filled with a blue picture; the animation
        # is one red frame. (Pixels are RGB565 little-endian: blue 0x001F, red 0xF800.)
        python3 - "$TMP" <<'PY'
import struct, sys
d = sys.argv[1]
FB = 43168
red = bytes([0x00, 0xF8]) * (FB // 2)
blue = bytes([0x1F, 0x00]) * (FB // 2)
open(d + '/red.bea', 'wb').write(b'BEA1' + struct.pack('<HHI', 10, 1, FB) + struct.pack('<H', 1) + red)
open(d + '/blue.bin', 'wb').write(blue)
open(d + '/red.bin', 'wb').write(red)
PY
        cp "$TMP/blue.bin" "$TMP/fb_fade"
        BE3600_FB="$TMP/fb_fade" BE3600_LOOPS=1 "$TMP/player-native" --fade 300 "$TMP/red.bea" >/dev/null 2>&1
        if cmp -s "$TMP/fb_fade" "$TMP/red.bin"; then pass "--fade ends exactly on the new picture"; else fail "--fade did not end on the new picture"; fi

        # Stopped part-way through a longer fade, the screen must be strictly between
        # the two pictures: neither still the old one nor already the new one.
        cp "$TMP/blue.bin" "$TMP/fb_mid"
        BE3600_FB="$TMP/fb_mid" BE3600_LOOPS=1 "$TMP/player-native" --fade 1500 "$TMP/red.bea" >/dev/null 2>&1 &
        FADER=$!
        sleep 0.6
        kill -TERM "$FADER" 2>/dev/null
        wait "$FADER" 2>/dev/null
        MID="$(python3 -c "
import sys
d = open(sys.argv[1], 'rb').read()
print('%02x%02x' % (d[1], d[0]))
" "$TMP/fb_mid")"
        case "$MID" in
            001f|f800) fail "mid-fade, the screen is still one endpoint ($MID), not a blend" ;;
            *) pass "mid-fade the screen is a blend, neither endpoint ($MID)" ;;
        esac
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


echo "== waiting for the finger to lift (--wait-release) =="
# A FIFO stands in for the touch device (it blocks a reader like the real one does).
# Events are 24 bytes: 16 (timestamp, unused) + type + code + value.
# EV_ABS(3) / ABS_MT_TRACKING_ID(57): 0 = a finger is down, -1 = it lifted.
python3 - "$TMP" <<'PY'
import struct, sys
d = sys.argv[1]
open(d + '/press.bin', 'wb').write(b'\0' * 16 + struct.pack('<HHi', 3, 57, 0))
open(d + '/release.bin', 'wb').write(b'\0' * 16 + struct.pack('<HHi', 3, 57, -1))
PY
mkfifo "$TMP/fakedev"

# Finger goes down and STAYS down: --wait-release must keep waiting, not return.
( cat "$TMP/press.bin"; sleep 3 ) > "$TMP/fakedev" &
WRITER=$!
TOUCH_DEVICE="$TMP/fakedev" timeout 1 "$LUA" router/usr/bin/be3600-wait-touch.lua --wait-release >/dev/null 2>&1
RC=$?
kill "$WRITER" 2>/dev/null; wait "$WRITER" 2>/dev/null
if [ "$RC" -eq 124 ]; then pass "a finger that is still down is not mistaken for a lift"; else fail "--wait-release returned $RC while the finger was down"; fi

# Finger goes down, then lifts: it must return (exit 0) once it lifts.
( cat "$TMP/press.bin"; sleep 0.3; cat "$TMP/release.bin" ) > "$TMP/fakedev" &
WRITER=$!
TOUCH_DEVICE="$TMP/fakedev" timeout 3 "$LUA" router/usr/bin/be3600-wait-touch.lua --wait-release >/dev/null 2>&1
RC=$?
wait "$WRITER" 2>/dev/null
if [ "$RC" -eq 0 ]; then pass "returns once the finger lifts"; else fail "--wait-release returned $RC after a lift"; fi

rm -f "$TMP/fakedev"


echo "== switching between saved animations (a single tap) =="
sed -n '/^next_library_animation() {/,/^}/p' router/usr/bin/be3600-screensaver > "$TMP/rotate.sh"
# shellcheck source=/dev/null
. "$TMP/rotate.sh"

# Named a, b, c because the function walks the library in alphabetical (glob) order.
mkdir -p "$TMP/lib"
echo first  > "$TMP/lib/a.bea"
echo second > "$TMP/lib/b.bea"
echo third  > "$TMP/lib/c.bea"

# Exported so they are visibly "used" (the function reads them, but it is loaded from
# a separate file at run time, which a linter cannot follow).
export LIB="$TMP/lib"

export ANIMATION="$TMP/lib/a.bea"
OUT="$(next_library_animation)"; case "$OUT" in "$TMP/lib/b.bea") pass "a -> b" ;; *) fail "a -> $OUT" ;; esac

export ANIMATION="$TMP/lib/b.bea"
OUT="$(next_library_animation)"; case "$OUT" in "$TMP/lib/c.bea") pass "b -> c" ;; *) fail "b -> $OUT" ;; esac

export ANIMATION="$TMP/lib/c.bea"
OUT="$(next_library_animation)"; case "$OUT" in "$TMP/lib/a.bea") pass "c wraps back to a" ;; *) fail "c -> $OUT" ;; esac

export ANIMATION="$TMP/not-in-the-library.bea"
OUT="$(next_library_animation)"; case "$OUT" in "$TMP/lib/a.bea") pass "an unrecognized active file defaults to the first" ;; *) fail "unrecognized -> $OUT" ;; esac

rm -f "$TMP/lib/b.bea" "$TMP/lib/c.bea"
export ANIMATION="$TMP/lib/a.bea"
OUT="$(next_library_animation)"; if [ -z "$OUT" ]; then pass "with only one saved animation, there is nothing to switch to"; else fail "one animation gave: $OUT"; fi

rm -f "$TMP/lib/a.bea"
OUT="$(next_library_animation)"; if [ -z "$OUT" ]; then pass "with an empty library, there is nothing to switch to"; else fail "empty library gave: $OUT"; fi


echo "== a second tap within the window is a double-tap; one tap alone is not =="
# Exercises the real timing path (second_tap_follows, using the real "timeout" and
# the real touch helper) against a FIFO standing in for /dev/input/eventN, since a
# FIFO blocks a reader exactly like the real device does until something is sent
# or the timeout fires.
sed -n '/^second_tap_follows() {/,/^}/p' router/usr/bin/be3600-screensaver > "$TMP/tap.sh"
# shellcheck source=/dev/null
. "$TMP/tap.sh"

export TOUCH="router/usr/bin/be3600-wait-touch.lua"
export DOUBLE_TAP_WINDOW_SECONDS=1
mkfifo "$TMP/fakeinput"
export TOUCH_DEVICE="$TMP/fakeinput"

# second_tap_follows() (like the rest of this script) runs on the router, where the
# interpreter is always /usr/bin/lua. Provide that path here too, if it is not
# already there, so this test exercises the function's real, unmodified content
# rather than a stand-in. Skips cleanly if it is missing and cannot be created
# (no root) rather than failing the whole suite.
CREATED_SYSTEM_LUA=0
if [ ! -e /usr/bin/lua ]; then
    LUA_ABS="$(command -v "$LUA" 2>/dev/null)"
    if [ -n "$LUA_ABS" ] && ln -s "$LUA_ABS" /usr/bin/lua 2>/dev/null; then
        CREATED_SYSTEM_LUA=1
    fi
fi

if [ -e /usr/bin/lua ]; then

    # A touch-down event: 16 zero bytes (timestamp, unused here), EV_ABS(3),
    # ABS_MT_TRACKING_ID(57), value 0.
    python3 -c "
import struct, sys
sys.stdout.buffer.write(b'\\0' * 16 + struct.pack('<HHi', 3, 57, 0))
" > "$TMP/touch_event.bin"

    # Case 1: a second tap arrives quickly -> counts as a double-tap.
    ( sleep 0.2; cat "$TMP/touch_event.bin" > "$TMP/fakeinput" ) &
    WRITER=$!
    if second_tap_follows; then pass "a prompt second tap is recognized (double-tap)"; else fail "a prompt second tap was missed"; fi
    wait "$WRITER" 2>/dev/null

    # Case 2: nothing follows -> times out, not a double-tap. (Runs for the real
    # 1-second window; unavoidable since this is what is being tested.)
    if second_tap_follows; then fail "silence was mistaken for a second tap"; else pass "silence within the window is correctly not a double-tap"; fi

    # Case 3: a fractional window (what config.default actually uses) is honored,
    # not just silently rounded away.
    export DOUBLE_TAP_WINDOW_SECONDS=0.3
    ( sleep 0.1; cat "$TMP/touch_event.bin" > "$TMP/fakeinput" ) &
    WRITER=$!
    if second_tap_follows; then pass "a fractional window (0.3s) works"; else fail "a fractional window was not honored"; fi
    wait "$WRITER" 2>/dev/null

    # And a malformed value falls back to a safe default instead of erroring out.
    export DOUBLE_TAP_WINDOW_SECONDS="nonsense"
    ( sleep 0.1; cat "$TMP/touch_event.bin" > "$TMP/fakeinput" ) &
    WRITER=$!
    if second_tap_follows; then pass "a malformed window value still works (falls back to a default)"; else fail "a malformed window value broke detection"; fi
    wait "$WRITER" 2>/dev/null
else
    echo "  skip  /usr/bin/lua is not available here and could not be created (no root)"
fi

[ "$CREATED_SYSTEM_LUA" = 1 ] && rm -f /usr/bin/lua
rm -f "$TMP/fakeinput"
unset TOUCH_DEVICE


echo "== Studio Link (the helper behind Motion Studio's drop zone) =="
if python3 tests/studio_link_test.py >"$TMP/studio-link.log" 2>&1; then
    pass "studio_link.py: $(grep -c '^  ok' "$TMP/studio-link.log") checks pass"
else
    fail "Studio Link tests failed: $(grep FAIL "$TMP/studio-link.log")"
fi

# The single-file downloads on the Studio page are generated; they must match the sources.
if python3 tools/build_downloads.py --check >"$TMP/downloads.log" 2>&1; then
    pass "studio/downloads are up to date with their sources"
else
    fail "studio/downloads are stale (run: python3 tools/build_downloads.py): $(cat "$TMP/downloads.log")"
fi
# The standalone Mac/Linux file must work on its own: no bea2.py beside it.
mkdir -p "$TMP/solo" && cp studio/downloads/studio-link.py "$TMP/solo/"
if (cd "$TMP/solo" && python3 -c "
import importlib.util
s = importlib.util.spec_from_file_location('solo', 'studio-link.py'); m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
assert m.parse_library('*\tdefault\t100\t25.0\nlimits\t3\t25\n')
import tarfile
path, pid = m.get_payload()
names = tarfile.open(path, 'r:gz').getnames()
assert pid != 'dev' and 'setup/router-install.sh' in names and 'router/usr/bin/be3600-player' in names and 'animations/default.bea.gz' in names") >/dev/null 2>&1; then
    pass "studio-link.py download imports, and carries the router's files inside itself"
else
    fail "studio-link.py download does not work on its own"
fi


echo "== Motion Studio's GIF decoder, checked against Pillow =="
if command -v node >/dev/null 2>&1 && python3 -c 'import PIL' 2>/dev/null; then
    if python3 tests/make_gif_fixtures.py "$TMP/gif" >/dev/null 2>&1 && node tests/gif_decoder.test.js "$TMP/gif" >"$TMP/gif.log" 2>&1; then
        pass "GIF decoder: $(grep -c '^  ok' "$TMP/gif.log") checks pass"
    else
        fail "GIF decoder tests failed: $(grep FAIL "$TMP/gif.log")"
    fi
else
    echo "  skip  needs node and Pillow (pip install pillow)"
fi


echo "== line endings: nothing the router reads may contain a carriage return =="
BADCR="$(grep -rlI "$(printf '\r')" router setup tests tools/*.py tools/make-sample-bea.py install.sh set-animation.sh studio-link.sh docs README.md 2>/dev/null)"
if [ -z "$BADCR" ]; then pass "no CR characters in router/, setup/, scripts or docs"; else fail "CR found in: $BADCR"; fi


echo "== shell syntax =="
for f in install.sh set-animation.sh studio-link.sh setup/router-install.sh setup/router-uninstall.sh \
         router/usr/bin/be3600-screensaver router/usr/sbin/be3600-anim router/etc/init.d/be3600-screensaver; do
    if sh -n "$f" 2>/dev/null; then pass "$f"; else fail "$f does not parse"; fi
done


echo
if [ "$FAILS" -eq 0 ]; then echo "All tests passed."; else echo "$FAILS test(s) FAILED."; fi
exit "$FAILS"
