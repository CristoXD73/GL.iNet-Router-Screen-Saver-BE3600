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
#
# Only pages served from the Motion Studio site, a local file, or localhost are
# accepted. To allow another site (for example your own fork's GitHub Pages address),
# set STUDIO_LINK_ORIGINS to a comma-separated list before starting it.

param(
    [int]$Port = 8791,
    [string]$Router,
    [string]$Key,
    [switch]$DryRun
)

. "$PSScriptRoot\lib.ps1"

$Script:MaxBody = 32MB
$Script:Latin1 = [System.Text.Encoding]::GetEncoding(28591)
$Script:AllowedOrigins = @('https://cristoxd73.github.io', 'null')
if ($env:STUDIO_LINK_ORIGINS) {
    $Script:AllowedOrigins += @($env:STUDIO_LINK_ORIGINS -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}
$Script:RouterIp = $null
$Script:Sent = 0
$Script:Password = $null
$Script:AskPassDir = $null
$Script:AskPassPath = $null


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

    while ($have -lt $len) {
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
        $h += "Access-Control-Allow-Headers: Content-Type`r`n"
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
    # Prints the password held in the environment. The password itself is never written to this file.
    $body = "@echo off`r`npowershell.exe -NoProfile -NonInteractive -Command `"[Console]::Out.Write(`$env:BE3600_STUDIO_PW)`"`r`n"
    [System.IO.File]::WriteAllText($path, $body, [System.Text.Encoding]::ASCII)
    $Script:AskPassDir = $dir
    $Script:AskPassPath = $path
}

function Set-PasswordEnv {
    param([string]$Pw)
    [Environment]::SetEnvironmentVariable('BE3600_STUDIO_PW', $Pw, 'Process')
    [Environment]::SetEnvironmentVariable('SSH_ASKPASS', $Script:AskPassPath, 'Process')
    [Environment]::SetEnvironmentVariable('SSH_ASKPASS_REQUIRE', 'force', 'Process')
    if (-not $env:DISPLAY) { [Environment]::SetEnvironmentVariable('DISPLAY', 'studio-link', 'Process') }
}

function Clear-PasswordEnv {
    foreach ($n in 'BE3600_STUDIO_PW', 'SSH_ASKPASS', 'SSH_ASKPASS_REQUIRE') { [Environment]::SetEnvironmentVariable($n, $null, 'Process') }
    try { if ($Script:AskPassDir) { [System.IO.Directory]::Delete($Script:AskPassDir, $true) } } catch {}
}

# One command on the router. -> @{ Code; Out }
function Invoke-Ssh {
    param([string]$Ip, [string]$Remote, [string]$InputFile)

    $ssh = $env:BE3600_SSH                      # test hook: a stand-in for ssh
    if (-not $ssh) { $ssh = Get-Exe 'ssh.exe' }
    if (-not $ssh) { throw 'ssh.exe was not found. Turn on "OpenSSH Client" under Settings > Apps > Optional features.' }

    $quiet = Test-QuietLogin
    $opt = ''
    if ($Key) { $opt = "-i `"$Key`" -o BatchMode=yes " }
    elseif ($null -ne $Script:Password) { $opt = '-o NumberOfPasswordPrompts=1 ' }

    $cmd = "`"$ssh`" $opt-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 root@$Ip `"$Remote`""
    if ($InputFile) { $cmd += " < `"$InputFile`"" } elseif ($quiet) { $cmd += ' < nul' }

    $outFile = $null
    if ($quiet) {
        $outFile = [System.IO.Path]::GetTempFileName()
        $cmd += " > `"$outFile`" 2>&1"
    }

    try {
        $p = Start-Process -FilePath $env:ComSpec -ArgumentList "/d /s /c `"$cmd`"" -NoNewWindow -Wait -PassThru
        $text = ''
        if ($outFile) { $text = [System.IO.File]::ReadAllText($outFile) }
        return @{ Code = $p.ExitCode; Out = $text }
    }
    finally {
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
        return New-Reply 502 'Bad Gateway' @{ ok = $false; message = "The router's software is older than this Studio Link. Run Install.cmd (or ./install.sh) again to update it." }
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
            Write-Box @('Type your router admin password here when asked.', '(Nothing shows while you type - that is normal.)') 'Yellow'
            Write-Host ''
            try { [Console]::Beep(880, 120) } catch {}
        }

        $remote = "cat > /tmp/be3600-new.bea && be3600-anim set /tmp/be3600-new.bea $name && rm -f /tmp/be3600-new.bea"
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
        return New-Reply 422 'Unprocessable Entity' @{ ok = $false; message = $m; detail = $r.Out.Trim() }
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

    $Client.ReceiveTimeout = 30000
    $Client.SendTimeout = 30000
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

        if ($req.Method -eq 'GET' -and $req.Path -eq '/ping') {
            $known = $Router
            if (-not $known) { $known = $Script:RouterIp }
            Send-Response $stream 200 'OK' $cors @{ ok = $true; app = 'be3600-studio-link'; version = 2; dryRun = [bool]$DryRun; sent = $Script:Sent; router = $known; loggedIn = (Test-QuietLogin) }
            return
        }

        if ($req.Method -eq 'GET' -and $req.Path -eq '/library') {
            $r = Get-Library
            Send-Response $stream $r.Status $r.Reason $cors $r.Data
            return
        }

        if ($req.Method -eq 'POST' -and ($req.Path -eq '/use' -or $req.Path -eq '/remove')) {
            $r = Invoke-RouterCommand $req.Path.Substring(1) $req.Query['name']
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
        try { Send-Response $stream 400 'Bad Request' $origin @{ ok = $false; message = $_.Exception.Message } } catch {}
    }
    finally {
        try { $stream.Close() } catch {}
        try { $Client.Close() } catch {}
    }
}


# ----------------------------------------------------------------------------
# Log in once, then go
# ----------------------------------------------------------------------------

function Start-Login {
    $ip = Get-TargetRouter
    if (-not $ip) {
        Write-Warn 'Could not find your router. Start Studio Link with its address, for example: Studio-Link.cmd -Router 192.168.8.1'
        return
    }
    Write-Ok "Router found at $ip"

    if ($Key) { Write-Ok 'Using your key file; no password needed.'; return }
    if ([Console]::IsInputRedirected) { return }

    New-AskPass
    for ($attempt = 0; $attempt -lt 3; $attempt++) {
        $sec = Read-Host '  Router admin password (kept in memory only; just Enter to be asked each time)' -AsSecureString
        $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
        try { $pw = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr) }
        finally { [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }

        if (-not $pw) {
            Write-Warn 'No password held: you will be asked in this window for every send, and Motion Studio cannot show your animations.'
            return
        }

        $Script:Password = $pw
        Set-PasswordEnv $pw
        $r = Invoke-Ssh -Ip $ip -Remote 'be3600-anim list --plain'
        if ($r.Code -eq 0 -and $null -ne (ConvertFrom-PlainList $r.Out)) { Write-Ok 'Logged in.'; return }
        if ($r.Code -eq 0) { Write-Warn "Logged in, but the router's software is older than this Studio Link; run Install.cmd again."; return }

        $Script:Password = $null
        Clear-PasswordEnv
        New-AskPass
        $why = Get-LastLine $r.Out
        if (-not $why) { $why = 'no reply' }
        Write-Bad "That did not work ($why)."
    }
}


# ----------------------------------------------------------------------------
# Go
# ----------------------------------------------------------------------------

Show-Banner 'GL.iNet Router Screen Saver (BE3600)' 'Studio Link'

$listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
try {
    $listener.Start()
}
catch {
    Write-Bad "Could not start listening on port $Port (is Studio Link already running?)."
    exit 1
}

try {
    if ($DryRun) { Write-Warn 'DRY RUN: files are checked but nothing is sent.' } else { Start-Login }

    Write-Host ''
    Write-Box @(
        'Studio Link is running.',
        '',
        'Leave this window open, then use Motion Studio''s',
        'drop zone to send animations to your router.',
        '',
        ('Listening on this computer only: 127.0.0.1:{0}' -f $Port),
        'Press Ctrl+C to stop.'
    ) 'Green'

    while ($true) {
        # Poll instead of blocking so Ctrl+C always works.
        while (-not $listener.Pending()) { Start-Sleep -Milliseconds 100 }
        Handle-Client ($listener.AcceptTcpClient())
    }
}
finally {
    $listener.Stop()
    $Script:Password = $null
    Clear-PasswordEnv
}
