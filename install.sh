#!/bin/sh
#
# One-command installer for macOS and Linux (Windows: double-click Install.cmd).
#
#   ./install.sh                     find the router automatically
#   ./install.sh 192.168.8.1         use this address
#   ./install.sh -i ~/.ssh/id_key    log in with a key instead of the admin password
#
# You only type your router's admin password once, when ssh asks for it.

set -e
cd "$(dirname "$0")"

KEY=""
ROUTER=""
while [ $# -gt 0 ]; do
    case "$1" in
        -i)  KEY="$2"; shift 2 ;;
        -h|--help) sed -n '3,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)   ROUTER="$1"; shift ;;
    esac
done

if [ -t 1 ]; then
    CY="$(printf '\033[36m')"; GR="$(printf '\033[32m')"; YE="$(printf '\033[33m')"
    RD="$(printf '\033[31m')"; BD="$(printf '\033[1m')"; DM="$(printf '\033[2m')"; RS="$(printf '\033[0m')"
else
    CY=""; GR=""; YE=""; RD=""; BD=""; DM=""; RS=""
fi

step() { printf '\n  %s[%s]%s %s%s%s\n' "$CY" "$1" "$RS" "$BD" "$2" "$RS"; }
ok()   { printf '      %sok%s  %s\n' "$GR" "$RS" "$1"; }
warn() { printf '      %s!%s   %s\n' "$YE" "$RS" "$1"; }

STATE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/be3600-screensaver"
STATE_FILE="$STATE_DIR/router"

gateway() {
    if command -v ip >/dev/null 2>&1; then
        ip route show default 2>/dev/null | awk '/^default/ {print $3; exit}'
    elif command -v route >/dev/null 2>&1; then
        route -n get default 2>/dev/null | awk '/gateway:/ {print $2; exit}'
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

ask_address() {
    printf '      Router address (e.g. 192.168.8.1): ' >/dev/tty
    read -r ANSWER </dev/tty
    valid_host "$ANSWER" || { printf '      That does not look like an address.\n' >&2; exit 1; }
    printf '%s' "$ANSWER"
}

if [ -n "$ROUTER" ] && ! valid_host "$ROUTER"; then
    printf 'The router address must be like 192.168.8.1 (letters, digits, dots and dashes only).\n' >&2
    exit 1
fi

# Only a few well-known addresses are probed (never a scan): the one that worked
# last time, this computer's default gateway, and GL.iNet's factory address.
find_router() {
    [ -n "$ROUTER" ] && { ok "Using $ROUTER (you asked for it)"; return; }
    SAVED=""; [ -f "$STATE_FILE" ] && SAVED="$(head -n 1 "$STATE_FILE")"
    for C in "$SAVED" "$(gateway)" 192.168.8.1; do
        valid_host "$C" || continue
        if ssh_open "$C"; then
            ROUTER="$C"
            if [ "$C" = "$SAVED" ]; then ok "Router found at $C (remembered from last time)"
            else ok "Router found at $C (answered on SSH)"; fi
            printf '      %sTo use a different address, run: ./install.sh 192.168.x.x%s\n' "$DM" "$RS"
            return
        fi
    done
    warn "Could not find the router on its own (nothing answered on SSH)."
    ROUTER="$(ask_address)"
}

printf '\n  %s###%s  %sGL.iNet Router Screen Saver (BE3600)%s\n  %s###%s  One-click installer\n' "$CY" "$RS" "$BD" "$RS" "$CY" "$RS"

# A plain IPv4 address that is not on a home or office network: ask before a password goes there.
confirm_address() {
    case "$1" in
        10.*|127.*|192.168.*|169.254.*|172.1[6-9].*|172.2[0-9].*|172.3[01].*) return 0 ;;
        [0-9]*.[0-9]*.[0-9]*.[0-9]*)
            printf '      %s is not an address on a home or office network. Your password would travel to it.\n      Continue anyway? [y/N] ' "$1" >/dev/tty
            read -r A </dev/tty
            case "$A" in y|Y|yes|YES) return 0 ;; *) exit 1 ;; esac ;;
    esac
    return 0
}

step "1/3" "Finding your router"
find_router
confirm_address "$ROUTER"

step "2/3" "Packing the files"
command -v ssh >/dev/null 2>&1 || { printf '      %sx%s   ssh was not found.\n' "$RD" "$RS"; exit 1; }
ok "Ready"

run_ssh() {
    if [ -n "$KEY" ]; then
        ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -i "$KEY" -o BatchMode=yes "$@"
    else
        ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "$@"
    fi
}
# Unpacked in a fresh private folder on the router, and removed afterwards; the installer's own exit code is kept.
# shellcheck disable=SC2016  # the $ signs are meant to expand on the router, not here
REMOTE='D=$(mktemp -d /tmp/be3600-setup.XXXXXX) && cd $D && tar xf - && sh setup/router-install.sh; R=$?; cd /; rm -rf $D; exit $R'
export COPYFILE_DISABLE=1   # macOS tar: don't add ._ resource-fork files

ATTEMPT=0
while :; do
    ATTEMPT=$((ATTEMPT + 1))
    step "3/3" "Installing on $ROUTER"
    printf '\n  %sType your router admin password when asked (nothing shows while you type).%s\n\n' "$YE" "$RS"

    RC=0
    # Finder leaves .DS_Store (and ._ files on some drives) in any folder you open; the router
    # has no use for them. Both BSD tar (macOS) and GNU tar know --exclude.
    tar --format ustar --exclude '.DS_Store' --exclude '._*' -cf - router setup animations |
        run_ssh "root@$ROUTER" "$REMOTE" || RC=$?

    # Exit 3: the address answered, but it is not a BE3600 (e.g. your main router).
    if [ "$RC" -eq 3 ] && [ "$ATTEMPT" -lt 3 ]; then
        warn "The device at $ROUTER is not a GL-BE3600 with a front display."
        printf '      Enter the address of your BE3600 (nothing else was changed).\n'
        ROUTER="$(ask_address)"
        continue
    fi
    break
done

echo
if [ "$RC" -eq 0 ]; then
    mkdir -p "$STATE_DIR" && printf '%s\n' "$ROUTER" > "$STATE_FILE"
    printf '  %sAll set - the screensaver is installed and running.%s\n' "$GR" "$RS"
    printf '  It starts on its own after a few idle seconds. Swipe or tap = next animation or page, double-tap = dismiss.\n'
    printf '  The live pages (clock, speed, vitals, ...) are on; choose yours in Motion Studio.\n'
    printf '  Design and send animations:  ./studio-link.sh   (opens Motion Studio, connected)\n'
    printf '  Or send a .bea file:          ./set-animation.sh   (then drag it into the window)\n'
    printf '  Turn it off / remove:  be3600-anim off   /   be3600-uninstall   (over SSH)\n\n'
elif [ "$RC" -eq 255 ]; then
    printf '  %sCould not connect or log in.%s Check the connection and that the password is the router admin password.\n\n' "$RD" "$RS"
else
    printf '  %sThe installer did not finish (see the message above).%s\n\n' "$RD" "$RS"
fi
exit "$RC"
