# Entirely synthetic launcher refusal tests; never starts Python, PowerShell child processes or a database.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\scripts\run-batch-b-acceptance.ps1')
$script:Checks = 0
function Assert-Refusal {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
    $script:Checks++
}
function New-SyntheticResult {
    param([int]$ExitCode, [string]$Json)
    $child = [pscustomobject]@{ExitCode=$ExitCode; HasExited=$true}
    $child | Add-Member ScriptMethod WaitForExit { $this.HasExited=$true }
    return @{Child=$child; Output=[System.Threading.Tasks.Task]::FromResult([string]$Json);
        Errors=[System.Threading.Tasks.Task]::FromResult([string]'')}
}
$refusalJson = '{"stage":"REFUSED_ASSERTIONS_DISABLED","safety_code":"ASSERTIONS_DISABLED"}'
$stageBOptions = @{RecoveryJournal='';RestoreRecovery=$false;FkLab=$false;RepairRecoveryFk=$false;StageBOnly=$true}
$normalOptions = @{RecoveryJournal='';RestoreRecovery=$false;FkLab=$false;RepairRecoveryFk=$false;StageBOnly=$false}

foreach ($options in @($stageBOptions, $normalOptions)) {
    $case = New-SyntheticResult 3 $refusalJson
    $outcome = Get-AcceptanceResult $case.Child $options $case.Output $case.Errors
    Assert-Refusal ($outcome.ExitCode -eq 3) 'Refusal exit code 3 was not preserved'
    Assert-Refusal (($outcome.Json | ConvertFrom-Json).stage -eq 'REFUSED_ASSERTIONS_DISABLED') 'Refusal stage was not reported'
}

$zeroExit = New-SyntheticResult 0 $refusalJson
$zeroRejected = $false
try { $null = Get-AcceptanceResult $zeroExit.Child $normalOptions $zeroExit.Output $zeroExit.Errors } catch { $zeroRejected = $true }
Assert-Refusal $zeroRejected 'Refusal report with exit 0 was accepted'

# Pre-prompt interpreter check: the real probe is replaced by a synthetic status; no Python, credential or database is involved.
$script:ProbeExit = 0
$script:PromptsReached = 0
$script:ChildStarted = $false
function Get-InterpreterAssertionStatus {
    param([string]$ProjectRoot)
    return $script:ProbeExit
}
function Read-Host {
    param([string]$Prompt, [switch]$AsSecureString)
    $script:PromptsReached++
    if ($Prompt -like 'Stop all*') { return 'EXCLUSIVE' }
    if ($AsSecureString.IsPresent) {
        $syntheticSecret = [pscustomobject]@{Synthetic=$true}
        $syntheticSecret | Add-Member ScriptMethod Dispose { }
        return $syntheticSecret
    }
    return 'synthetic-value'
}
function New-AcceptanceProcess {
    $fake = [pscustomobject]@{StartInfo=$null; HasExited=$false; ExitCode=0}
    $fake | Add-Member ScriptMethod Start { $script:ChildStarted = $true; throw 'STOP_AFTER_PROMPT' }
    $fake | Add-Member ScriptMethod WaitForExit { }
    $fake | Add-Member ScriptMethod Dispose { }
    return $fake
}
$root = Split-Path -Parent $PSScriptRoot

$script:ProbeExit = 3; $script:PromptsReached = 0; $script:ChildStarted = $false
$refusedResult = $null; $refusedError = ''
try { $refusedResult = Invoke-BatchBAcceptance $root } catch { $refusedError = $_.Exception.Message }
Assert-Refusal ($refusedError -eq '' -and $refusedResult.ExitCode -eq 3) 'Pre-prompt refusal did not exit 3'
Assert-Refusal ($script:PromptsReached -eq 0) 'Credential prompt reached after assertion refusal'
Assert-Refusal (-not $script:ChildStarted) 'Child started after assertion refusal'
Assert-Refusal (($refusedResult.Json | ConvertFrom-Json).safety_code -eq 'ASSERTIONS_DISABLED') 'Refusal safety code missing'

$script:ProbeExit = 0; $script:PromptsReached = 0; $script:ChildStarted = $false
$proceededError = ''
try { $null = Invoke-BatchBAcceptance $root } catch { $proceededError = $_.Exception.Message }
Assert-Refusal ($script:PromptsReached -gt 0) 'Normal interpreter did not reach the credential prompt'
Assert-Refusal ($proceededError -like '*STOP_AFTER_PROMPT*' -and $script:ChildStarted) ('Normal interpreter did not reach the child start: ' + $proceededError)

$script:ProbeExit = 1; $script:PromptsReached = 0; $script:ChildStarted = $false
$unknownError = ''
try { $null = Invoke-BatchBAcceptance $root } catch { $unknownError = $_.Exception.Message }
Assert-Refusal ($unknownError -eq 'INTERPRETER_CHECK_FAILED') 'Unexpected interpreter status did not fail closed'
Assert-Refusal ($script:PromptsReached -eq 0) 'Prompt reached after an unexpected interpreter status'

Write-Output ('PASS: ' + $script:Checks + ' refusal assertions; no process or MySQL started.')
