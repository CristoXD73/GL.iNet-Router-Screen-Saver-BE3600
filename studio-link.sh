#!/bin/sh
#
# Lets Motion Studio (in your browser) send animations straight to your router.
# Run it, leave the terminal open, and use the drop zone in Motion Studio.
# (Windows: double-click Studio-Link.cmd instead. Mac: you can double-click Studio-Link.command.)
#
#   ./studio-link.sh [--port 8791] [--router 192.168.8.1] [--key FILE] [--dry-run]

cd "$(dirname "$0")" || exit 1

# A Mac without Apple's Command Line Tools has only a stand-in /usr/bin/python3 that offers to
# install them and then stops. Say so, instead of leaving a bare error behind.
if [ "$(uname -s 2>/dev/null)" = "Darwin" ] && [ "$(command -v python3)" = "/usr/bin/python3" ] &&
   ! xcode-select -p >/dev/null 2>&1; then
    echo "Studio Link needs Python 3, which comes with Apple's Command Line Tools." >&2
    echo "macOS will offer to install them now: click Install, wait for it to finish, then start Studio Link again." >&2
    xcode-select --install >/dev/null 2>&1
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "Studio Link needs Python 3 (the project's other helper tools use it too)." >&2
    echo "macOS: run 'xcode-select --install'.  Debian/Ubuntu: sudo apt install python3" >&2
    exit 1
fi

exec python3 tools/studio_link.py "$@"
