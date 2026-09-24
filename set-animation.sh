#!/bin/sh
#
# Drag-and-drop tool for macOS and Linux: replaces the animation on your BE3600.
# (Windows: drag a .bea onto Set-Animation.cmd.)
#
#   ./set-animation.sh                       then drag a .bea into this window + Enter
#   ./set-animation.sh my.bea                send this file
#   ./set-animation.sh -i KEY 192.168.8.1 my.bea    key login / explicit router address

set -e
cd "$(dirname "$0")"

KEY=""; ROUTER=""; FILE=""
while [ $# -gt 0 ]; do
    case "$1" in
        -i) KEY="$2"; shift 2 ;;
        -h|--help) sed -n '3,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        [0-9]*.[0-9]*.[0-9]*.[0-9]*)   # looks like an IPv4 address, unless it is a file ("1.2.3.4-eyes.bea")
            if [ -f "$1" ]; then FILE="$1"; else ROUTER="$1"; fi; shift ;;
        *)                            # anything else is the animation file (checked below)
            FILE="$1"; shift ;;
    esac
done

if [ -t 1 ]; then
    CY="$(printf '\033[36m')"; GR="$(printf '\033[32m')"; YE="$(printf '\033[33m')"
    RD="$(printf '\033[31m')"; BD="$(printf '\033[1m')"; RS="$(printf '\033[0m')"
else
    CY=""; GR=""; YE=""; RD=""; BD=""; RS=""
fi

STATE_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/be3600-screensaver/router"
FRAME_BYTES=43168

gateway() {
    if command -v ip >/dev/null 2>&1; then ip route show default 2>/dev/null | awk '/^default/ {print $3; exit}'
    elif command -v route >/dev/null 2>&1; then route -n get default 2>/dev/null | awk '/gateway:/ {print $2; exit}'
    fi
}
# Is something answering on SSH at $1? macOS's nc ignores -w while it is still connecting, so an
# address with nothing behind it (192.168.8.1 on another network) held it for over a minute; -G
# is its own connect timeout. Other nc's have no -G, and -w already covers the connect there.
ssh_open() {
    command -v nc >/dev/null 2>&1 || return 0
    if [ "$(uname -s 2>/dev/null)" = "Darwin" ]; then
        nc -z -G 2 -w 2 "$1" 22 >/dev/null 2>&1
    else
        nc -z -w 2 "$1" 22 >/dev/null 2>&1
    fi
}

# A router address is a plain IP address or name: letters, digits, dots and dashes, and
# never something that could be read as an option (a leading dash).
valid_host() {
    case "$1" in ''|-*|*[!A-Za-z0-9.-]*) return 1 ;; esac
    return 0
}

if [ -n "$ROUTER" ] && ! valid_host "$ROUTER"; then
    printf 'The router address must be like 192.168.8.1 (letters, digits, dots and dashes only).\n' >&2
    exit 1
fi

# A plain IPv4 address that is not on a home or office network: ask before a password goes there.
confirm_address() {
    case "$1" in
        10.*|127.*|192.168.*|169.254.*|172.1[6-9].*|172.2[0-9].*|172.3[01].*) return 0 ;;
        [0-9]*.[0-9]*.[0-9]*.[0-9]*)
            printf '  %s is not an address on a home or office network. Your password would travel to it.\n  Continue anyway? [y/N] ' "$1" >/dev/tty
            read -r A </dev/tty
            case "$A" in y|Y|yes|YES) return 0 ;; *) exit 1 ;; esac ;;
    esac
    return 0
}

find_router() {
    [ -n "$ROUTER" ] && return
    SAVED=""; [ -f "$STATE_FILE" ] && SAVED="$(head -n 1 "$STATE_FILE")"
    for C in "$SAVED" "$(gateway)" 192.168.8.1; do
        valid_host "$C" || continue
        if ssh_open "$C"; then ROUTER="$C"; return; fi
    done
    printf '  Router address (e.g. 192.168.8.1): ' >/dev/tty
    read -r ROUTER </dev/tty
    valid_host "$ROUTER" || { printf '  That does not look like an address.\n' >&2; exit 1; }
}

# Reads N bytes at OFFSET of FILE as unsigned numbers, one per line. od -tu1 reads single bytes,
# so the result is the same whatever the byte order of this computer.
bytes_at() { od -An -v -tu1 -j"$2" -N"$3" "$1" | awk '{ for (i = 1; i <= NF; i++) print $i }'; }

# Little-endian u16 / u32 from bytes_at.
u16_at() { bytes_at "$1" "$2" 2 | { read -r A; read -r B; echo $((A + B * 256)); }; }
u32_at() { bytes_at "$1" "$2" 4 | { read -r A; read -r B; read -r C; read -r D; echo $((A + B * 256 + C * 65536 + D * 16777216)); }; }

# Returns 0 and prints a summary if $1 is a valid .bea; otherwise prints the reason.
# The same checks as Set-Animation on Windows and Studio Link: header, size, every BEA2
# record, and the 25-second limit on a loop.
check_bea() {
    F="$1"
    [ -f "$F" ] || { echo "I can't find that file."; return 1; }
    SIZE="$(wc -c < "$F" | tr -d ' ')"
    [ "$SIZE" -ge 12 ] || { echo "That file is too small to be a .bea animation."; return 1; }
    MAGIC="$(head -c 4 "$F")"
    case "$MAGIC" in BEA1|BEA2) ;; *) echo "That is not a .bea animation (it does not start with BEA1 or BEA2)."; return 1 ;; esac
    FPS="$(u16_at "$F" 4)"
    REC="$(u16_at "$F" 6)"
    FB="$(u32_at "$F" 8)"
    [ "$FB" -eq "$FRAME_BYTES" ] || { echo "Its frames are $FB bytes; this display needs $FRAME_BYTES (76 x 284 pixels)."; return 1; }
    if [ "$FPS" -lt 1 ] || [ "$FPS" -gt 24 ]; then
        echo "Its speed is $FPS fps; it must be 1 to 24."
        return 1
    fi
    [ "$REC" -ge 1 ] || { echo "It has no frames."; return 1; }
    TICKS=0
    if [ "$MAGIC" = "BEA1" ]; then
        EXPECTED=$((12 + REC * (2 + FB)))
        [ "$SIZE" -eq "$EXPECTED" ] || { echo "Its size is $SIZE bytes but its header says $EXPECTED. It may be incomplete."; return 1; }
        N=0
        while [ "$N" -lt "$REC" ]; do
            TICKS=$((TICKS + $(u16_at "$F" $((12 + N * (2 + FB))))))
            N=$((N + 1))
        done
    else
        # BEA2: walk the records (u16 run, u8 kind, u32 payload length, payload), one read each.
        # The router checks every span of every delta when the file is installed.
        EXPECTED=12
        N=0
        while [ "$N" -lt "$REC" ]; do
            [ $((EXPECTED + 7)) -le "$SIZE" ] || { echo "It is cut off at frame $((N + 1)). It may be incomplete."; return 1; }
            # shellcheck disable=SC2046  # seven numbers, split on purpose into $1..$7
            set -- $(bytes_at "$F" "$EXPECTED" 7)
            KIND="$3"
            PLEN=$(($4 + $5 * 256 + $6 * 65536 + $7 * 16777216))
            if [ "$KIND" -gt 2 ] || { [ "$N" -eq 0 ] && [ "$KIND" -ne 0 ]; }; then
                echo "Frame $((N + 1)) is not valid BEA2 data."; return 1
            fi
            TICKS=$((TICKS + $1 + $2 * 256))
            EXPECTED=$((EXPECTED + 7 + PLEN))
            N=$((N + 1))
        done
        [ "$SIZE" -eq "$EXPECTED" ] || { echo "Its size is $SIZE bytes but its frames add up to $EXPECTED. It may be incomplete."; return 1; }
    fi
    # Tenths of a second, in whole numbers (sh has no fractions).
    TENTHS=$(((TICKS * 10 + FPS / 2) / FPS))
    if [ "$TICKS" -gt $((25 * FPS)) ]; then
        echo "It loops for $((TENTHS / 10)).$((TENTHS % 10)) seconds; the limit is 25 seconds."
        return 1
    fi
    echo "$REC frames, $FPS fps, $((TENTHS / 10)).$((TENTHS % 10)) s per loop"
    return 0
}

read_dropped() {
    printf '\n  %sDROP YOUR .bea FILE HERE%s  (drag it into this window, then press Enter; just Enter to quit)\n  File: ' "$CY$BD" "$RS" >&2
    read -r P </dev/tty || return 1
    unescape_dropped "$P"
}

# Terminals type a dropped file's path either in quotes or with a backslash before every
# character the shell treats specially. macOS Terminal and iTerm escape far more than spaces:
# "eyes (1).bea" arrives as eyes\ \(1\).bea, "Mia's.bea" as Mia\'s.bea. The trailing space
# Terminal adds after a drop is dropped too.
unescape_dropped() {
    printf '%s' "$1" | sed "s/^[[:space:]]*//; s/[[:space:]]*\$//; s/^'\(.*\)'\$/\1/; s/^\"\(.*\)\"\$/\1/; s/\\\\\(.\)/\1/g"
}

printf '\n  %s###%s  %sGL.iNet Router Screen Saver (BE3600)%s\n  %s###%s  Animation drop zone\n' "$CY" "$RS" "$BD" "$RS" "$CY" "$RS"

run_ssh() {
    if [ -n "$KEY" ]; then
        ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -i "$KEY" -o BatchMode=yes "$@"
    else
        ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "$@"
    fi
}
DONE=0

while :; do
    [ -n "$FILE" ] || FILE="$(read_dropped)" || break
    [ -n "$FILE" ] || break

    printf '\n  %s[check]%s Looking at %s\n' "$CY" "$RS" "$(basename "$FILE")"
    if ! SUMMARY="$(check_bea "$FILE")"; then
        printf '      %sx%s   %s\n' "$RD" "$RS" "$SUMMARY"
        FILE=""; continue
    fi
    printf '      %sok%s  %s\n' "$GR" "$RS" "$SUMMARY"

    find_router
    confirm_address "$ROUTER"
    printf '\n  %s[send]%s  Sending it to %s\n' "$CY" "$RS" "$ROUTER"
    printf '\n  %sType your router admin password when asked (nothing shows while you type).%s\n\n' "$YE" "$RS"

    RC=0
    # The file's name (letters, digits . _ - only) becomes its name in the router's library.
    LIBNAME="$(basename "$FILE")"; LIBNAME="${LIBNAME%.[Bb][Ee][Aa]}"
    LIBNAME="$(printf '%s' "$LIBNAME" | sed 's/[^A-Za-z0-9._-][^A-Za-z0-9._-]*/-/g; s/^-*//; s/-*$//' | cut -c1-40)"
    [ -n "$LIBNAME" ] || LIBNAME="animation"
    # A fresh private temp name on the router each time (never a fixed, guessable path).
    # shellcheck disable=SC2029  # LIBNAME is sanitized above and is meant to expand on this side
    run_ssh "root@$ROUTER" "T=\$(mktemp /tmp/be3600-new.XXXXXX) && cat > \$T && be3600-anim set \$T $LIBNAME; R=\$?; rm -f \$T; exit \$R" < "$FILE" || RC=$?

    echo
    if [ "$RC" -eq 0 ]; then
        mkdir -p "$(dirname "$STATE_FILE")" && printf '%s\n' "$ROUTER" > "$STATE_FILE"
        DONE=$((DONE + 1))
        printf '  %sDone - your animation is on the router.%s Swipe or tap the screen for the next animation, double-tap to dismiss it.\n' "$GR" "$RS"
    elif [ "$RC" -eq 255 ]; then
        printf '  %sCould not connect or log in.%s\n' "$RD" "$RS"; ROUTER=""
    else
        printf '  %sThe router did not accept that file (see the message above).%s\n' "$RD" "$RS"
    fi
    FILE=""
done

echo
[ "$DONE" -gt 0 ] && printf '  %sBye!%s\n\n' "$CY" "$RS"
exit 0
