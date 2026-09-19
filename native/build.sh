#!/bin/sh
#
# Builds router/usr/bin/be3600-player: a static aarch64 binary for the router.
# Needs aarch64-linux-gnu-gcc, or Docker (then it does the build in a container).
#
#   sh native/build.sh
#
# The router does not need anything installed for it; the binary is fully static.

set -e
cd "$(dirname "$0")/.."

if command -v aarch64-linux-gnu-gcc >/dev/null 2>&1; then
    aarch64-linux-gnu-gcc -O2 -Wall -Wextra -static -s \
        -o router/usr/bin/be3600-player native/be3600-player.c
    ls -l router/usr/bin/be3600-player
elif command -v docker >/dev/null 2>&1; then
    docker run --rm -v "$PWD:/repo" -w /repo ubuntu:22.04 sh -c \
        'apt-get update -qq >/dev/null && apt-get install -y -qq gcc-aarch64-linux-gnu >/dev/null && sh native/build.sh'
else
    echo "Install aarch64-linux-gnu-gcc (Debian/Ubuntu: apt install gcc-aarch64-linux-gnu) or Docker." >&2
    exit 1
fi
