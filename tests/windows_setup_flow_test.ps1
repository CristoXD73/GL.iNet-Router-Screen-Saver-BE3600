# The Install and Uninstall conversations on Windows, word for word: tools/studio-link.ps1 (and the
# built Install-Screen-Saver.cmd / Uninstall-Screen-Saver.cmd) must print exactly what
# tests/setup_flow/*/expected.N say, the same files tools/studio_link.py is held to on macOS and Linux.
# The routers are fakes (tests/fake_router.py) standing in for ssh.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File tests/windows_setup_flow_test.ps1             (tools\studio-link.ps1)
#   powershell -NoProfile -ExecutionPolicy Bypass -File tests/windows_setup_flow_test.ps1 -Downloads  (the built .cmd files)
param([switch]$Downloads)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$flows = Join-Path $repo 'tests\setup_flow'
$python = (Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1).Source
if (-not $python) { $python = (Get-Command python3 -ErrorAction SilentlyContinue | Select-Object -First 1).Source }
if (-not $python) { 'python was not found (the fake routers are written in it)'; exit 1 }
$powershell = (Get-Process -Id $PID).Path
$fails = 0

function Get-Normalized {
    param([string]$Text)
    $lines = @(($Text -replace "`r`n", "`n") -split "`n" | ForEach-Object { $_.TrimEnd() })
    return (($lines -join "`n").Trim("`n") + "`n")
}

function Read-Scenario {
    param([string]$Path)
    $conf = @{}
    foreach ($line in [IO.File]::ReadAllLines($Path)) {
        if (-not $line.Trim() -or $line.TrimStart().StartsWith('#')) { continue }
        $i = $line.IndexOf(':')
        if ($i -lt 0) { continue }
        $k = $line.Substring(0, $i).Trim()
        if (-not $conf.ContainsKey($k)) { $conf[$k] = $line.Substring($i + 1).Trim() }
    }
    return $conf
}

foreach ($dir in (Get-ChildItem -LiteralPath $flows -Directory | Sort-Object Name)) {
    $name = $dir.Name
    $conf = Read-Scenario (Join-Path $dir.FullName 'scenario')
    if ($conf['only'] -eq 'python') { continue }

    $work = Join-Path ([IO.Path]::GetTempPath()) ('be3600-winflow-' + [guid]::NewGuid().ToString('N'))
    [void][IO.Directory]::CreateDirectory($work)
    & $python (Join-Path $repo 'tests\setup_flow_test.py') --world $name $work
    $fake = Join-Path $work 'fake-ssh.cmd'
    [IO.File]::WriteAllText($fake, "@`"$python`" `"$repo\tests\fake_router.py`" %*`r`n", [Text.Encoding]::ASCII)

    $n = 0
    foreach ($action in ($conf['runs'] -split '\s+' | Where-Object { $_ })) {
        $n++
        $answers = Join-Path $dir.FullName "answers.$n"
        if (-not (Test-Path -LiteralPath $answers)) { $answers = Join-Path $work 'no-answers'; [IO.File]::WriteAllText($answers, '') }
        # An empty variable does not exist in PowerShell, so "none" says "nothing" to the test hooks.
        $reach = 'none'
        if ($conf['reachable']) { $reach = (($conf['reachable'] -split '\s+' | Where-Object { $_ }) -join ',') }

        $env:BE3600_TESTING = '1'
        $env:BE3600_SSH = $fake
        $env:FAKE_ROUTER_DIR = Join-Path $work 'routers'
        $env:BE3600_REACHABLE = $reach
        $env:BE3600_GATEWAY = 'none'
        if ($conf['gateway']) { $env:BE3600_GATEWAY = $conf['gateway'] }
        $env:BE3600_ANSWERS = $answers
        $env:LOCALAPPDATA = Join-Path $work 'appdata'
        [void][IO.Directory]::CreateDirectory($env:LOCALAPPDATA)

        $out = Join-Path $work "out.$n.txt"
        $err = Join-Path $work "err.$n.txt"
        if ($Downloads) {
            $file = Join-Path $repo ("studio\downloads\{0}-Screen-Saver.cmd" -f ((Get-Culture).TextInfo.ToTitleCase($action)))
            $p = Start-Process -FilePath $env:ComSpec -ArgumentList '/d', '/c', "`"$file`"" -RedirectStandardOutput $out -RedirectStandardError $err -Wait -PassThru -NoNewWindow
        } else {
            $p = Start-Process -FilePath $powershell -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$repo\tools\studio-link.ps1`"", "-$action" `
                -RedirectStandardOutput $out -RedirectStandardError $err -Wait -PassThru -NoNewWindow
        }
        $got = Get-Normalized (([IO.File]::ReadAllText($out)) + ([IO.File]::ReadAllText($err)))
        $want = Get-Normalized ([IO.File]::ReadAllText((Join-Path $dir.FullName "expected.$n")))
        $wantCode = 0
        if ($conf.ContainsKey("exit.$n")) { $wantCode = [int]$conf["exit.$n"] }
        $codeOk = ($p.ExitCode -eq $wantCode)
        if ($got -ceq $want -and $codeOk) {
            "  ok    $name, run $n ($action): the exact text"
        } else {
            $fails++
            $why = ''
            if (-not $codeOk) { $why = " (exit $($p.ExitCode), not $wantCode)" }
            "  FAIL  $name, run $n ($action): the exact text$why"
            $g = $got -split "`n"; $w = $want -split "`n"
            for ($i = 0; $i -lt [Math]::Max($g.Count, $w.Count); $i++) {
                if ($g[$i] -cne $w[$i]) { "        line $($i + 1)"; "        expected: $($w[$i])"; "        got:      $($g[$i])"; break }
            }
        }
        foreach ($ip in ([string]$conf["reset.$n"] -split '\s+' | Where-Object { $_ })) {
            Remove-Item -LiteralPath (Join-Path $work "routers\$ip\authorized_keys") -ErrorAction SilentlyContinue
        }
    }
    $checks = & $python (Join-Path $repo 'tests\setup_flow_test.py') --check $name $work --windows
    $checks
    if ($LASTEXITCODE) { $fails++ }
    try { [IO.Directory]::Delete($work, $true) } catch {}
}

if ($Downloads) {
    $left = @(Get-ChildItem ([IO.Path]::GetTempPath()) -Filter 'be3600-setup-*.ps1' -ErrorAction SilentlyContinue).Count
    if ($left) { "  FAIL  the unpacked script was left in TEMP ($left file(s))"; $fails++ }
    else { '  ok    the .cmd files leave nothing behind in TEMP' }
}

''
if ($fails) { "$fails Install / Uninstall conversation check(s) FAILED on Windows."; exit 1 }
'All Install / Uninstall conversations match on Windows.'
