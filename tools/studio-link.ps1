# Studio Link: lets Motion Studio (in your browser) send animations to your router and
# show what is on it. Start it with Studio-Link.cmd, type your router's admin password
# once when it asks, and leave the window open.
#
# It listens on THIS computer only (127.0.0.1), so nothing else on your network can
# reach it. The password is kept in memory only while this window is open (never written
# anywhere, never given to the web page) and is handed to ssh through a small helper
# program, so nothing asks again.
#
#   -Port    port to listen on (default 8791)
#   -Router  skip auto-detection and use this address
#   -Key     use this SSH private key instead of the admin password
#   -DryRun  check files but send nothing (for testing)
#   -NoBrowser  do not open Motion Studio automatically
#   -Forget  remove the remembered login from the router and this computer
#   -Install    put the screen saver on your router, step by step (what Install-Screen-Saver.cmd runs)
#   -Uninstall  take it off your router and this computer, step by step (Uninstall-Screen-Saver.cmd)
#
# The first time, it also puts the screen saver on your router if it is not there yet
# (press Enter to agree), and offers to remember this computer so you never type the
# password again. It then opens Motion Studio in your browser.
#
# Who may use it: only a page served from the Motion Studio site (or localhost) AND that
# holds this run's secret token. The token is made fresh each time Studio Link starts and
# is handed to the page in the address Studio Link opens, so other websites, other
# programs and other users on this computer cannot drive it. To allow another site (for
# example your own fork's GitHub Pages address), set STUDIO_LINK_ORIGINS to a
# comma-separated list before starting it.

param(
    [int]$Port = 8791,
    [string]$Router,
    [string]$Key,
    [switch]$DryRun,
    [switch]$NoBrowser,
    [switch]$Forget,
    [switch]$Install,
    [switch]$Uninstall
)

. "$PSScriptRoot\lib.ps1"

$Script:MaxBody = 32MB
$Script:Latin1 = [System.Text.Encoding]::GetEncoding(28591)
# Test hooks (a stand-in ssh, answering "yes" for you) only work when BE3600_TESTING is
# set, so nothing in a normal environment can switch them on by accident.
$Script:Testing = [bool]$env:BE3600_TESTING

# "null" (a sandboxed page or a local file) is NOT accepted by default: any website can
# produce that origin. Add it yourself in STUDIO_LINK_ORIGINS if you open a local copy.
$Script:AllowedOrigins = @('https://cristoxd73.github.io')
if ($env:STUDIO_LINK_ORIGINS) {
    $Script:AllowedOrigins += @($env:STUDIO_LINK_ORIGINS -split ',' | ForEach-Object { $_.Trim() } |
        Where-Object { $_ -match '^(null|https?://[A-Za-z0-9.-]+(:\d+)?)$' })
}
$Script:RouterIp = $null
$Script:Sent = 0
$Script:Password = $null
$Script:KeyPass = $null                       # the remembered key's passphrase (memory only)
$Script:AskPassDir = $null
$Script:AskPassPath = $null
$Script:Token = $null                         # this run's secret; the page must present it
$Script:Paired = $false                       # has a page proved it holds the token yet?
$Script:KeyPath = Join-Path $Script:StateDir 'studio-key'     # the "remember this computer" key
$Script:PassPath = $Script:KeyPath + '.pass'                  # its passphrase, locked to this Windows account (DPAPI)
$Script:KeyComment = 'be3600-studio-link-' + (($env:COMPUTERNAME -replace '[^A-Za-z0-9_-]', '-'))
$Script:AuthKeys = '/etc/dropbear/authorized_keys'
$Script:DefaultStudioUrl = 'https://cristoxd73.github.io/GL.iNet-Router-Screen-Saver-BE3600/studio/'
$Script:StudioUrl = $Script:DefaultStudioUrl
# Only an https address (or localhost) may be opened; never file:, javascript: or anything odd.
if ($env:STUDIO_LINK_URL -and $env:STUDIO_LINK_URL -match '^(https://[A-Za-z0-9.-]+(:\d+)?/[^\s#]*|http://(localhost|127\.0\.0\.1)(:\d+)?/[^\s#]*)$') {
    $Script:StudioUrl = $env:STUDIO_LINK_URL
}
if ($Router -and -not (Test-HostName $Router)) {
    Write-Host '  The router address must be like 192.168.8.1 (letters, digits, dots and dashes only).'
    exit 1
}


# ----------------------------------------------------------------------------
# Who may talk to this: only a browser page from an allowed origin, and only
# when it addressed us as localhost / 127.0.0.1 (which also defeats DNS tricks).
# ----------------------------------------------------------------------------

function Test-OriginAllowed {
    param([string]$Origin)
    if (-not $Origin) { return $true }                    # not a browser (curl and friends)
    if ($Script:AllowedOrigins -contains $Origin) { return $true }
    return ($Origin -match '^http://(localhost|127\.0\.0\.1)(:\d+)?$')
}

function Test-HostAllowed {
    param([string]$HostHeader)
    return ($HostHeader -match '^(localhost|127\.0\.0\.1)(:\d+)?$')
}

# Compares in constant time, so the token cannot be guessed a character at a time.
function Test-Token {
    param([string]$Given)
    if (-not $Script:Token -or -not $Given -or $Given.Length -ne $Script:Token.Length) { return $false }
    $diff = 0
    for ($i = 0; $i -lt $Given.Length; $i++) { $diff = $diff -bor ([int][char]$Given[$i] -bxor [int][char]$Script:Token[$i]) }
    return ($diff -eq 0)
}

function New-Token {
    $b = New-Object byte[] 24
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($b) } finally { $rng.Dispose() }
    return ([Convert]::ToBase64String($b)).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}


# ----------------------------------------------------------------------------
# A very small HTTP server: read one request, answer it, close.
# ----------------------------------------------------------------------------

function Find-HeaderEnd {
    param([string]$Text)
    return $Text.IndexOf("`r`n`r`n")
}

function Read-HttpRequest {
    param([System.Net.Sockets.NetworkStream]$Stream)

    $buf = New-Object byte[] 65536
    $ms = New-Object System.IO.MemoryStream
    $end = -1
    $all = $null

    while ($end -lt 0) {
        $n = $Stream.Read($buf, 0, $buf.Length)
        if ($n -le 0) { return $null }
        $ms.Write($buf, 0, $n)
        if ($ms.Length -gt 65536 + 32768) { throw 'The request headers were too large.' }
        $all = $ms.ToArray()
        $end = Find-HeaderEnd $Script:Latin1.GetString($all)
    }

    $head = $Script:Latin1.GetString($all, 0, $end)
    $lines = $head -split "`r`n"
    $first = $lines[0] -split ' '
    if ($first.Count -lt 2) { throw 'That was not an HTTP request.' }

    $headers = @{}
    for ($i = 1; $i -lt $lines.Count; $i++) {
        $c = $lines[$i].IndexOf(':')
        if ($c -gt 0) { $headers[$lines[$i].Substring(0, $c).Trim().ToLower()] = $lines[$i].Substring($c + 1).Trim() }
    }

    $path = $first[1]
    $query = @{}
    $q = $path.IndexOf('?')
    if ($q -ge 0) {
        foreach ($pair in $path.Substring($q + 1).Split('&')) {
            $e = $pair.IndexOf('=')
            if ($e -gt 0) { $query[[Uri]::UnescapeDataString($pair.Substring(0, $e))] = [Uri]::UnescapeDataString($pair.Substring($e + 1).Replace('+', ' ')) }
        }
        $path = $path.Substring(0, $q)
    }

    $len = 0L
    if ($headers.ContainsKey('content-length')) { [void][long]::TryParse($headers['content-length'], [ref]$len) }

    return @{
        Method  = $first[0].ToUpper()
        Path    = $path
        Query   = $query
        Headers = $headers
        Length  = $len
        Prefix  = $all
        Start   = $end + 4
    }
}

function Read-HttpBody {
    param([System.Net.Sockets.NetworkStream]$Stream, $Req)

    $len = [int]$Req.Length
    $body = New-Object byte[] $len
    $have = $Req.Prefix.Length - $Req.Start
    if ($have -gt $len) { $have = $len }
    if ($have -gt 0) { [Array]::Copy($Req.Prefix, $Req.Start, $body, 0, $have) }

    # A whole animation arrives in well under a second on this computer; a client that
    # dribbles it in for a minute is not given the (single) listener for longer.
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    while ($have -lt $len) {
        if ($clock.Elapsed.TotalSeconds -gt 60) { throw 'The upload was too slow.' }
        $n = $Stream.Read($body, $have, [Math]::Min(65536, $len - $have))
        if ($n -le 0) { throw 'The connection closed before the whole file arrived.' }
        $have += $n
    }
    return $body
}

function Send-Response {
    param($Stream, [int]$Status, [string]$Reason, [string]$Origin, $Data)

    $bytes = New-Object byte[] 0
    if ($null -ne $Data) { $bytes = [System.Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject $Data -Compress -Depth 6)) }

    $h = "HTTP/1.1 $Status $Reason`r`n"
    if ($bytes.Length -gt 0) { $h += "Content-Type: application/json`r`n" }
    $h += "Content-Length: $($bytes.Length)`r`n"
    $h += "Connection: close`r`n"
    if ($Origin) {
        $h += "Access-Control-Allow-Origin: $Origin`r`n"
        $h += "Vary: Origin`r`n"
        $h += "Access-Control-Allow-Methods: GET, POST, OPTIONS`r`n"
        $h += "Access-Control-Allow-Headers: Content-Type, X-Studio-Token`r`n"
        # Browsers ask permission before a public web page talks to your own computer.
        $h += "Access-Control-Allow-Private-Network: true`r`n"
        $h += "Access-Control-Max-Age: 600`r`n"
    }
    $h += "`r`n"

    $head = $Script:Latin1.GetBytes($h)
    $Stream.Write($head, 0, $head.Length)
    if ($bytes.Length -gt 0) { $Stream.Write($bytes, 0, $bytes.Length) }
    $Stream.Flush()
}


# ----------------------------------------------------------------------------
# Talking to the router
#
# With a key or a held password nothing asks anyone: ssh gets the password from a
# tiny helper program (SSH_ASKPASS) that reads it from this process's environment,
# and the router's reply comes back to us. Otherwise ssh asks in this window.
# ----------------------------------------------------------------------------

function Test-QuietLogin { return [bool]($Key -or ($null -ne $Script:Password)) }

function New-AskPass {
    $dir = Join-Path ([System.IO.Path]::GetTempPath()) ('be3600-studio-' + [guid]::NewGuid().ToString('N'))
    [void][System.IO.Directory]::CreateDirectory($dir)
    $path = Join-Path $dir 'askpass.cmd'
    # Prints the secret held in the environment. The secret itself is never written to this file.
    $body = "@echo off`r`npowershell.exe -NoProfile -NonInteractive -Command `"[Console]::Out.Write(`$env:BE3600_STUDIO_PW)`"`r`n"
    [System.IO.File]::WriteAllText($path, $body, [System.Text.Encoding]::ASCII)
    $Script:AskPassDir = $dir
    $Script:AskPassPath = $path
}

# A secret (the password, or the remembered key's passphrase) is placed in the environment
# only for the length of one ssh call and removed straight after, so nothing else this
# program starts (the browser, for one) can ever inherit it.
function Set-SecretEnv {
    param([string]$Secret)
    if (-not $Script:AskPassPath) { New-AskPass }
    [Environment]::SetEnvironmentVariable('BE3600_STUDIO_PW', $Secret, 'Process')
    [Environment]::SetEnvironmentVariable('SSH_ASKPASS', $Script:AskPassPath, 'Process')
    [Environment]::SetEnvironmentVariable('SSH_ASKPASS_REQUIRE', 'force', 'Process')
    if (-not $env:DISPLAY) { [Environment]::SetEnvironmentVariable('DISPLAY', 'studio-link', 'Process') }
}

function Clear-SecretEnv {
    foreach ($n in 'BE3600_STUDIO_PW', 'SSH_ASKPASS', 'SSH_ASKPASS_REQUIRE') { [Environment]::SetEnvironmentVariable($n, $null, 'Process') }
}

function Clear-PasswordEnv {
    Clear-SecretEnv
    try { if ($Script:AskPassDir) { [System.IO.Directory]::Delete($Script:AskPassDir, $true) } } catch {}
    $Script:AskPassDir = $null
    $Script:AskPassPath = $null
}

# One command on the router. -> @{ Code; Out }
function Invoke-Ssh {
    param([string]$Ip, [string]$Remote, [string]$InputFile)

    # Everything below goes into one cmd.exe command line, so it must be plain.
    if (-not (Test-HostName $Ip)) { throw 'The router address must be like 192.168.8.1 (letters, digits, dots and dashes only).' }
    if ($Key -and $Key -match '["&|<>^%]') { throw 'The key file path may not contain any of  " & | < > ^ %' }

    $ssh = $null
    if ($Script:Testing) { $ssh = $env:BE3600_SSH }              # test hook: a stand-in for ssh
    if (-not $ssh) { $ssh = Get-Exe 'ssh.exe' }
    if (-not $ssh) { throw 'ssh.exe was not found. Turn on "OpenSSH Client" under Settings > Apps > Optional features.' }

    $quiet = Test-QuietLogin
    $opt = ''
    $secret = $null
    if ($Key -and ($null -ne $Script:KeyPass)) {
        $opt = "-i `"$Key`" -o IdentitiesOnly=yes -o NumberOfPasswordPrompts=1 "      # ssh asks for the key's passphrase
        $secret = $Script:KeyPass
    }
    elseif ($Key) { $opt = "-i `"$Key`" -o IdentitiesOnly=yes -o BatchMode=yes " }
    elseif ($null -ne $Script:Password) {
        $opt = '-o NumberOfPasswordPrompts=1 '
        $secret = $Script:Password
    }

    $cmd = "`"$ssh`" $opt-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 root@$Ip `"$Remote`""
    if ($InputFile) { $cmd += " < `"$InputFile`"" } elseif ($quiet) { $cmd += ' < nul' }

    $outFile = $null
    if ($quiet) {
        $outFile = [System.IO.Path]::GetTempFileName()
        $cmd += " > `"$outFile`" 2>&1"
    }

    try {
        if ($null -ne $secret) { Set-SecretEnv $secret }
        $p = Start-Process -FilePath $env:ComSpec -ArgumentList "/d /s /c `"$cmd`"" -NoNewWindow -Wait -PassThru
        $text = ''
        if ($outFile) { $text = [System.IO.File]::ReadAllText($outFile) }
        return @{ Code = $p.ExitCode; Out = $text }
    }
    finally {
        if ($null -ne $secret) { Clear-SecretEnv }
        if ($outFile) { try { [System.IO.File]::Delete($outFile) } catch {} }
    }
}

function Get-LastLine {
    param([string]$Text)
    $l = @($Text -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($l.Count) { return $l[$l.Count - 1] }
    return ''
}

function ConvertFrom-PlainList {
    param([string]$Text)
    $items = New-Object System.Collections.ArrayList
    $limits = $null
    foreach ($line in ($Text -split "`r?`n")) {
        $p = $line.Split("`t")
        if (($p[0] -eq '*' -or $p[0] -eq '-') -and $p.Count -ge 4) {
            $bytes = 0L; $secs = 0.0
            if ([long]::TryParse($p[2], [ref]$bytes) -and [double]::TryParse($p[3], [System.Globalization.NumberStyles]::Float, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$secs)) {
                [void]$items.Add(@{ name = $p[1]; bytes = $bytes; seconds = $secs; active = ($p[0] -eq '*') })
            }
        }
        elseif ($p[0] -eq 'limits' -and $p.Count -ge 3) {
            $m = 0; $s = 0
            if ([int]::TryParse($p[1], [ref]$m) -and [int]::TryParse($p[2], [ref]$s)) { $limits = @{ max = $m; maxSeconds = $s } }
        }
    }
    if ($null -eq $limits) { return $null }
    return @{ Items = $items; Limits = $limits }
}

function Get-TargetRouter {
    if ($Router) { return $Router }
    if ($Script:RouterIp -and (Test-SshPort $Script:RouterIp)) { return $Script:RouterIp }

    $cands = @()
    $saved = Get-SavedRouter
    if ($saved) { $cands += $saved }
    $cands += @(Get-GatewayCandidates)
    $cands += '192.168.8.1'

    foreach ($c in ($cands | Where-Object { $_ } | Select-Object -Unique)) {
        if (Test-SshPort $c) { $Script:RouterIp = $c; return $c }
    }
    return $null
}

function New-Reply {
    param([int]$Status, [string]$Reason, $Data)
    return @{ Status = $Status; Reason = $Reason; Data = $Data }
}

function Get-Library {
    if ($DryRun) { return New-Reply 200 'OK' @{ ok = $true; dryRun = $true; max = 3; maxSeconds = 25; items = @() } }

    if (-not (Test-QuietLogin)) {
        return New-Reply 200 'OK' @{ ok = $false; needPassword = $true; message = 'Studio Link was started without your router password, so it cannot look at your animations. Close it and start it again, and type the password when it asks.' }
    }
    $ip = Get-TargetRouter
    if (-not $ip) { return New-Reply 502 'Bad Gateway' @{ ok = $false; message = 'Could not find your router.' } }

    $r = Invoke-Ssh -Ip $ip -Remote 'be3600-anim list --plain'
    if ($r.Code -eq 255 -or $r.Out -match 'Permission denied') {
        return New-Reply 502 'Bad Gateway' @{ ok = $false; message = 'Could not log in to the router. Is the password right?' }
    }
    $lib = ConvertFrom-PlainList $r.Out
    if ($null -eq $lib) {
        return New-Reply 502 'Bad Gateway' @{ ok = $false; message = 'The screen saver on the router is missing or out of date. Close Studio Link and start it again; it will offer to update it.' }
    }
    return New-Reply 200 'OK' @{ ok = $true; router = $ip; max = $lib.Limits.max; maxSeconds = $lib.Limits.maxSeconds; items = $lib.Items }
}

# be3600-anim use NAME / remove NAME
function Invoke-RouterCommand {
    param([string]$Verb, [string]$Name)

    if ($Name -notmatch '^[A-Za-z0-9._-]{1,40}$') {
        return New-Reply 400 'Bad Request' @{ ok = $false; message = 'That is not a valid animation name.' }
    }
    if ($DryRun) { return New-Reply 200 'OK' @{ ok = $true; dryRun = $true; message = 'Dry run: nothing was changed.' } }

    $ip = Get-TargetRouter
    if (-not $ip) { return New-Reply 502 'Bad Gateway' @{ ok = $false; message = 'Could not find your router.' } }

    Write-Step $Verb "$Verb '$Name'"
    $r = Invoke-Ssh -Ip $ip -Remote "be3600-anim $Verb $Name"
    if ($r.Code -eq 255 -or $r.Out -match 'Permission denied') {
        return New-Reply 502 'Bad Gateway' @{ ok = $false; message = 'Could not log in to the router.' }
    }
    $line = Get-LastLine $r.Out
    if ($r.Code -ne 0) {
        if (-not $line) { $line = 'The router refused.' }
        return New-Reply 422 'Unprocessable Entity' @{ ok = $false; message = $line }
    }
    if (-not $line) { $line = 'Done.' }
    return New-Reply 200 'OK' @{ ok = $true; message = $line }
}


function Invoke-SendChime {
    param([string]$Name, [string]$Steps)

    $Steps = ($Steps -split '\s+' | Where-Object { $_ }) -join ' '
    if ($Name -notmatch '^[A-Za-z0-9._-]{1,40}$') {
        return New-Reply 400 'Bad Request' @{ ok = $false; message = 'That is not a valid chime name.' }
    }
    if ($Steps -notmatch '^\d{1,3}:\d{1,6}( \d{1,3}:\d{1,6}){0,31}$') {
        return New-Reply 400 'Bad Request' @{ ok = $false; message = 'A chime is a list of duty:milliseconds steps, nothing else.' }
    }
    if ($DryRun) { return New-Reply 200 'OK' @{ ok = $true; dryRun = $true; message = 'Dry run: nothing was sent.' } }

    $ip = Get-TargetRouter
    if (-not $ip) { return New-Reply 502 'Bad Gateway' @{ ok = $false; message = 'Could not find your router.' } }

    Write-Step 'send' "chime '$Name' to $ip"
    # $Steps has already been checked to be digits, colons and single spaces, so it cannot escape the quotes.
    $r = Invoke-Ssh -Ip $ip -Remote "be3600-fan save $Name '$Steps'"
    if ($r.Code -eq 255 -or $r.Out -match 'Permission denied') {
        return New-Reply 502 'Bad Gateway' @{ ok = $false; message = 'Could not log in to the router.' }
    }
    if ($r.Code -ne 0) {
        $line = Get-LastLine $r.Out
        if (-not $line) { $line = 'The router refused it. It keeps at most 8 chimes of your own.' }
        return New-Reply 422 'Unprocessable Entity' @{ ok = $false; message = $line }
    }
    Write-Step 'ok' "saved '$Name' on the router"
    return New-Reply 200 'OK' @{ ok = $true; message = "Saved on the router as '$Name'. Hear it from the chimes page, or set FAN_CHIME_TAPS=$Name for five taps." }
}


# The screen pages the router can show, and which are on (Motion Studio's widget list).
function ConvertFrom-PagesList {
    param([string]$Text)
    $order = $null
    $pages = New-Object System.Collections.ArrayList
    foreach ($line in ($Text -split "`r?`n")) {
        $p = $line.Split("`t")
        if ($p[0] -eq 'order' -and $p.Count -ge 2) { $order = @($p[1] -split ' ' | Where-Object { $_ }) }
        elseif (($p[0] -eq '*' -or $p[0] -eq '-') -and $p.Count -ge 3 -and $p[1] -match '^[A-Za-z0-9._-]{1,40}$') {
            [void]$pages.Add(@{ name = $p[1]; on = ($p[0] -eq '*'); about = $p[2] })
        }
    }
    if ($null -eq $order -or $pages.Count -eq 0) { return $null }
    return @{ Order = $order; Pages = $pages }
}

function Get-Pages {
    if ($DryRun) { return New-Reply 200 'OK' @{ ok = $true; dryRun = $true; order = @('animations'); pages = @(@{ name = 'animations'; on = $true; about = 'your saved animations' }) } }
    if (-not (Test-QuietLogin)) {
        return New-Reply 200 'OK' @{ ok = $false; needPassword = $true; message = 'Studio Link was started without your router password, so it cannot look at the pages.' }
    }
    $ip = Get-TargetRouter
    if (-not $ip) { return New-Reply 502 'Bad Gateway' @{ ok = $false; message = 'Could not find your router.' } }

    $r = Invoke-Ssh -Ip $ip -Remote 'be3600-anim pages --plain'
    if ($r.Code -eq 255 -or $r.Out -match 'Permission denied') {
        return New-Reply 502 'Bad Gateway' @{ ok = $false; message = 'Could not log in to the router.' }
    }
    $list = ConvertFrom-PagesList $r.Out
    if ($null -eq $list) {
        return New-Reply 502 'Bad Gateway' @{ ok = $false; message = 'The screen saver on the router is too old to choose pages from here. Close Studio Link and start it again; it will offer to update it.' }
    }
    return New-Reply 200 'OK' @{ ok = $true; order = $list.Order; pages = $list.Pages }
}

# be3600-anim pages set "animations clock ..."
function Set-Pages {
    param([string]$Text)
    $names = ($Text -split '\s+' | Where-Object { $_ }) -join ' '
    if ($names -notmatch '^[A-Za-z0-9:._-]{1,40}( [A-Za-z0-9:._-]{1,40}){0,40}$') {
        return New-Reply 400 'Bad Request' @{ ok = $false; message = 'Choose at least one page.' }
    }
    if ($DryRun) { return New-Reply 200 'OK' @{ ok = $true; dryRun = $true; message = 'Dry run: nothing was changed.' } }

    $ip = Get-TargetRouter
    if (-not $ip) { return New-Reply 502 'Bad Gateway' @{ ok = $false; message = 'Could not find your router.' } }

    Write-Step 'pages' $names
    # $names has already been checked to be page names and single spaces, so it cannot escape the quotes.
    $r = Invoke-Ssh -Ip $ip -Remote "be3600-anim pages set '$names'"
    if ($r.Code -eq 255 -or $r.Out -match 'Permission denied') {
        return New-Reply 502 'Bad Gateway' @{ ok = $false; message = 'Could not log in to the router.' }
    }
    if ($r.Code -ne 0) {
        $line = Get-LastLine $r.Out
        if (-not $line) { $line = 'The router refused.' }
        return New-Reply 422 'Unprocessable Entity' @{ ok = $false; message = $line }
    }
    return New-Reply 200 'OK' @{ ok = $true; message = "Saved. The router's screen now shows: $names" }
}


# ----------------------------------------------------------------------------
# Sending one animation (the same steps as Set-Animation, quietly)
# ----------------------------------------------------------------------------

function Invoke-Send {
    param($Req, [byte[]]$Body)

    $name = ConvertTo-LibName ($Req.Query['name'] + '.bea')
    if (-not $Req.Query.ContainsKey('name')) { $name = 'animation' }

    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ('be3600-studio-' + [guid]::NewGuid().ToString('N') + '.bea')

    try {
        [System.IO.File]::WriteAllBytes($tmp, $Body)

        Write-Step 'got' ("'{0}' from Motion Studio ({1})" -f $name, (Format-Size $Body.Length))

        $info = Test-BeaFile $tmp
        if (-not $info.Ok) {
            Write-Bad $info.Reason
            return New-Reply 400 'Bad Request' @{ ok = $false; message = $info.Reason }
        }
        Write-Ok ('{0} frames, {1} fps, {2:N1} s per loop' -f $info.Frames, $info.Fps, $info.Seconds)

        if ($info.Seconds -gt 25) {
            $m = ('It loops for {0:N1} seconds; the limit is 25 seconds.' -f $info.Seconds)
            Write-Bad $m
            return New-Reply 400 'Bad Request' @{ ok = $false; message = $m }
        }

        $ip = Get-TargetRouter
        if (-not $ip) {
            $m = 'Could not find your router. Start Studio Link with its address, for example: Studio-Link.cmd -Router 192.168.8.1'
            Write-Bad $m
            return New-Reply 502 'Bad Gateway' @{ ok = $false; message = $m }
        }

        if ($DryRun) {
            Write-Warn 'Dry run: nothing was sent.'
            return New-Reply 200 'OK' @{ ok = $true; message = 'Dry run: the file is valid, nothing was sent.'; name = $name; seconds = [math]::Round($info.Seconds, 1); dryRun = $true }
        }

        Write-Step 'send' "Sending it to $ip"
        if (-not (Test-QuietLogin)) {
            Write-Host ''
            Write-Box @('Type your router admin password here when asked.') 'Yellow'
            Write-Host ''
            try { [Console]::Beep(880, 120) } catch {}
        }

        # A fresh private temp name on the router each time (never a fixed, guessable path).
        $remote = 'T=$(mktemp /tmp/be3600-new.XXXXXX) && cat > $T && be3600-anim set $T {0}; R=$?; rm -f $T; exit $R' -f $name
        $r = Invoke-Ssh -Ip $ip -Remote $remote -InputFile $tmp
        Write-Host ''

        if ($r.Code -eq 0) {
            Save-Router $ip
            $Script:Sent++
            Write-Box @('Done - your animation is on the router.', '', ('  ' + $name)) 'Green'
            return New-Reply 200 'OK' @{ ok = $true; message = "Sent. It is saved on the router as '$name' and playing."; name = $name; seconds = [math]::Round($info.Seconds, 1) }
        }

        if ($r.Code -eq 255 -or $r.Out -match 'Permission denied') {
            $Script:RouterIp = $null
            Write-Box @('Could not connect or log in.') 'Red'
            return New-Reply 502 'Bad Gateway' @{ ok = $false; message = 'Could not connect to the router or log in. Check the connection and the password, then try again.' }
        }

        $why = Get-LastLine $r.Out
        $m = 'The router did not accept that file. The usual reason: it already holds 3 animations. Give the file a name you already use to replace one, or remove one below.'
        if ($why) { Write-Bad $why }
        Write-Box @('The router did not accept that file.') 'Red'
        return New-Reply 422 'Unprocessable Entity' @{ ok = $false; message = $m }
    }
    finally {
        try { [System.IO.File]::Delete($tmp) } catch {}
    }
}


# ----------------------------------------------------------------------------
# Answer one connection
# ----------------------------------------------------------------------------

function Handle-Client {
    param([System.Net.Sockets.TcpClient]$Client)

    $Client.ReceiveTimeout = 15000
    $Client.SendTimeout = 15000
    $stream = $Client.GetStream()
    $origin = $null

    try {
        $req = Read-HttpRequest $stream
        if (-not $req) { return }

        $origin = $req.Headers['origin']
        $allowed = (Test-OriginAllowed $origin) -and (Test-HostAllowed $req.Headers['host'])

        if (-not $allowed) {
            Write-Warn "Refused a request from '$origin' (host '$($req.Headers['host'])')."
            Send-Response $stream 403 'Forbidden' $null @{ ok = $false; message = 'That page is not allowed to use Studio Link.' }
            return
        }

        # Only an actual browser origin gets CORS headers back.
        $cors = $origin

        if ($req.Method -eq 'OPTIONS') {
            Send-Response $stream 204 'No Content' $(if ($cors) { $cors } else { '*' }) $null
            return
        }

        $hasToken = Test-Token $req.Headers['x-studio-token']

        # Without the token the ping only says "I am Studio Link, and I need pairing": no
        # router address, nothing else. With it, the page gets the full picture.
        if ($req.Method -eq 'GET' -and $req.Path -eq '/ping') {
            if (-not $hasToken) {
                Send-Response $stream 200 'OK' $cors @{ ok = $true; app = 'be3600-studio-link'; version = 3; needToken = $true }
                return
            }
            $Script:Paired = $true
            $known = $Router
            if (-not $known) { $known = $Script:RouterIp }
            Send-Response $stream 200 'OK' $cors @{ ok = $true; app = 'be3600-studio-link'; version = 3; authed = $true; dryRun = [bool]$DryRun; sent = $Script:Sent; router = $known; loggedIn = (Test-QuietLogin); pages = $true }
            return
        }

        # Everything else needs this run's secret token.
        if (-not $hasToken) {
            Send-Response $stream 401 'Unauthorized' $cors @{ ok = $false; needToken = $true; message = 'This page has not been paired with Studio Link yet.' }
            return
        }

        if ($req.Method -eq 'GET' -and $req.Path -eq '/library') {
            $r = Get-Library
            Send-Response $stream $r.Status $r.Reason $cors $r.Data
            return
        }

        if ($req.Method -eq 'GET' -and $req.Path -eq '/pages') {
            $r = Get-Pages
            Send-Response $stream $r.Status $r.Reason $cors $r.Data
            return
        }

        if ($req.Method -eq 'POST' -and $req.Path -eq '/pages') {
            if ($req.Length -le 0 -or $req.Length -gt 2048) {
                Send-Response $stream 400 'Bad Request' $cors @{ ok = $false; message = 'Choose at least one page.' }
                return
            }
            if ($req.Headers['expect'] -eq '100-continue') {
                $c = $Script:Latin1.GetBytes("HTTP/1.1 100 Continue`r`n`r`n")
                $stream.Write($c, 0, $c.Length)
            }
            $body = Read-HttpBody $stream $req
            $r = Set-Pages ([Text.Encoding]::UTF8.GetString($body))
            Send-Response $stream $r.Status $r.Reason $cors $r.Data
            return
        }

        if ($req.Method -eq 'POST' -and ($req.Path -eq '/use' -or $req.Path -eq '/remove')) {
            $r = Invoke-RouterCommand $req.Path.Substring(1) $req.Query['name']
            Send-Response $stream $r.Status $r.Reason $cors $r.Data
            return
        }

        if ($req.Method -eq 'POST' -and $req.Path -eq '/chime') {
            if ($req.Length -le 0 -or $req.Length -gt 4096) {
                Send-Response $stream 400 'Bad Request' $cors @{ ok = $false; message = 'A chime is a short list of steps.' }
                return
            }
            if ($req.Headers['expect'] -eq '100-continue') {
                $c = $Script:Latin1.GetBytes("HTTP/1.1 100 Continue`r`n`r`n")
                $stream.Write($c, 0, $c.Length)
            }
            $body = Read-HttpBody $stream $req
            $r = Invoke-SendChime $req.Query['name'] ([Text.Encoding]::UTF8.GetString($body))
            Send-Response $stream $r.Status $r.Reason $cors $r.Data
            return
        }

        if ($req.Method -eq 'POST' -and $req.Path -eq '/send') {
            if ($req.Length -le 0) {
                Send-Response $stream 400 'Bad Request' $cors @{ ok = $false; message = 'No file was sent.' }
                return
            }
            if ($req.Length -gt $Script:MaxBody) {
                Send-Response $stream 413 'Payload Too Large' $cors @{ ok = $false; message = 'That file is far too big to be an animation.' }
                return
            }
            if ($req.Headers['expect'] -eq '100-continue') {
                $c = $Script:Latin1.GetBytes("HTTP/1.1 100 Continue`r`n`r`n")
                $stream.Write($c, 0, $c.Length)
            }

            $body = Read-HttpBody $stream $req
            $r = Invoke-Send $req $body
            Send-Response $stream $r.Status $r.Reason $cors $r.Data
            return
        }

        Send-Response $stream 404 'Not Found' $cors @{ ok = $false; message = 'Not found.' }
    }
    catch {
        # The page hangs up on its own quick status check while a router job runs; that is normal.
        if ($_.Exception.Message -notmatch 'transport connection|forcibly closed|connection was aborted|Unable to read data') {
            Write-Warn ("Request problem: " + $_.Exception.Message)
        }
        # The page gets a plain message; the details stay in this window (never an internal error text).
        try { Send-Response $stream 400 'Bad Request' $origin @{ ok = $false; message = 'Studio Link could not handle that request.' } } catch {}
    }
    finally {
        try { $stream.Close() } catch {}
        try { $Client.Close() } catch {}
    }
}


# ----------------------------------------------------------------------------
# Log in once, then go
# ----------------------------------------------------------------------------

function Test-Login {
    # Can we get in without anyone typing (a key, or the password we hold)?
    param([string]$Ip)
    $r = Invoke-Ssh -Ip $Ip -Remote 'echo studio-ok'
    return ($r.Code -eq 0 -and $r.Out -match 'studio-ok')
}

# ----------------------------------------------------------------------------
# The remembered key is locked with a passphrase, and that passphrase is locked to
# this Windows account (DPAPI). Copying the key file to another computer or another
# account gets an attacker nothing.
# ----------------------------------------------------------------------------

function Protect-Text {
    param([string]$Plain)
    $ss = ConvertTo-SecureString -String $Plain -AsPlainText -Force
    return (ConvertFrom-SecureString -SecureString $ss)
}

function Unprotect-Text {
    param([string]$Blob)
    $ss = ConvertTo-SecureString -String $Blob
    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($ss)
    try { return [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr) }
    finally { [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Get-SavedKeyPass {
    if (-not (Test-Path -LiteralPath $Script:PassPath)) { return $null }
    try {
        $blob = Get-Content -LiteralPath $Script:PassPath -TotalCount 1
        if ($blob) { return (Unprotect-Text $blob) }
    } catch {}
    return $null
}

function Save-KeyPass {
    param([string]$Phrase)
    [void][System.IO.Directory]::CreateDirectory($Script:StateDir)
    [System.IO.File]::WriteAllText($Script:PassPath, (Protect-Text $Phrase))
    Lock-ToUser $Script:PassPath
}

# Only this Windows account may read or change the file.
function Lock-ToUser {
    param([string]$Path)
    try {
        $me = ('{0}\{1}' -f $env:USERDOMAIN, $env:USERNAME)
        & icacls.exe $Path /inheritance:r /grant:r ('{0}:(F)' -f $me) | Out-Null
    } catch {}
}

function New-Phrase {
    $chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
    $b = New-Object byte[] 40
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($b) } finally { $rng.Dispose() }
    $s = New-Object System.Text.StringBuilder
    foreach ($x in $b) { [void]$s.Append($chars[$x % $chars.Length]) }
    return $s.ToString()
}

function Remove-KeyFiles {
    foreach ($p in $Script:KeyPath, ($Script:KeyPath + '.pub'), $Script:PassPath) { try { [System.IO.File]::Delete($p) } catch {} }
}

# A key remembered by an earlier version has no passphrase: lock it now, in place.
function Protect-OldKey {
    param([string]$Ip)
    $rekeyed = $false
    try {
        $keygen = Get-Exe 'ssh-keygen.exe'
        if (-not $keygen) { return }
        $phrase = New-Phrase
        Save-KeyPass $phrase                                   # stored first, so it can never be lost
        $p = Start-Process -FilePath $env:ComSpec -ArgumentList "/d /s /c `"`"$keygen`" -q -p -f `"$($Script:KeyPath)`" -P `"`" -N $phrase`"" -NoNewWindow -Wait -PassThru
        if ($p.ExitCode -ne 0) { throw 'ssh-keygen could not lock the key' }
        $rekeyed = $true
        $script:KeyPass = $phrase
        if (-not (Test-Login $Ip)) { throw 'the locked key did not work' }
        Write-Ok 'Your remembered key is now locked with a passphrase that only this Windows account can open.'
    }
    catch {
        $script:KeyPass = $null
        if (-not $rekeyed) { try { [System.IO.File]::Delete($Script:PassPath) } catch {} }
        Write-Warn ("Could not lock the remembered key ($($_.Exception.Message)); it keeps working as before.")
    }
}

# Get in: the remembered key if there is one, otherwise the password, asked for once
# (it stays in memory). -> the router's address, or $null.
function Start-Login {
    $ip = Get-TargetRouter
    if (-not $ip) {
        Write-Warn 'Could not find your router. Start Studio Link with its address, for example: Studio-Link.cmd -Router 192.168.8.1'
        return $null
    }
    Write-Ok "Router found at $ip"

    if ($Key) { Write-Ok 'Using your key file; no password needed.'; return $ip }

    if (Test-Path -LiteralPath $Script:KeyPath) {
        $script:Key = $Script:KeyPath
        $script:KeyPass = Get-SavedKeyPass                     # $null: a key from an earlier version (no passphrase)
        if (Test-Login $ip) {
            Write-Ok 'Logged in (this computer is remembered).'
            if ($null -eq $Script:KeyPass) { Protect-OldKey $ip }
            return $ip
        }
        $script:Key = $null
        $script:KeyPass = $null
        Write-Warn 'The remembered login no longer works (was the router reset?). Please type the password.'
    }

    if ([Console]::IsInputRedirected) { return $ip }

    if (Test-UnusualAddress $ip) {
        Write-Warn "$ip is not an address on a home or office network. Your password would travel to it."
        $go = ($Script:Testing -and $env:BE3600_ASSUME_YES)
        if (-not $go) { $go = ((Read-Host '      Continue anyway? [y/N]').Trim() -match '^(y|yes)$') }
        if (-not $go) { return $ip }
    }

    if (-not $Script:AskPassPath) { New-AskPass }
    for ($attempt = 0; $attempt -lt 3; $attempt++) {
        $sec = Read-Host '  Router admin password (kept in memory only; just Enter to be asked each time)' -AsSecureString
        $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
        try { $pw = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr) }
        finally { [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }

        if (-not $pw) {
            Write-Warn 'No password held: you will be asked in this window for every send, and Motion Studio cannot show your animations.'
            return $ip
        }

        $Script:Password = $pw
        if (Test-Login $ip) { Write-Ok 'Logged in.'; return $ip }

        $Script:Password = $null
        Write-Bad 'That did not work. Is it your router''s admin password?'
    }
    return $ip
}


# ----------------------------------------------------------------------------
# Putting the screen saver on the router, and remembering this computer
# ----------------------------------------------------------------------------

# The files the router needs, as a .tar.gz in the temp folder -> @{ Path; Id }, or $null.
# The single-file download carries them inside itself (after a marker line); from a clone
# of the repo they are packed on the fly.
function Get-Payload {
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ('be3600-setup-' + [guid]::NewGuid().ToString('N') + '.tar.gz')

    $self = $env:SELF
    if ($self -and (Test-Path -LiteralPath $self)) {
        $text = [System.IO.File]::ReadAllText($self)
        $i = $text.LastIndexOf('#' + '#PAYLOAD#' + '# ')
        if ($i -ge 0) {
            $lines = $text.Substring($i) -split "`r?`n"
            $id = ($lines[0] -split ' ')[1]
            if ($id -notmatch '^[0-9a-f]{12}$') { return $null }
            $b64 = (($lines | Select-Object -Skip 1) -join '') -replace '\s', ''
            [System.IO.File]::WriteAllBytes($tmp, [Convert]::FromBase64String($b64))
            return @{ Path = $tmp; Id = $id }
        }
    }

    $root = Split-Path -Parent $PSScriptRoot
    $tar = Get-Exe 'tar.exe'
    if ($tar -and (Test-Path -LiteralPath (Join-Path $root 'router'))) {
        & $tar -czf $tmp -C $root router setup animations
        if ($LASTEXITCODE -eq 0) { return @{ Path = $tmp; Id = 'dev' } }
    }
    return $null
}

function Read-YesNo {
    # Enter or Y = yes, N = no. Never asks when nobody is there to answer.
    param([string]$Question)
    if ($Script:Testing -and $env:BE3600_ASSUME_YES) { return $true }     # test hook
    if ([Console]::IsInputRedirected) { return $false }
    $a = (Read-Host "  $Question [Enter = yes, N = no]").Trim()
    return ($a -notmatch '^(n|no)$')
}

function Install-OnRouter {
    param([string]$Ip, $Payload)
    Write-Step 'install' 'Putting the screen saver on your router'
    # Unpacked in a fresh private folder on the router, and removed afterwards; the installer's own exit code is kept.
    $remote = 'D=$(mktemp -d /tmp/be3600-setup.XXXXXX) && cd $D && gunzip -c | tar xf - && BE3600_VERSION={0} sh setup/router-install.sh; R=$?; cd /; rm -rf $D; exit $R' -f $Payload.Id
    $r = Invoke-Ssh -Ip $Ip -Remote $remote -InputFile $Payload.Path
    foreach ($l in ($r.Out -split "`r?`n")) { if ($l.Trim()) { Write-Info $l.TrimEnd() } }
    if ($r.Code -eq 0) { Write-Ok 'The screen saver is installed and running.'; return }
    if ($r.Code -eq 3) {
        Write-Bad 'That device is not a GL-BE3600 with a front display (nothing was changed). Start Studio Link with the right address: Studio-Link.cmd -Router 192.168.x.x'
    } else {
        Write-Bad 'The install did not finish (see above).'
    }
}

# Install (or update) the screen saver on the router when it is missing or old.
function Confirm-Installed {
    param([string]$Ip)
    $r = Invoke-Ssh -Ip $Ip -Remote 'cat /etc/be3600-screen/version 2>/dev/null; echo; command -v be3600-anim >/dev/null && echo HAVE-ANIM; be3600-anim list --plain >/dev/null 2>&1 && echo LIST-OK'
    if ($r.Code -ne 0) { return }
    $have = ($r.Out -match 'HAVE-ANIM')
    $current = ($r.Out -match 'LIST-OK')
    $version = ''
    foreach ($l in ($r.Out -split "`r?`n")) {
        $t = $l.Trim()
        if ($t -and $t -ne 'HAVE-ANIM' -and $t -ne 'LIST-OK') { $version = $t; break }
    }

    $payload = Get-Payload
    if (-not $payload) { return }
    try {
        if ($current -and ($payload.Id -eq 'dev' -or $version -eq $payload.Id)) { return }   # already there, up to date

        if (-not $have) { $headline = 'This router does not have the screen saver yet.'; $ask = 'Put it on the router now?' }
        elseif (-not $current) { $headline = 'The screen saver on this router is an older version.'; $ask = 'Update it now?' }
        else { $headline = 'A newer version of the screen saver is available.'; $ask = 'Update it now?' }

        Write-Host ''
        Write-Host "  $headline"
        if (Read-YesNo $ask) { Install-OnRouter $Ip $payload }
        else { Write-Warn 'Skipped. Motion Studio can only show and change animations once it is installed.' }
    }
    finally {
        try { [System.IO.File]::Delete($payload.Path) } catch {}
    }
}

# After a password login: offer to keep a key so the password is never asked again.
function Offer-Remember {
    param([string]$Ip)
    if ($Key -or ($null -eq $Script:Password) -or (Test-Path -LiteralPath $Script:KeyPath)) { return }

    Write-Host ''
    Write-Box @(
        'Remember this computer?',
        '',
        'Studio Link keeps a private key on this computer, locked',
        'with a passphrase that only your Windows account can open,',
        'so you never type the password again. Anyone signed in as',
        'you can reach the router.',
        'Undo any time:  Studio-Link.cmd -Forget'
    ) 'Yellow'
    if (-not (Read-YesNo 'Remember it?')) { return }

    $problem = New-RememberedKey $Ip
    if ($null -eq $problem) { Write-Ok 'Remembered. Next time there is nothing to type.' }
    else { Write-Warn ("Could not set that up ($problem). Nothing is lost; you will just be asked for the password each time.") }
}

# Makes this computer's key (locked with a passphrase that only this Windows account can open),
# adds it to the router and switches to it. -> $null when it works, else the reason in a few words
# (and nothing is left behind: key files, passphrase and the password are as they were).
function New-RememberedKey {
    param([string]$Ip)
    try {
        $keygen = Get-Exe 'ssh-keygen.exe'
        if (-not $keygen) { throw 'ssh-keygen.exe was not found' }
        [void][System.IO.Directory]::CreateDirectory($Script:StateDir)
        Remove-KeyFiles

        $phrase = New-Phrase
        Save-KeyPass $phrase                                   # kept first, so the key can never be locked out
        $p = Start-Process -FilePath $env:ComSpec -ArgumentList "/d /s /c `"`"$keygen`" -q -t ed25519 -N $phrase -C $($Script:KeyComment) -f `"$($Script:KeyPath)`"`"" -NoNewWindow -Wait -PassThru
        if ($p.ExitCode -ne 0 -or -not (Test-Path -LiteralPath ($Script:KeyPath + '.pub'))) { throw 'could not make a key' }
        Lock-ToUser $Script:KeyPath

        $af = $Script:AuthKeys
        $remote = "mkdir -p /etc/dropbear && touch $af && chmod 600 $af && sed -i '/ $($Script:KeyComment)`$/d' $af && cat >> $af"
        $r = Invoke-Ssh -Ip $Ip -Remote $remote -InputFile ($Script:KeyPath + '.pub')
        if ($r.Code -ne 0) {
            $why = Get-LastLine $r.Out
            if (-not $why) { $why = 'the router refused the key' }
            throw $why
        }

        $pw = $Script:Password
        $Script:Password = $null
        $script:Key = $Script:KeyPath
        $script:KeyPass = $phrase
        if (Test-Login $Ip) { return $null }
        $script:Key = $null
        $script:KeyPass = $null
        $Script:Password = $pw
        throw 'the router did not accept the key'
    }
    catch {
        Remove-KeyFiles
        return $_.Exception.Message
    }
}

# Take this computer's key off the router and delete it here.
function Invoke-Forget {
    param([string]$Ip)
    if ($Ip -and (Test-QuietLogin)) {
        $r = Invoke-Ssh -Ip $Ip -Remote "sed -i '/ $($Script:KeyComment)`$/d' $($Script:AuthKeys)"
        if ($r.Code -eq 0) { Write-Ok 'This computer''s key was removed from the router.' }
        else { Write-Warn "Could not remove the key from the router ($(Get-LastLine $r.Out))." }
    }
    Remove-KeyFiles
    Write-Ok 'Forgotten. Studio Link will ask for your password again.'
}


# ----------------------------------------------------------------------------
# Install and Uninstall: the guided flow behind the one-click downloads
#
# The same steps, sentences and choices as tools/studio_link.py (--install / --uninstall);
# tests/setup_flow/ holds the exact conversations both must produce.
# ----------------------------------------------------------------------------

$Script:AddressPrompt = "Press Enter to try again, or type your router's address (like 192.168.8.1), or Q to quit: "
# No double quotes: this travels inside one quoted cmd.exe argument.
$Script:Be3600Check = 'S=$(cat /sys/class/graphics/fb0/virtual_size 2>/dev/null); [ -x /etc/init.d/gl_screen ] && [ x$S = x76,284 ] && echo be3600-yes; command -v be3600-uninstall >/dev/null 2>&1 && echo be3600-installed; true'
$Script:Answers = $null

# Test hook: answers come from a file (one per line) instead of the keyboard.
function Get-ScriptedAnswers {
    if (-not ($Script:Testing -and $env:BE3600_ANSWERS)) { return $null }
    if ($null -eq $Script:Answers) {
        $Script:Answers = New-Object System.Collections.ArrayList
        foreach ($l in [IO.File]::ReadAllLines($env:BE3600_ANSWERS)) { [void]$Script:Answers.Add($l) }
    }
    return ,$Script:Answers
}

function Test-Interactive { return (-not $Script:Testing) -and (-not [Console]::IsInputRedirected) }

function Flow-Say { param([string]$Text) if ($Text) { Write-Host ('     ' + $Text) } else { Write-Host '' } }
function Flow-Title { param([string]$Text) Write-Host ''; Write-Host ('  ' + $Text) -ForegroundColor White }
function Flow-Step { param([int]$N, [int]$Total, [string]$Text) Write-Host ''; Write-Host ("  Step $N of ${Total}: $Text") -ForegroundColor Cyan }
function Flow-End {
    param([string]$Text, [bool]$Good)
    Write-Host ''
    $c = 'Red'; if ($Good) { $c = 'Green' }
    Write-Host ('  ' + $Text) -ForegroundColor $c
}

# Shows the prompt and reads one line. Throws BE3600-QUIT when the input ends.
function Flow-Ask {
    param([string]$Prompt, [switch]$Secret)
    Write-Host ('     ' + $Prompt) -NoNewline
    $scripted = Get-ScriptedAnswers
    if ($null -ne $scripted) {
        if ($scripted.Count -eq 0) { Write-Host ''; throw 'BE3600-QUIT' }
        $a = [string]$scripted[0]
        $scripted.RemoveAt(0)
        if ($Secret) { Write-Host '' } else { Write-Host $a }
        return $a
    }
    try {
        if ($Secret) {
            $sec = Read-Host -AsSecureString
            if ($null -eq $sec) { throw 'eof' }
            $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
            try { return [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr) }
            finally { [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
        }
        $a = Read-Host
        if ($null -eq $a) { throw 'eof' }
        return $a
    } catch {
        Write-Host ''
        throw 'BE3600-QUIT'
    }
}

function Flow-Fail { param([string]$Text) throw ('BE3600-FAIL:' + $Text) }

# Double-clicked windows close by themselves; leave the result on screen.
function Flow-Close {
    if (-not (Test-Interactive)) { return }
    Write-Host ''
    Write-Host '  Press Enter to close this window.' -NoNewline
    try { [void](Read-Host) } catch {}
}

# Where to look, in order: the address that worked last time, this computer's gateway,
# and GL.iNet's factory address. Only a few well-known places are probed, never a scan.
function Get-FlowCandidates {
    $c = @()
    if ($Router) { $c += $Router }                                  # -Router first
    $saved = Get-SavedRouter
    if ($saved) { $c += $saved }
    if ($Script:Testing -and $env:BE3600_GATEWAY) {                 # test hook ("none": no gateway)
        if ($env:BE3600_GATEWAY -ne 'none') { $c += $env:BE3600_GATEWAY }
    } else {
        $c += @(Get-GatewayCandidates)
    }
    $c += '192.168.8.1'
    return @($c | Where-Object { $_ -and (Test-HostName $_) } | Select-Object -Unique)
}

function Test-RouterAnswers {
    param([string]$Ip)
    if ($Script:Testing -and $env:BE3600_REACHABLE) { return (@($env:BE3600_REACHABLE -split ',') -contains $Ip) }   # test hook ("none": nothing answers)
    return (Test-SshPort $Ip)
}

# Step 1. $Skip: addresses found not to be a GL-BE3600. -> the router's address.
function Flow-Find {
    param([string[]]$Skip)
    $look = $true
    while ($true) {
        if ($look) {
            foreach ($ip in (Get-FlowCandidates)) {
                if (($Skip -notcontains $ip) -and (Test-RouterAnswers $ip)) { Flow-Say "Found your router at $ip."; return $ip }
            }
            Flow-Say "We can't find your router. Make sure this computer is on the router's Wi-Fi (or plugged into it), then press Enter to try again."
        }
        $look = $false
        $answer = (Flow-Ask $Script:AddressPrompt).Trim()
        if ($answer -match '^(q|quit)$') { throw 'BE3600-QUIT' }
        if (-not $answer) { Flow-Say 'Trying again...'; $look = $true; continue }
        if (-not (Test-HostName $answer)) { Flow-Say "That doesn't look like a router address. It should look like 192.168.8.1."; continue }
        if (Test-UnusualAddress $answer) {
            Flow-Say "$answer isn't an address on a home or office network, and your password would be sent there."
            if ((Flow-Ask 'Use it anyway? [y/N] ').Trim() -notmatch '^(y|yes)$') { continue }
        }
        if ($Skip -contains $answer) { Flow-Say "$answer isn't a GL-BE3600 (Slate 7). Type the address of your GL-BE3600."; continue }
        if (Test-RouterAnswers $answer) { Flow-Say "Found your router at $answer."; return $answer }
        Flow-Say "Nothing answered at $answer. Check the address, and that this computer is on the router's Wi-Fi."
    }
}

# -> 'ok', 'denied' (wrong password or key) or 'unreachable'
function Test-FlowLogin {
    param([string]$Ip)
    $r = Invoke-Ssh -Ip $Ip -Remote 'echo studio-ok'
    if ($r.Code -eq 0 -and $r.Out -match 'studio-ok') { return 'ok' }
    if ($r.Out -match 'Permission denied' -or $r.Code -ne 255) { return 'denied' }
    return 'unreachable'
}

# Step 2: the saved login, or the password (3 tries). -> 'ok', 'failed' or 'unreachable'.
# $Script:Password is set afterwards only if the password was what got in.
function Flow-Login {
    param([string]$Ip)
    $Script:Password = $null
    $script:Key = $null
    $script:KeyPass = $null
    if (Test-Path -LiteralPath $Script:KeyPath) {
        $script:Key = $Script:KeyPath
        $script:KeyPass = Get-SavedKeyPass
        $r = Test-FlowLogin $Ip
        if ($r -eq 'ok') { Flow-Say 'Using the login saved on this computer.'; return 'ok' }
        $script:Key = $null
        $script:KeyPass = $null
        if ($r -eq 'unreachable') { return 'unreachable' }
        Flow-Say "The saved login doesn't work any more (maybe the router was reset). Please type the password instead."
    }
    if (-not $Script:AskPassPath) { New-AskPass }
    Flow-Say "Type your router's admin password (the one for its admin web page) and press Enter."
    Flow-Say "Nothing will show while you type - that's normal."
    $tries = 3
    while ($tries -gt 0) {
        $pw = Flow-Ask 'Password: ' -Secret
        if (-not $pw) { Flow-Say 'Nothing was typed. Type the password, then press Enter.'; continue }
        $Script:Password = $pw
        $r = Test-FlowLogin $Ip
        if ($r -eq 'ok') { Flow-Say 'Logged in.'; return 'ok' }
        $Script:Password = $null
        if ($r -eq 'unreachable') { return 'unreachable' }
        $tries--
        if ($tries -eq 2) { Flow-Say "That password didn't work. Please try again (2 tries left)." }
        elseif ($tries -eq 1) { Flow-Say "That password didn't work. Please try again (1 try left)." }
    }
    return 'failed'
}

# After a password login: keep a key (never the password) so it is not asked again.
function Flow-SaveLogin {
    param([string]$Ip)
    Write-Host ''
    $a = (Flow-Ask "Save this login so you don't have to type the password next time? [Y/n] ").Trim()
    if ($a -match '^(n|no)$') { Flow-Say "OK. You'll type the password next time."; return }
    $problem = New-RememberedKey $Ip
    if ($null -eq $problem) { Flow-Say "Saved. Next time you won't need to type the password." }
    else { Flow-Say "The login couldn't be saved this time ($problem). That's fine - you'll type the password next time." }
}

# -> @{ Be3600; Installed }, or $null if the router stopped answering.
function Get-RouterFacts {
    param([string]$Ip)
    $r = Invoke-Ssh -Ip $Ip -Remote $Script:Be3600Check
    if ($r.Code -ne 0) { return $null }
    return @{ Be3600 = ($r.Out -match 'be3600-yes'); Installed = ($r.Out -match 'be3600-installed') }
}

function Say-NotBe3600 {
    param([string]$Ip)
    Flow-Say "The device at $Ip isn't a GL-BE3600 (Slate 7) - it may be your internet provider's modem. Nothing was changed on it."
}

# Steps 1 and 2, until the address is a GL-BE3600 we are logged in to; then, after a password
# login, the offer to save it (only ever to a GL-BE3600). -> @{ Ip; Installed }
function Flow-Connect {
    param([bool]$OfferSave, [int]$Total)
    $skip = @()
    while ($true) {
        Flow-Step 1 $Total 'Finding your router'
        $ip = Flow-Find $skip
        Flow-Step 2 $Total 'Logging in to your router'
        $result = Flow-Login $ip
        if ($result -eq 'failed') {
            Flow-Fail "That password didn't work 3 times. Check it's the password for the router's admin web page, then run this again."
        }
        $facts = $null
        if ($result -eq 'ok') { $facts = Get-RouterFacts $ip }
        if ($null -eq $facts) {
            Flow-Fail "Your router stopped answering. Check this computer is still on its Wi-Fi, then run this again."
        }
        if (-not $facts.Be3600) {
            Say-NotBe3600 $ip
            $skip += $ip
            $Script:Password = $null
            $script:Key = $null
            $script:KeyPass = $null
            continue
        }
        if ($OfferSave -and ($null -ne $Script:Password)) { Flow-SaveLogin $ip }
        return @{ Ip = $ip; Installed = $facts.Installed }
    }
}

# Runs the body, turning BE3600-QUIT / BE3600-FAIL into the closing sentence. -> exit code
function Invoke-Flow {
    param([scriptblock]$Body, [scriptblock]$OnQuit)
    $code = 1
    try {
        & $Body | Out-Null
        $code = 0
    } catch {
        $m = [string]$_.Exception.Message
        if ($m -eq 'BE3600-QUIT') { & $OnQuit }
        elseif ($m.StartsWith('BE3600-FAIL:')) { Flow-End $m.Substring(12) $false }
        else { Flow-End ("Something went wrong: $m. Run this again.") $false }
    } finally {
        $Script:Password = $null
        Clear-PasswordEnv
    }
    Flow-Close
    return $code
}

function Invoke-InstallFlow {
    Flow-Title 'GL.iNet Router Screen Saver - Install'
    Flow-Say 'This puts the animated screen saver on your GL.iNet GL-BE3600 (Slate 7).'
    return (Invoke-Flow -Body {
        $c = Flow-Connect $true 3
        $ip = $c.Ip
        Flow-Step 3 3 'Installing the screen saver'
        Flow-Say 'This takes about a minute. Please keep this window open.'
        $payload = Get-Payload
        if (-not $payload) { Flow-Fail "This file is missing the screen saver's files. Download it again, then run it." }
        try {
            $remote = 'D=$(mktemp -d /tmp/be3600-setup.XXXXXX) && cd $D && gunzip -c | tar xf - && BE3600_VERSION={0} sh setup/router-install.sh; R=$?; cd /; rm -rf $D; exit $R' -f $payload.Id
            $r = Invoke-Ssh -Ip $ip -Remote $remote -InputFile $payload.Path
        } finally {
            try { [System.IO.File]::Delete($payload.Path) } catch {}
        }
        if ($r.Code -eq 3) {
            Flow-Fail "The device at $ip isn't a GL-BE3600 (Slate 7), so nothing was installed. Run this again and type your GL-BE3600's address."
        }
        if ($r.Code -ne 0) {
            $why = ((Get-LastLine $r.Out) -replace 'ERROR:', '').Trim().TrimEnd('.')
            if (-not $why) { $why = 'no reason given' }
            Flow-Fail "The install didn't finish on the router: $why. Run this again; if it keeps failing, restart the router and try once more."
        }
        Flow-Say 'Installed.'
        Save-Router $ip
        Flow-End "All done! Your router's screen will show the animation after a few idle seconds." $true
    } -OnQuit {
        Flow-End "Nothing was changed. Run this again when this computer is on the router's Wi-Fi." $false
    })
}

# The saved router address, the saved login's key files and its DPAPI-locked passphrase.
function Remove-ThisComputer {
    Remove-KeyFiles
    try { [System.IO.File]::Delete($Script:StateFile) } catch {}
    try {
        if ((Test-Path -LiteralPath $Script:StateDir) -and -not @(Get-ChildItem -LiteralPath $Script:StateDir -Force).Count) {
            [System.IO.Directory]::Delete($Script:StateDir)
        }
    } catch {}
}

function Invoke-UninstallFlow {
    Flow-Title 'GL.iNet Router Screen Saver - Uninstall'
    Flow-Say "This removes the screen saver and puts your router's normal screen back."
    $Script:FlowIp = $null
    return (Invoke-Flow -Body {
        $c = Flow-Connect $false 3
        $ip = $c.Ip
        $Script:FlowIp = $ip
        Flow-Step 3 3 'Removing the screen saver'
        if ($c.Installed) {
            $r = Invoke-Ssh -Ip $ip -Remote '/usr/sbin/be3600-uninstall --purge --forget-keys'
            if ($r.Code -ne 0) {
                $why = ((Get-LastLine $r.Out) -replace 'ERROR:', '').Trim().TrimEnd('.')
                if (-not $why) { $why = 'no reason given' }
                Flow-Fail "The router couldn't remove it: $why. Run this again, or remove it on the router itself: connect with ssh root@$ip, then type be3600-uninstall --purge"
            }
            Flow-Say "Removed it from the router. GL.iNet's normal screen is back."
        } else {
            [void](Invoke-Ssh -Ip $ip -Remote "sed -i '/ be3600-studio-link-[A-Za-z0-9_-]*`$/d' $($Script:AuthKeys) 2>/dev/null; true")
            Flow-Say "The screen saver wasn't on this router, so there was nothing to remove there."
        }
        Remove-ThisComputer
        Flow-Say 'Removed the saved login and router address from this computer.'
        Flow-End 'Your router is back to normal. You can delete this file.' $true
    } -OnQuit {
        $at = $Script:FlowIp
        if (-not $at) { $at = '192.168.8.1' }
        Flow-End "We couldn't reach your router, so nothing was changed." $false
        Flow-Say "To remove it on the router itself: connect with ssh root@$at, log in with the router's admin password,"
        Flow-Say 'then type: be3600-uninstall --purge'
    })
}

# ----------------------------------------------------------------------------
# Go
# ----------------------------------------------------------------------------

if ($Install -or $Uninstall) {
    # The single-file download unpacks this script to a temp file (SELF is set by it); remove it now.
    if ($env:SELF -and $MyInvocation.MyCommand.Path) { try { [System.IO.File]::Delete($MyInvocation.MyCommand.Path) } catch {} }
    try { $Host.UI.RawUI.WindowTitle = 'GL.iNet Router Screen Saver' } catch {}
    if ($Install) { $code = Invoke-InstallFlow } else { $code = Invoke-UninstallFlow }
    exit ([int](@($code)[-1]))
}

Show-Banner 'GL.iNet Router Screen Saver (BE3600)' 'Studio Link'

# The single-file download unpacks this script to a temp file (SELF is set by it); remove
# that file as soon as it is running, so nothing is left behind, even after Ctrl+C.
if ($env:SELF -and $MyInvocation.MyCommand.Path) { try { [System.IO.File]::Delete($MyInvocation.MyCommand.Path) } catch {} }

$Script:Token = New-Token

if ($Forget) {
    try { Invoke-Forget (Start-Login) }
    finally { $Script:Password = $null; Clear-PasswordEnv }
    exit 0
}

$listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
try {
    $listener.Start()
}
catch {
    Write-Bad "Could not start listening on port $Port (is Studio Link already running?)."
    exit 1
}

try {
    if ($DryRun) {
        Write-Warn 'DRY RUN: files are checked but nothing is sent.'
        Write-Warn ('To pair a page by hand, open: ' + $Script:StudioUrl + '#link=' + $Script:Token)
    }
    else {
        $ip = Start-Login
        if ($ip -and (Test-QuietLogin)) {
            Confirm-Installed $ip
            Offer-Remember $ip
        }
    }

    Write-Host ''
    Write-Box @(
        'Studio Link is running.',
        '',
        'Leave this window open.',
        'Motion Studio connects to it by itself.',
        '',
        ('Listening on this computer only: 127.0.0.1:{0}' -f $Port),
        'Press Ctrl+C to stop.'
    ) 'Green'

    if ($Script:Testing) { Write-Warn ('TEST pairing link: ' + $Script:StudioUrl + '#link=' + $Script:Token) }

    $openAt = (Get-Date).AddSeconds(6)
    $opened = ($NoBrowser -or $DryRun -or [bool]$env:BE3600_NO_BROWSER)

    while ($true) {
        # Poll instead of blocking so Ctrl+C always works.
        while (-not $listener.Pending()) {
            Start-Sleep -Milliseconds 100
            if (-not $opened -and (Get-Date) -gt $openAt) {
                $opened = $true
                if (-not $Script:Paired) {          # no page has paired yet: open one, carrying the token
                    Write-Ok 'Opening Motion Studio in your browser'
                    try { Start-Process ($Script:StudioUrl + '#link=' + $Script:Token) } catch {}
                }
            }
        }
        Handle-Client ($listener.AcceptTcpClient())
    }
}
finally {
    $listener.Stop()
    $Script:Password = $null
    Clear-PasswordEnv
}
