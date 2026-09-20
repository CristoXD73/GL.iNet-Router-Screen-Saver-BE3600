# One-click installer for the BE3600 animation screensaver (Windows).
# Normally started by double-clicking Install.cmd.
#
#   -Router  skip auto-detection and use this address
#   -Key     use this SSH private key instead of asking for the admin password
#            (for automation; most people never need it)

param(
    [string]$Router,
    [string]$Key
)

. "$PSScriptRoot\lib.ps1"

if ($Router -and -not (Test-HostName $Router)) { Write-Host '  The router address must be like 192.168.8.1 (letters, digits, dots and dashes only).'; exit 1 }

$root = Split-Path -Parent $PSScriptRoot
$tar  = Join-Path $env:TEMP ('be3600-setup-{0}.tar' -f [guid]::NewGuid().ToString('N'))
$code = 1

Show-Banner 'GL.iNet Router Screen Saver (BE3600)' 'One-click installer'

try {
    # ------------------------------------------------------------------
    Write-Step '1/3' 'Finding your router'
    $ip = Resolve-Router $Router

    # ------------------------------------------------------------------
    Write-Step '2/3' 'Packing the files'
    $tarExe = Get-Exe 'tar.exe'
    if (-not $tarExe) { throw 'tar.exe was not found (it ships with Windows 10 and 11).' }
    if (-not (Get-Exe 'ssh.exe')) { throw 'ssh.exe was not found. Turn on "OpenSSH Client" under Settings > Apps > Optional features.' }

    Push-Location $root
    try { & $tarExe --format ustar -cf $tar router setup animations } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw 'Could not pack the files.' }
    Write-Ok ('Ready ({0})' -f (Format-Size (Get-Item -LiteralPath $tar).Length))

    # ------------------------------------------------------------------
    # Unpacked in a fresh private folder on the router, and removed afterwards; the installer's own exit code is kept.
    $remote = 'D=$(mktemp -d /tmp/be3600-setup.XXXXXX) && cd $D && tar xf - && sh setup/router-install.sh; R=$?; cd /; rm -rf $D; exit $R'
    $attempt = 0

    while ($true) {
        $attempt++
        Write-Step '3/3' "Installing on $ip"
        Write-Host ''
        Write-Box @(
            'Type your router admin password when asked.',
            '(Same one you use on the router''s admin page.',
            ' Nothing shows while you type - that is normal.)'
        ) 'Yellow'
        Write-Host ''

        $code = Invoke-RouterSsh -Router $ip -Remote $remote -InputFile $tar -Key $Key

        # Exit 3: the address answered, but it is not a BE3600 (for example your
        # main router). Ask for the right address right away instead of failing.
        if ($code -eq 3 -and $attempt -lt 3) {
            Write-Host ''
            Write-Warn "The device at $ip is not a GL-BE3600 with a front display."
            Write-Info 'Enter the address of your BE3600 (nothing else was changed).'
            $ip = Read-RouterAddress
            continue
        }
        break
    }
}
catch {
    Write-Host ''
    Write-Bad $_.Exception.Message
    $code = 1
}
finally {
    try { [System.IO.File]::Delete($tar) } catch {}
}

Write-Host ''
if ($code -eq 0) {
    Save-Router $ip
    Write-Box @(
        'All set - the screensaver is installed and running.',
        '',
        'It starts on its own after a few idle seconds.',
        'Tap = next animation. Double-tap = dismiss.',
        '',
        'Change the animation:',
        '   drag a .bea file onto  Set-Animation.cmd',
        '',
        'Turn it off / remove it (over SSH):',
        '   be3600-anim off        be3600-uninstall'
    ) 'Green'
}
elseif ($code -eq 255) {
    Write-Box @(
        'Could not connect or log in.',
        '',
        '- Is this PC connected to the router?',
        '- Is the password the router admin password?',
        '- Router not at the address above? Run again with:',
        '     Install.cmd -Router 192.168.x.x'
    ) 'Red'
}
else {
    Write-Box @(
        'The installer did not finish (see the message above).',
        '',
        'If it says this is not a GL-BE3600, the address may',
        'belong to a different router. Run again with:',
        '     Install.cmd -Router 192.168.x.x'
    ) 'Red'
}
Write-Host ''
exit $code
