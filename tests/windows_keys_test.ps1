# Tests the Windows key protection with the REAL functions and the REAL ssh-keygen / DPAPI / icacls.
# Run:  powershell -File tests\windows_keys_test.ps1   (Windows only; CI runs it)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
. "$repo\tools\lib.ps1"

# load every function from studio-link.ps1 without running its main program
$tokens = $null; $errs = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile("$repo\tools\studio-link.ps1", [ref]$tokens, [ref]$errs)
foreach ($f in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false)) { Invoke-Expression $f.Extent.Text }

$fails = 0
function Check($ok, $name) { if ($ok) { "  ok    $name" } else { "  FAIL  $name"; $script:fails++ } }

$w = Join-Path $env:TEMP ('be3600-wintest-keys-' + [guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($w)
$Script:StateDir = $w
$Script:KeyPath = "$w\studio-key"
$Script:PassPath = "$w\studio-key.pass"
$Script:KeyComment = 'be3600-studio-link-TESTPC'
$Script:AuthKeys = '/etc/dropbear/authorized_keys'
$Script:Testing = $true
$env:BE3600_ASSUME_YES = '1'
$keygen = Get-Exe 'ssh-keygen.exe'

function KeyOpens($phrase) {
    $p = Start-Process -FilePath $env:ComSpec -ArgumentList "/d /s /c `"`"$keygen`" -y -P `"$phrase`" -f `"$($Script:KeyPath)`" > nul 2>&1`"" -NoNewWindow -Wait -PassThru
    return ($p.ExitCode -eq 0)
}

"== address checks"
Check ((Test-HostName '192.168.8.1') -and (Test-HostName 'router.lan')) 'ordinary addresses pass'
Check (-not ((Test-HostName '') -or (Test-HostName '-oProxyCommand=x') -or (Test-HostName '1.2.3.4 & calc') -or (Test-HostName '1.2.3.4"x') -or (Test-HostName 'a|b'))) 'option-like / command-like text is refused'
Check ((-not (Test-UnusualAddress '192.168.1.1')) -and (-not (Test-UnusualAddress '10.0.0.5')) -and (-not (Test-UnusualAddress '172.20.1.1')) -and (Test-UnusualAddress '8.8.8.8') -and (Test-UnusualAddress '172.32.0.1') -and (-not (Test-UnusualAddress 'router.lan'))) 'only a non-home IP address is flagged'
$Script:StateFile = "$w\router.txt"
[IO.File]::WriteAllText($Script:StateFile, "-oProxyCommand=calc`n")
Check ($null -eq (Get-SavedRouter)) 'a hostile saved-address file is ignored'
[IO.File]::WriteAllText($Script:StateFile, "192.168.20.1`n")
Check ((Get-SavedRouter) -eq '192.168.20.1') 'a good saved address is used'
$threw = $false; try { [void](Invoke-RouterSsh -Router '127.0.0.1 & calc' -Remote 'true') } catch { $threw = $true }
Check $threw 'the installer refuses a router address with a command in it (the injection I demonstrated earlier)'

"== token"
$Script:Token = New-Token
Check (($Script:Token.Length -ge 30) -and ($Script:Token -match '^[A-Za-z0-9_-]+$')) 'the token is long and URL-safe'
Check ((Test-Token $Script:Token) -and -not (Test-Token '') -and -not (Test-Token $null) -and -not (Test-Token ($Script:Token + 'x')) -and -not (Test-Token ('a' * $Script:Token.Length))) 'the token check is exact'
Check ((New-Token) -ne (New-Token)) 'every token is different'

"== the passphrase is locked to this Windows account"
$phrase = New-Phrase
Check (($phrase.Length -eq 40) -and ($phrase -match '^[A-Za-z0-9]+$')) 'the passphrase is 40 random letters and digits'
Save-KeyPass $phrase
$blob = [IO.File]::ReadAllText($Script:PassPath)
Check (($blob -notmatch [regex]::Escape($phrase)) -and ($blob.Length -gt 100)) 'the file on disk is an encrypted blob, not the passphrase'
Check ((Get-SavedKeyPass) -eq $phrase) 'it decrypts again for this user'
$acl = (& icacls.exe $Script:PassPath) -join ' '
Check (($acl -match [regex]::Escape($env:USERNAME)) -and ($acl -notmatch 'Everyone|BUILTIN\\Users')) 'only this user (no Everyone / Users) can read the file'
Remove-KeyFiles

"== remember this computer (real ssh-keygen; a fake router)"
$script:sshCalls = @()
function Invoke-Ssh { param([string]$Ip, [string]$Remote, [string]$InputFile)
    $script:sshCalls += , @($Remote, $(if ($InputFile) { [IO.File]::ReadAllText($InputFile) } else { $null }))
    return @{ Code = 0; Out = 'studio-ok' } }
function Test-Login { param([string]$Ip) return $true }
$Script:Password = 'hunter2'; $Key = $null; $Script:KeyPass = $null
Offer-Remember '10.0.0.1'
Check ((Test-Path $Script:KeyPath) -and (Test-Path ($Script:KeyPath + '.pub')) -and (Test-Path $Script:PassPath)) 'key, public key and locked passphrase were created'
$saved = Get-SavedKeyPass
Check (($saved -and $saved.Length -eq 40) -and ($Script:KeyPass -eq $saved) -and ($script:Key -eq $Script:KeyPath) -and ($null -eq $Script:Password)) 'it switched to the key and let go of the password'
Check ((-not (KeyOpens '')) -and (KeyOpens $saved)) 'the private key is encrypted: it opens only with the passphrase'
$add = $script:sshCalls | Where-Object { $_[0] -match 'authorized_keys' } | Select-Object -First 1
Check ($add -and ($add[1] -eq [IO.File]::ReadAllText($Script:KeyPath + '.pub')) -and ($add[0] -match 'chmod 600') -and ($add[0] -match 'TESTPC')) 'only the public key goes to the router, and the file is locked down'
$acl = (& icacls.exe $Script:KeyPath) -join ' '
Check (($acl -match [regex]::Escape($env:USERNAME)) -and ($acl -notmatch 'Everyone|BUILTIN\\Users')) 'only this user can read the private key file'

"== an older, unlocked key is locked in place"
Remove-KeyFiles
$p = Start-Process -FilePath $env:ComSpec -ArgumentList "/d /s /c `"`"$keygen`" -q -t ed25519 -N `"`" -C old -f `"$($Script:KeyPath)`"`"" -NoNewWindow -Wait -PassThru
Check ((KeyOpens '') -and -not (Test-Path $Script:PassPath)) '(setup) an old key with no passphrase'
$script:Key = $Script:KeyPath; $script:KeyPass = $null
Protect-OldKey '10.0.0.1'
$saved = Get-SavedKeyPass
Check (($saved) -and ($Script:KeyPass -eq $saved) -and (-not (KeyOpens '')) -and (KeyOpens $saved)) 'it is now locked with a passphrase held under this Windows account'

"== forget"
$Script:Password = $null
Invoke-Forget '10.0.0.1'
Check ((-not (Test-Path $Script:KeyPath)) -and (-not (Test-Path ($Script:KeyPath + '.pub'))) -and (-not (Test-Path $Script:PassPath))) 'forgetting deletes the key, its public half and the locked passphrase'
$rm = $script:sshCalls | Where-Object { $_[0] -match "sed -i" } | Select-Object -Last 1
Check ($rm -and ($rm[0] -match 'TESTPC')) '...and removes this computer''s key from the router'

"== the password and passphrase only exist in the environment during an ssh call"
Remove-Item Function:\Invoke-Ssh; Remove-Item Function:\Test-Login
. { }   # restore the real Invoke-Ssh from the file
foreach ($f in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and ($n.Name -eq 'Invoke-Ssh' -or $n.Name -eq 'Test-Login') }, $false)) { Invoke-Expression $f.Extent.Text }
[IO.File]::WriteAllText("$w\fake-ssh.cmd", "@echo off`r`nset BE3600_STUDIO_PW> `"$w\envseen.txt`"`r`nexit /b 0`r`n", [Text.Encoding]::ASCII)
$env:BE3600_SSH = "$w\fake-ssh.cmd"
$Script:Password = 'super-secret-pw'; $Key = $null; $Script:KeyPass = $null
$r = Invoke-Ssh -Ip '127.0.0.1' -Remote 'true'
Check ((Get-Content "$w\envseen.txt" -Raw) -match 'super-secret-pw') 'during the ssh call the helper program can see it (that is how ssh gets it)'
Check ([string]::IsNullOrEmpty($env:BE3600_STUDIO_PW) -and [string]::IsNullOrEmpty($env:SSH_ASKPASS) -and [string]::IsNullOrEmpty($env:SSH_ASKPASS_REQUIRE)) 'straight after, it is gone from the environment, so a browser started next cannot inherit it'
Clear-PasswordEnv

""
if ($fails) { "$fails Windows key/validation test(s) FAILED." ; exit 1 } else { 'All Windows key/validation tests passed.' }

try { [IO.Directory]::Delete($w, $true) } catch {}
