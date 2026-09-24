#!/bin/sh
#
# Builds router/usr/bin/be3600-player so that ANYONE gets byte-for-byte the same file, which
# is how you can check that the binary shipped in this repo really comes from
# native/be3600-player.c and not from somewhere else.
#
#   sh native/build-reproducible.sh            rebuild it (needs Docker)
#   sh native/build-reproducible.sh --check    rebuild into a temp file and compare with the
#                                              committed binary; exit 1 if they differ
#
# How it stays identical: the compiler comes from a Debian image pinned by its digest, with
# the compiler packages taken from a dated snapshot of the Debian archive (so a newer
# package can never sneak in), and the build embeds no paths, times or build ids.
# native/be3600-player.sha256 records the SHA-256 of the result.

set -e
cd "$(dirname "$0")/.."

IMAGE="debian@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251"   # debian:bookworm-slim
SNAPSHOT="20250915T000000Z"                                                            # Debian archive, that day

command -v docker >/dev/null 2>&1 || { echo "Docker is needed for the reproducible build." >&2; exit 1; }

OUT="$PWD/router/usr/bin/be3600-player"   # absolute: docker only bind-mounts absolute paths
[ "$1" = "--check" ] && OUT="$(mktemp)"

docker run --rm -v "$PWD:/repo:ro" -v "$OUT:/out/be3600-player" -e SNAPSHOT="$SNAPSHOT" "$IMAGE" \
    sh /repo/native/reproducible-inner.sh

GOT="$(sha256sum "$OUT" | cut -d' ' -f1)"

if [ "$1" = "--check" ]; then
    HAVE="$(sha256sum router/usr/bin/be3600-player | cut -d' ' -f1)"
    rm -f "$OUT"
    echo "committed: $HAVE"
    echo "rebuilt:   $GOT"
    [ "$HAVE" = "$GOT" ] || { echo "DIFFERENT: the committed binary is not what this source builds." >&2; exit 1; }
    echo "identical: the committed binary is exactly what native/be3600-player.c builds."
else
    printf '%s  be3600-player\n' "$GOT" > native/be3600-player.sha256
    ls -l "$OUT"
    echo "sha256 $GOT (written to native/be3600-player.sha256)"
fi
