#!/bin/sh
# Runs INSIDE the pinned Debian container started by native/build-reproducible.sh.
# Needs SNAPSHOT (a Debian archive timestamp) in the environment; the repo is at /repo
# (read-only) and the result goes to /out/be3600-player.
set -e
rm -f /etc/apt/sources.list.d/*
echo "deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/$SNAPSHOT/ bookworm main" > /etc/apt/sources.list
apt-get -o Acquire::Check-Valid-Until=false -o Acquire::Retries=5 update -qq
apt-get install -y -qq --no-install-recommends gcc-aarch64-linux-gnu libc6-dev-arm64-cross >/dev/null
SOURCE_DATE_EPOCH=1 aarch64-linux-gnu-gcc -O2 -Wall -Wextra -Werror -static -s -fno-ident \
    -ffile-prefix-map=/repo=. -Wl,--build-id=none \
    -o /out/be3600-player /repo/native/be3600-player.c
