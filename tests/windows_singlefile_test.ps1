# Runs the REAL single-file download (studio/downloads/Studio-Link.cmd) against a fake ssh and checks
# pairing, refusals, and the exact command sent to the router.
# Run:  powershell -File tests\windows_singlefile_test.ps1   (Windows only; CI runs it)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$w = Join-Path $env:TEMP ('be3600-wintest-cmd-' + [guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($w)
$fails = 0
function Check($ok, $name) { if ($ok) { "  ok    $name" } else { "  FAIL  $name"; $script:fails++ } }

# a fake ssh that records how it was called and answers like a router
[IO.File]::WriteAllText("$w\fake-ssh.cmd", "@echo off`r`necho %*> `"$w\args.txt`"`r`nmore > `"$w\stdin.bin`"`r`necho studio-ok`r`nexit /b 0`r`n", [Text.Encoding]::ASCII)
[IO.File]::WriteAllText("$w\dummy-key", "not a real key", [Text.Encoding]::ASCII)

$env:BE3600_TESTING = '1'
$env:BE3600_SSH = "$w\fake-ssh.cmd"
$env:BE3600_NO_BROWSER = '1'
$cmdFile = "$repo\studio\downloads\Studio-Link.cmd"
$port = 8796

$p = Start-Process -FilePath cmd.exe -ArgumentList '/c', "`"$cmdFile`"", '-Port', "$port", '-Router', '127.0.0.1', '-Key', "$w\dummy-key" `
    -RedirectStandardOutput "$w\out.txt" -RedirectStandardError "$w\err.txt" -WindowStyle Hidden -PassThru

$token = $null
for ($i = 0; $i -lt 40 -and -not $token; $i++) {
    Start-Sleep -Milliseconds 500
    if (Test-Path "$w\out.txt") { $m = [regex]::Match((Get-Content "$w\out.txt" -Raw), '#link=([A-Za-z0-9_-]+)'); if ($m.Success) { $token = $m.Groups[1].Value } }
}
Check ($token -and $token.Length -ge 30) 'the single-file download starts and prints a pairing link with a long secret token'
if (-not $token) { Get-Content "$w\out.txt", "$w\err.txt" -ErrorAction SilentlyContinue; Stop-Process -Id $p.Id -Force; exit 1 }

function Call($method, $path, $hdr, $body) {
    $h = @{ 'Origin' = 'https://cristoxd73.github.io' }
    foreach ($k in $hdr.Keys) { $h[$k] = $hdr[$k] }
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port$path" -Method $method -Headers $h -Body $body -ContentType 'application/octet-stream' -TimeoutSec 20
        return @{ Status = [int]$r.StatusCode; Body = $r.Content }
    } catch {
        $resp = $_.Exception.Response
        if ($resp) { $sr = New-Object IO.StreamReader($resp.GetResponseStream()); return @{ Status = [int]$resp.StatusCode; Body = $sr.ReadToEnd() } }
        return @{ Status = 0; Body = $_.Exception.Message }
    }
}

$x = Call 'GET' '/ping' @{} $null
Check (($x.Status -eq 200) -and ($x.Body -match 'needToken') -and ($x.Body -notmatch 'router')) 'ping without the token only asks for pairing (no router address)'
$x = Call 'GET' '/ping' @{ 'X-Studio-Token' = $token } $null
Check (($x.Status -eq 200) -and ($x.Body -match '"authed":true') -and ($x.Body -match '"version":3')) 'ping with the token is the full picture'
$x = Call 'GET' '/library' @{} $null;                     Check ($x.Status -eq 401) 'library without the token: 401'
$x = Call 'POST' '/remove?name=default' @{} $null;        Check ($x.Status -eq 401) 'remove without the token: 401'
$x = Call 'GET' '/library' @{ 'Origin' = 'null'; 'X-Studio-Token' = $token } $null
Check ($x.Status -eq 403) 'a sandboxed page (Origin: null) is refused even with the token'
$x = Call 'GET' '/library' @{ 'Origin' = 'https://evil.example'; 'X-Studio-Token' = $token } $null
Check ($x.Status -eq 403) 'another website is refused even with the token'

$bea = New-Object byte[] (12 + 2 + 43168)
[Text.Encoding]::ASCII.GetBytes('BEA1').CopyTo($bea, 0); $bea[4] = 8; $bea[6] = 1; [BitConverter]::GetBytes([uint32]43168).CopyTo($bea, 8); $bea[12] = 8
$x = Call 'POST' '/send?name=Sunset%20Test' @{ 'X-Studio-Token' = $token } $bea
Check (($x.Status -eq 200) -and ($x.Body -match 'Sunset-Test')) 'a valid animation is accepted and named safely'
$args1 = Get-Content "$w\args.txt" -Raw
Check ($args1 -match [regex]::Escape('T=$(mktemp /tmp/be3600-new.XXXXXX) && cat > $T && be3600-anim set $T Sunset-Test; R=$?; rm -f $T; exit $R')) 'the router is told to use a fresh private temp file (the exact command survives cmd.exe quoting)'
Check ($args1 -match 'IdentitiesOnly=yes') 'ssh is told to use only the given key'

Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'be3600-studio-link-' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 1
$left = @(Get-ChildItem $env:TEMP -Filter 'be3600-studio-link-*.ps1' -ErrorAction SilentlyContinue).Count
Check ($left -eq 0) 'the unpacked script deleted itself: nothing left in TEMP'
try { [IO.Directory]::Delete($w, $true) } catch {}

""
if ($fails) { "$fails single-file test(s) FAILED."; exit 1 } else { 'All single-file download tests passed.' }
