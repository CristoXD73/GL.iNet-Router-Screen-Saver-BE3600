# Drag-and-drop tool: replaces the animation on your BE3600.
# Normally started by dropping a .bea file onto Set-Animation.cmd, or by
# double-clicking it and then dragging a file into the window.
#
#   -Path    the .bea file (optional; you will be asked if omitted)
#   -Router  skip auto-detection and use this address
#   -Key     use this SSH private key instead of the admin password

param(
    [string]$Path,
    [string]$Router,
    [string]$Key
)

. "$PSScriptRoot\lib.ps1"

function Read-DroppedPath {
    param([switch]$Again)
    Write-Host ''
    if ($Again) {
        $s = Read-Host '  Drop another file here (or press Enter to finish)'
    } else {
        Write-Box @(
            '',
            '     DROP YOUR  .bea  FILE HERE',
            '',
            '     Drag it into this window, then press Enter.',
            '     (Or just press Enter to quit.)',
            ''
        ) 'Cyan' 54
        Write-Host ''
        $s = Read-Host '  File'
    }
    if (-not $s) { return $null }
    $s = $s.Trim()
    $s = $s -replace '^&\s*', ''          # PowerShell adds "& " when a path with spaces is dropped
    $s = $s.Trim().Trim('"').Trim("'")
    return $s
}

Show-Banner 'GL.iNet Router Screen Saver (BE3600)' 'Animation drop zone'

$ip = $null
$done = 0

while ($true) {

    if (-not $Path) { $Path = Read-DroppedPath -Again:($done -gt 0) }
    if (-not $Path) { break }

    $name = Split-Path -Leaf $Path

    Write-Step 'check' "Looking at $name"
    $info = Test-BeaFile $Path
    if (-not $info.Ok) {
        Write-Bad $info.Reason
        Write-Info 'A valid file is a .bea for this display (see docs/BEA-FORMAT.md).'
        Write-Info 'To make a test one: python tools/make-sample-bea.py colors test.bea'
        $Path = $null
        continue
    }
    Write-Ok ('{0} frames, {1} fps, {2:N1} s per loop, {3}' -f $info.Frames, $info.Fps, $info.Seconds, (Format-Size $info.Bytes))
    if ($info.Seconds -gt 25) {
        Write-Bad ('It loops for {0:N1} seconds; the limit is 25 seconds.' -f $info.Seconds)
        Write-Info 'Make it shorter, or lower its length in the Studio, and try again.'
        $Path = $null
        continue
    }

    if (-not $ip) {
        Write-Step 'find' 'Finding your router'
        $ip = Resolve-Router $Router
    }

    Write-Step 'send' "Sending it to $ip"
    Write-Host ''
    Write-Box @(
        'Type your router admin password when asked.',
        '(Nothing shows while you type - that is normal.)'
    ) 'Yellow'
    Write-Host ''

    # The file's name (letters, digits . _ - only, so it is safe in a shell command)
    # becomes its name in the router's library.
    $libName = ConvertTo-LibName $name
    $remote = "cat > /tmp/be3600-new.bea && be3600-anim set /tmp/be3600-new.bea $libName && rm -f /tmp/be3600-new.bea"
    $code = Invoke-RouterSsh -Router $ip -Remote $remote -InputFile $Path -Key $Key

    Write-Host ''
    if ($code -eq 0) {
        Save-Router $ip
        $done++
        Write-Box @(
            'Done - your animation is on the router.',
            '',
            ('  {0}' -f $name),
            ('  {0:N1} seconds per loop, shows after a few idle seconds' -f $info.Seconds),
            '',
            'Tap for the next animation, double-tap to dismiss it.'
        ) 'Green'
    }
    elseif ($code -eq 255) {
        Write-Box @('Could not connect or log in.', '', 'Check the connection and the admin password, then try again.') 'Red'
        $ip = $null
    }
    else {
        Write-Box @('The router did not accept that file (see the message above).') 'Red'
    }

    # Loop back for another drop (Enter on an empty prompt finishes).
    $Path = $null
}

Write-Host ''
if ($done -gt 0) { Write-Host '  Bye!' -ForegroundColor Cyan }
Write-Host ''
