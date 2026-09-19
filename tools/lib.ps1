# Shared helpers for Install.cmd and Set-Animation.cmd (dot-sourced).
# Kept ASCII-only on purpose: Windows PowerShell 5.1 misreads UTF-8 without a
# BOM, so the box-drawing characters are built from character codes.

$ErrorActionPreference = 'Stop'

$Script:FrameBytes = 43168      # 76 x 284 pixels x 2 bytes; see docs/BEA-FORMAT.md
$Script:StateDir   = Join-Path $env:LOCALAPPDATA 'be3600-screensaver'
$Script:StateFile  = Join-Path $Script:StateDir 'router.txt'

# ----------------------------------------------------------------------------
# Pretty output
# ----------------------------------------------------------------------------

$Script:HL = [string][char]0x2500
$Script:VL = [string][char]0x2502
$Script:TL = [string][char]0x250C
$Script:TR = [string][char]0x2510
$Script:BL = [string][char]0x2514
$Script:BR = [string][char]0x2518
$Script:BLOCK = [string][char]0x2588

function Write-Box {
    param([string[]]$Lines, [string]$Color = 'Cyan', [int]$MinWidth = 48)
    $w = $MinWidth
    foreach ($l in $Lines) { if ($l.Length + 4 -gt $w) { $w = $l.Length + 4 } }
    Write-Host ('  ' + $Script:TL + ($Script:HL * $w) + $Script:TR) -ForegroundColor $Color
    foreach ($l in $Lines) {
        Write-Host ('  ' + $Script:VL) -ForegroundColor $Color -NoNewline
        Write-Host ('  ' + $l.PadRight($w - 2)) -NoNewline
        Write-Host $Script:VL -ForegroundColor $Color
    }
    Write-Host ('  ' + $Script:BL + ($Script:HL * $w) + $Script:BR) -ForegroundColor $Color
}

function Show-Banner {
    param([string]$Title, [string]$Subtitle)
    try { $Host.UI.RawUI.WindowTitle = $Title } catch {}
    Write-Host ''
    Write-Host ('  ' + ($Script:BLOCK * 3) + '  ') -ForegroundColor Cyan -NoNewline
    Write-Host $Title -ForegroundColor White
    Write-Host ('  ' + ($Script:BLOCK * 3) + '  ') -ForegroundColor DarkCyan -NoNewline
    Write-Host $Subtitle -ForegroundColor Gray
    Write-Host ''
}

function Write-Step { param([string]$N, [string]$Text) Write-Host ''; Write-Host "  [$N] " -ForegroundColor Cyan -NoNewline; Write-Host $Text -ForegroundColor White }
function Write-Ok   { param([string]$Text) Write-Host '      ok  ' -ForegroundColor Green -NoNewline; Write-Host $Text }
function Write-Info { param([string]$Text) Write-Host "      $Text" -ForegroundColor Gray }
function Write-Warn { param([string]$Text) Write-Host '      !   ' -ForegroundColor Yellow -NoNewline; Write-Host $Text }
function Write-Bad  { param([string]$Text) Write-Host '      x   ' -ForegroundColor Red -NoNewline; Write-Host $Text }

# ----------------------------------------------------------------------------
# Finding the router
#
# Only a few well-known addresses are ever probed (never a scan):
#   1. the address that worked last time (saved on this PC)
#   2. this PC's default gateway (right if you're on the BE3600's own network)
#   3. GL.iNet's factory address, 192.168.8.1
# If none answers on SSH, the user is asked to type it once; it is then saved.
# ----------------------------------------------------------------------------

function Test-SshPort {
    param([string]$Ip, [int]$TimeoutMs = 1500)
    $c = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $c.BeginConnect($Ip, 22, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne($TimeoutMs) -and $c.Connected) { $c.EndConnect($iar); return $true }
        return $false
    } catch { return $false } finally { $c.Close() }
}

function Get-SavedRouter {
    if (Test-Path -LiteralPath $Script:StateFile) {
        $v = (Get-Content -LiteralPath $Script:StateFile -TotalCount 1 -ErrorAction SilentlyContinue)
        if ($v) { return $v.Trim() }
    }
    return $null
}

function Save-Router {
    param([string]$Ip)
    try {
        New-Item -ItemType Directory -Force -Path $Script:StateDir | Out-Null
        Set-Content -LiteralPath $Script:StateFile -Value $Ip -Encoding ascii
    } catch {}
}

function Get-GatewayCandidates {
    try {
        Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction Stop |
            Where-Object { $_.NextHop -and $_.NextHop -ne '0.0.0.0' } |
            Sort-Object { $_.RouteMetric + $_.InterfaceMetric } |
            ForEach-Object { $_.NextHop } | Select-Object -Unique
    } catch { @() }
}

function Read-RouterAddress {
    for ($i = 0; $i -lt 3; $i++) {
        $a = (Read-Host '      Router address (e.g. 192.168.8.1)').Trim()
        if ($a -match '^\d{1,3}(\.\d{1,3}){3}$') { return $a }
        Write-Warn 'That does not look like an IP address.'
    }
    throw 'No router address given.'
}

function Resolve-Router {
    param([string]$Override)

    if ($Override) { Write-Ok "Using $Override (you asked for it)"; return $Override }

    $saved = Get-SavedRouter
    $cands = @()
    if ($saved) { $cands += $saved }
    $cands += @(Get-GatewayCandidates)
    $cands += '192.168.8.1'
    $cands = $cands | Where-Object { $_ } | Select-Object -Unique

    foreach ($c in $cands) {
        if (Test-SshPort $c) {
            $tag = if ($c -eq $saved) { 'remembered from last time' } else { 'answered on SSH' }
            Write-Ok "Router found at $c ($tag)"
            return (Confirm-Router $c)
        }
    }

    Write-Warn 'Could not find the router on its own (nothing answered on SSH).'
    Write-Info 'Make sure this PC is connected to the router, then type its address.'
    $a = Read-RouterAddress
    return $a
}

# A short window to change the address: press any key within 4 seconds to type another.
function Confirm-Router {
    param([string]$Ip)
    try {
        if ([Console]::IsInputRedirected) { return $Ip }
        Write-Host '      Press any key within 4 seconds to use a different address...' -ForegroundColor DarkGray -NoNewline
        $end = (Get-Date).AddSeconds(4)
        while ((Get-Date) -lt $end) {
            if ([Console]::KeyAvailable) {
                [void][Console]::ReadKey($true)
                Write-Host ''
                return (Read-RouterAddress)
            }
            Start-Sleep -Milliseconds 100
        }
        Write-Host ''
    } catch {}
    return $Ip
}

# ----------------------------------------------------------------------------
# Talking to the router
#
# One ssh session per operation, so the admin password is typed once. Files are
# fed to ssh with cmd.exe's "<" redirection, which is byte-exact (PowerShell's
# own pipe would re-encode the data and corrupt it).
# ----------------------------------------------------------------------------

function Get-Exe {
    param([string]$Name)
    $c = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return $c.Source }
    return $null
}

function Invoke-RouterSsh {
    param([string]$Router, [string]$Remote, [string]$InputFile, [string]$Key)

    $ssh = Get-Exe 'ssh.exe'
    if (-not $ssh) { throw 'ssh.exe was not found. Turn on "OpenSSH Client" under Settings > Apps > Optional features.' }

    $keyArgs = ''
    if ($Key) { $keyArgs = "-i `"$Key`" -o BatchMode=yes " }

    $cmd = "`"$ssh`" $keyArgs-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 root@$Router `"$Remote`""
    if ($InputFile) { $cmd += " < `"$InputFile`"" }

    # /s: cmd strips the outer quotes and runs the rest exactly as written.
    $p = Start-Process -FilePath $env:ComSpec -ArgumentList "/d /s /c `"$cmd`"" -NoNewWindow -Wait -PassThru
    return $p.ExitCode
}

# ----------------------------------------------------------------------------
# Checking a .bea file before it is sent anywhere
# ----------------------------------------------------------------------------

function Test-BeaFile {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return @{ Ok = $false; Reason = "I can't find that file." }
    }

    $fs = [System.IO.File]::OpenRead($Path)
    try {
        if ($fs.Length -lt 12) { return @{ Ok = $false; Reason = 'That file is too small to be a .bea animation.' } }

        $h = New-Object byte[] 12
        [void]$fs.Read($h, 0, 12)

        if ([System.Text.Encoding]::ASCII.GetString($h, 0, 4) -ne 'BEA1') {
            return @{ Ok = $false; Reason = 'That is not a .bea animation (it does not start with the BEA1 marker).' }
        }

        $fps = [int][BitConverter]::ToUInt16($h, 4)
        $rec = [int][BitConverter]::ToUInt16($h, 6)
        $fb  = [int64][BitConverter]::ToUInt32($h, 8)

        if ($fb -ne $Script:FrameBytes) {
            return @{ Ok = $false; Reason = "Its frames are $fb bytes; this display needs $($Script:FrameBytes) (76 x 284 pixels)." }
        }
        if ($fps -lt 1 -or $fps -gt 24) { return @{ Ok = $false; Reason = "Its speed is $fps frames per second; it must be 1 to 24." } }
        if ($rec -lt 1) { return @{ Ok = $false; Reason = 'It has no frames.' } }

        $expected = 12 + $rec * (2 + $fb)
        if ($fs.Length -ne $expected) {
            return @{ Ok = $false; Reason = "Its size is $($fs.Length) bytes but its header says it should be $expected. It may be incomplete." }
        }

        $ticks = 0
        $two = New-Object byte[] 2
        for ($i = 0; $i -lt $rec; $i++) {
            [void]$fs.Seek(12 + $i * (2 + $fb), 'Begin')
            [void]$fs.Read($two, 0, 2)
            $ticks += [int][BitConverter]::ToUInt16($two, 0)
        }

        return @{ Ok = $true; Frames = $rec; Fps = $fps; Seconds = ($ticks / $fps); Bytes = $fs.Length }
    } finally {
        $fs.Dispose()
    }
}

function Format-Size {
    param([int64]$Bytes)
    if ($Bytes -ge 1MB) { return ('{0:N1} MB' -f ($Bytes / 1MB)) }
    return ('{0:N0} KB' -f ($Bytes / 1KB))
}
