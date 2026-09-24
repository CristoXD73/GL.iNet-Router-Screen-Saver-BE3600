#!/bin/sh
# Double-click me on a Mac: Finder opens a .command file in Terminal (a .sh file it would only
# open in a text editor). Sends a .bea animation to your router: drag the file into the window
# when it asks.
# The same as running ./set-animation.sh in Terminal; options go straight through to it.
cd "$(dirname "$0")" || exit 1
sh ./set-animation.sh "$@"
RC=$?
printf '\n  Press Return to close this window. '
read -r _ </dev/tty 2>/dev/null
exit "$RC"
