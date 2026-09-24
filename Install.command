#!/bin/sh
# Double-click me on a Mac: Finder opens a .command file in Terminal (a .sh file it would only
# open in a text editor). Installs the GL.iNet Router Screen Saver (BE3600) on your router.
# The same as running ./install.sh in Terminal; options go straight through to it.
cd "$(dirname "$0")" || exit 1
sh ./install.sh "$@"
RC=$?
printf '\n  Press Return to close this window. '
read -r _ </dev/tty 2>/dev/null
exit "$RC"
