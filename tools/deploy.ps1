# Copy this project to a GL-BE3600 from Windows and install it.
#
#   powershell -File tools\deploy.ps1 [-Router 192.168.8.1] [-Key C:\path\to\private_key]
#
# Needs the OpenSSH client (built into Windows 10/11) and SSH access as root.
# `scp -O` is required because OpenWrt's dropbear has no SFTP server.
# Files are copied with scp, never piped through PowerShell: PowerShell's pipe
# to a native program re-adds Windows line endings, which break BusyBox sh.

param(
    [string]$Router = '192.168.8.1',
    [string]$Key
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$sshArgs = @()
if ($Key) { $sshArgs = @('-i', $Key) }

& ssh @sshArgs "root@$Router" 'rm -rf /tmp/be3600-screensaver && mkdir -p /tmp/be3600-screensaver'
if ($LASTEXITCODE -ne 0) { throw 'ssh failed' }

& scp -O -r @sshArgs (Join-Path $root 'router') (Join-Path $root 'install.sh') (Join-Path $root 'uninstall.sh') "root@${Router}:/tmp/be3600-screensaver/"
if ($LASTEXITCODE -ne 0) { throw 'scp failed' }

& ssh @sshArgs "root@$Router" 'cd /tmp/be3600-screensaver && sh install.sh'
if ($LASTEXITCODE -ne 0) { throw 'install failed' }
