#!/bin/sh
#
# Copy this project to a GL-BE3600 from macOS/Linux and install it.
#
#   tools/deploy.sh [router-ip]        (default 192.168.8.1, GL.iNet's factory LAN address)
#
# Needs SSH access as root. `scp -O` is required because OpenWrt's dropbear
# has no SFTP server.

set -e

ROUTER="${1:-192.168.8.1}"

cd "$(dirname "$0")/.."

ssh "root@$ROUTER" 'rm -rf /tmp/be3600-screensaver && mkdir -p /tmp/be3600-screensaver'
scp -O -r router install.sh uninstall.sh "root@$ROUTER:/tmp/be3600-screensaver/"
ssh "root@$ROUTER" 'cd /tmp/be3600-screensaver && sh install.sh'
