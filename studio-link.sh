#!/bin/sh
#
# Lets Motion Studio (in your browser) send animations straight to your router.
# Run it, leave the terminal open, and use the drop zone in Motion Studio.
# (Windows: double-click Studio-Link.cmd instead.)
#
#   ./studio-link.sh [--port 8791] [--router 192.168.8.1] [--key FILE] [--dry-run]

cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
    echo "Studio Link needs Python 3 (the project's other helper tools use it too)." >&2
    echo "macOS: run 'xcode-select --install'.  Debian/Ubuntu: sudo apt install python3" >&2
    exit 1
fi

exec python3 tools/studio_link.py "$@"
