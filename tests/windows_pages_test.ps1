# Studio-Link.cmd's screen-page list (Motion Studio's widget selector), without a router:
# the functions are taken from tools/studio-link.ps1 itself and ssh is replaced by a stand-in.
#   powershell -NoProfile -ExecutionPolicy Bypass -File tests/windows_pages_test.ps1

$ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path (Split-Path -Parent $PSScriptRoot) 'tools\studio-link.ps1'), [ref]$null, [ref]$null)
$want = 'New-Reply','ConvertFrom-PagesList','Get-Pages','Set-Pages','Get-LastLine'
foreach ($fn in $ast.FindAll({ $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
  if ($want -contains $fn.Name) { . ([ScriptBlock]::Create($fn.Extent.Text)) }
}
$Script:Calls = @()
function Test-QuietLogin { $true }
function Get-TargetRouter { '10.0.0.1' }
function Write-Step { }
$Script:Out = "order`tanimations clock`n*`tanimations`tyour saved animations`n*`tclock`tbig clock`n-`twifiqr`tQR code`n"
$Script:Code = 0
function Invoke-Ssh { param($Ip, $Remote) $Script:Calls += $Remote; @{ Code = $Script:Code; Out = $Script:Out } }
$DryRun = $false
$fails = 0
function Check($c, $n) { if ($c) { "  ok    $n" } else { "  FAIL  $n"; $script:fails++ } }
$r = Get-Pages
Check ($r.Status -eq 200 -and ($r.Data.order -join ' ') -eq 'animations clock' -and $r.Data.pages.Count -eq 3 -and $r.Data.pages[2].on -eq $false -and $Script:Calls[-1] -eq 'be3600-anim pages --plain') 'Get-Pages lists the router pages'
$json = ConvertTo-Json -InputObject $r.Data -Compress -Depth 6
Check ($json -match '"order":\["animations","clock"\]' -and $json -match '"name":"wifiqr"') "and it goes out as JSON the page reads: $json"
$r = Set-Pages "animations  clock`n"
Check ($r.Status -eq 200 -and $Script:Calls[-1] -eq "be3600-anim pages set 'animations clock'") 'Set-Pages saves a tidy list'
$n = $Script:Calls.Count
foreach ($b in '', 'clock; reboot', '$(id)', "clock 'x", ('a' * 41)) { $r = Set-Pages $b; Check ($r.Status -eq 400) "Set-Pages refuses [$b]" }
Check ($Script:Calls.Count -eq $n) 'and none of them reached ssh'
$Script:Code = 1; $Script:Out = "usage: be3600-anim pages`n"
$r = Get-Pages
Check ($r.Status -eq 502 -and $r.Data.message -match 'too old') 'an older router is told to update'
Check ($null -eq (ConvertFrom-PagesList "order`tanimations`n")) 'a list with no pages is not a list'
exit $fails
