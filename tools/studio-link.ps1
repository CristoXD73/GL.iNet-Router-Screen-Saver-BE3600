# Studio Link: lets Motion Studio (in your browser) send an animation straight
# to your router. Start it with Studio-Link.cmd and leave the window open.
#
# It listens on THIS computer only (127.0.0.1), so nothing else on your network
# can reach it. Motion Studio's drop zone posts a .bea file to it, and it does
# exactly what Set-Animation does: check the file, then send it to the router over
# SSH. You type your router admin password in THIS window; the web page never
# sees it.
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
    if ($null -ne $Data) { $bytes = [System.Text.Encoding]::UTF8.GetBytes(($Data | ConvertTo-Json -Compress)) }

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
# Sending one animation (the same steps as Set-Animation, quietly)
# ----------------------------------------------------------------------------

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
            return @{ Status = 400; Reason = 'Bad Request'; Data = @{ ok = $false; message = $info.Reason } }
        }
        Write-Ok ('{0} frames, {1} fps, {2:N1} s per loop' -f $info.Frames, $info.Fps, $info.Seconds)

        if ($info.Seconds -gt 25) {
            $m = ('It loops for {0:N1} seconds; the limit is 25 seconds.' -f $info.Seconds)
            Write-Bad $m
            return @{ Status = 400; Reason = 'Bad Request'; Data = @{ ok = $false; message = $m } }
        }

        $ip = Get-TargetRouter
        if (-not $ip) {
            $m = 'Could not find your router. Start Studio-Link with its address, for example: Studio-Link.cmd -Router 192.168.8.1'
            Write-Bad $m
            return @{ Status = 502; Reason = 'Bad Gateway'; Data = @{ ok = $false; message = $m } }
        }

        if ($DryRun) {
            Write-Warn 'Dry run: nothing was sent.'
            return @{ Status = 200; Reason = 'OK'; Data = @{ ok = $true; message = 'Dry run: the file is valid, nothing was sent.'; name = $name; seconds = [math]::Round($info.Seconds, 1); dryRun = $true } }
        }

        Write-Step 'send' "Sending it to $ip"
        if (-not $Key) {
            Write-Host ''
            Write-Box @('Type your router admin password here when asked.', '(Nothing shows while you type - that is normal.)') 'Yellow'
            Write-Host ''
            try { [Console]::Beep(880, 120) } catch {}
        }

        $remote = "cat > /tmp/be3600-new.bea && be3600-anim set /tmp/be3600-new.bea $name && rm -f /tmp/be3600-new.bea"
        $code = Invoke-RouterSsh -Router $ip -Remote $remote -InputFile $tmp -Key $Key
        Write-Host ''

        if ($code -eq 0) {
            Save-Router $ip
            $Script:Sent++
            Write-Box @('Done - your animation is on the router.', '', ('  ' + $name)) 'Green'
            return @{ Status = 200; Reason = 'OK'; Data = @{ ok = $true; message = "Sent. It is saved on the router as '$name' and playing."; name = $name; seconds = [math]::Round($info.Seconds, 1) } }
        }

        if ($code -eq 255) {
            $Script:RouterIp = $null
            $m = 'Could not connect to the router or log in. Check the connection and the password, then try again.'
            Write-Box @('Could not connect or log in.') 'Red'
            return @{ Status = 502; Reason = 'Bad Gateway'; Data = @{ ok = $false; message = $m } }
        }

        $m = 'The router did not accept that file. The usual reason: it already holds 3 animations. Give the file a name you already use to replace one, or remove one with "be3600-anim remove NAME". The router''s own message is in the Studio Link window.'
        Write-Box @('The router did not accept that file (see the message above).') 'Red'
        return @{ Status = 422; Reason = 'Unprocessable Entity'; Data = @{ ok = $false; message = $m } }
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
            Send-Response $stream 200 'OK' $cors @{ ok = $true; app = 'be3600-studio-link'; version = 1; dryRun = [bool]$DryRun; sent = $Script:Sent }
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
        Write-Warn ("Request problem: " + $_.Exception.Message)
        try { Send-Response $stream 400 'Bad Request' $origin @{ ok = $false; message = $_.Exception.Message } } catch {}
    }
    finally {
        try { $stream.Close() } catch {}
        try { $Client.Close() } catch {}
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

Write-Box @(
    'Studio Link is running.',
    '',
    'Leave this window open, then use the drop zone in',
    'Motion Studio to send animations to your router.',
    '',
    ('Listening on this computer only: 127.0.0.1:{0}' -f $Port),
    'Press Ctrl+C to stop.'
) 'Green'
if ($DryRun) { Write-Warn 'DRY RUN: files are checked but nothing is sent.' }

try {
    while ($true) {
        # Poll instead of blocking so Ctrl+C always works.
        while (-not $listener.Pending()) { Start-Sleep -Milliseconds 100 }
        Handle-Client ($listener.AcceptTcpClient())
    }
}
finally {
    $listener.Stop()
}
