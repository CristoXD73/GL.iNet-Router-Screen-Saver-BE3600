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
        [0-9]*.[0-9]*.[0-9]*.[0-9]*)   # looks like an IPv4 address
            ROUTER="$1"; shift ;;
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
ssh_open() { if command -v nc >/dev/null 2>&1; then nc -z -w 2 "$1" 22 >/dev/null 2>&1; else return 0; fi; }

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

# Returns 0 and prints a summary if $1 is a valid .bea; otherwise prints the reason.
check_bea() {
    F="$1"
    [ -f "$F" ] || { echo "I can't find that file."; return 1; }
    SIZE="$(wc -c < "$F" | tr -d ' ')"
    [ "$SIZE" -ge 12 ] || { echo "That file is too small to be a .bea animation."; return 1; }
    MAGIC="$(head -c 4 "$F")"
    case "$MAGIC" in BEA1|BEA2) ;; *) echo "That is not a .bea animation (it does not start with BEA1 or BEA2)."; return 1 ;; esac
    FPS="$(od -An -tu2 -j4 -N2 "$F" | tr -d ' ')"
    REC="$(od -An -tu2 -j6 -N2 "$F" | tr -d ' ')"
    FB="$(od -An -tu4 -j8 -N4 "$F" | tr -d ' ')"
    [ "$FB" -eq "$FRAME_BYTES" ] || { echo "Its frames are $FB bytes; this display needs $FRAME_BYTES (76 x 284 pixels)."; return 1; }
    if [ "$FPS" -lt 1 ] || [ "$FPS" -gt 24 ]; then
        echo "Its speed is $FPS fps; it must be 1 to 24."
        return 1
    fi
    [ "$REC" -ge 1 ] || { echo "It has no frames."; return 1; }
    if [ "$MAGIC" = "BEA1" ]; then
        EXPECTED=$((12 + REC * (2 + FB)))
    else
        # BEA2: walk the records (u16 run, u8 kind, u32 payload length, payload).
        # The router checks every span of every delta when the file is installed.
        EXPECTED=12
        N=0
        while [ "$N" -lt "$REC" ]; do
            [ $((EXPECTED + 7)) -le "$SIZE" ] || { echo "It is cut off at frame $((N + 1)). It may be incomplete."; return 1; }
            PLEN="$(od -An -tu4 -j$((EXPECTED + 3)) -N4 "$F" | tr -d ' ')"
            EXPECTED=$((EXPECTED + 7 + PLEN))
            N=$((N + 1))
        done
    fi
    [ "$SIZE" -eq "$EXPECTED" ] || { echo "Its size is $SIZE bytes but its header says $EXPECTED. It may be incomplete."; return 1; }
    echo "$REC frames, $FPS fps"
    return 0
}

read_dropped() {
    printf '\n  %sDROP YOUR .bea FILE HERE%s  (drag it into this window, then press Enter; just Enter to quit)\n  File: ' "$CY$BD" "$RS" >&2
    read -r P </dev/tty || return 1
    # Terminals paste dropped paths quoted or with backslash-escaped spaces.
    printf '%s' "$P" | sed "s/^[[:space:]]*//; s/[[:space:]]*\$//; s/^['\"]//; s/['\"]\$//; s/\\\\ / /g"
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
    LIBNAME="$(printf '%s' "$LIBNAME" | sed 's/[^A-Za-z0-9._-][^A-Za-z0-9._-]*/-/g' | cut -c1-40)"
    [ -n "$LIBNAME" ] || LIBNAME="animation"
    # A fresh private temp name on the router each time (never a fixed, guessable path).
    # shellcheck disable=SC2029  # LIBNAME is sanitized above and is meant to expand on this side
    run_ssh "root@$ROUTER" "T=\$(mktemp /tmp/be3600-new.XXXXXX) && cat > \$T && be3600-anim set \$T $LIBNAME; R=\$?; rm -f \$T; exit \$R" < "$FILE" || RC=$?

    echo
    if [ "$RC" -eq 0 ]; then
        mkdir -p "$(dirname "$STATE_FILE")" && printf '%s\n' "$ROUTER" > "$STATE_FILE"
        DONE=$((DONE + 1))
        printf '  %sDone - your animation is on the router.%s Tap the screen for the next animation, double-tap to dismiss it.\n' "$GR" "$RS"
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
