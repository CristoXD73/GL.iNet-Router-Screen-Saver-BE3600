#!/bin/sh
# Double-click me on a Mac: Finder opens a .command file in Terminal (a .sh file it would only
# open in a text editor). Lets Motion Studio send animations to your router, and opens it
# (served from this Mac, so it works in Safari). Leave the window open while you use it.
# The same as running ./studio-link.sh in Terminal; options go straight through to it.
cd "$(dirname "$0")" || exit 1
sh ./studio-link.sh "$@"
RC=$?
printf '\n  Press Return to close this window. '
read -r _ </dev/tty 2>/dev/null
exit "$RC"
