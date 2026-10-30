# Entirely synthetic launcher tests; never starts Python or opens a database.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\scripts\run-batch-b-acceptance.ps1')
$script:Checks = 0
$script:Secrets = @()
$script:InputIndex = 0
$script:Mode = 'normal'
$script:ExpectedMode = 'normal'
function Assert-Bridge {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
    $script:Checks++
}
function Read-Host {
    param([string]$Prompt, [switch]$AsSecureString)
    if ($Prompt -like 'Stop all*') {
        if ($Prompt -like '*READONLY*') { return 'READONLY' }
        return 'EXCLUSIVE'
    }
    if ($Prompt -like 'Local DBA*') { return 'root' }
    if ($Prompt -like 'Previously reviewed read-only*') { return ('a' * 64) }
    if ($Prompt -like 'Previously*') { return '11111111-2222-4333-8444-555555555555' }
    if ($Prompt -like 'After approved Stage B*') {
        if ($script:Mode -eq 'reject-stage-confirm') { return 'NO' }
        return ($Prompt -split 'type ', 2)[1]
    }
    if ($Prompt -like 'After approved*') {
        if ($script:Mode -eq 'reject-confirm') { return 'NO' }
        return 'RESTORE 14d8aee30b034a9883e9f71cdec8146e'
    }
    if ($Prompt -like 'Confirm exclusive*') { return 'EXCLUSIVE_NO_DDL' }
    Assert-Bridge $AsSecureString.IsPresent 'Unmasked password prompt'
    $script:InputIndex++
    $secret = ConvertTo-SecureString ('fake-secret-' + $script:InputIndex) -AsPlainText -Force
    $script:Secrets += $secret
    return $secret
}
function New-AcceptanceProcess {
    $fake = [pscustomobject]@{StartInfo=$null; HasExited=$false; ExitCode=23; Disposed=$false;
        StandardInput=(New-Object System.IO.StringWriter); StandardOutput=[pscustomobject]@{}; StandardError=[pscustomobject]@{}}
    $fake.StandardOutput | Add-Member ScriptMethod ReadToEndAsync {
        $stage = if ($script:ExpectedMode -eq 'stage-b') { 'B_ONLY_FAILED_BLOCKED' } elseif ($script:ExpectedMode -eq 'repair') { 'R_FK_FAILED_BLOCKED' } elseif ($script:ExpectedMode -eq 'lab') { 'FK_LAB_COMPLETE' } elseif ($script:ExpectedMode -eq 'read') { 'R_READ_ONLY_COMPLETE' } elseif ($script:ExpectedMode -eq 'write') { 'R_RECOVERY_FAILED_BLOCKED' } else { 'B_TEST_FAILED' }
        [System.Threading.Tasks.Task]::FromResult([string]('{"stage":"' + $stage + '"}'))
    }
    $fake.StandardError | Add-Member ScriptMethod ReadToEndAsync { [System.Threading.Tasks.Task]::FromResult([string]'fake-private-driver-text') }
    $fake | Add-Member ScriptMethod Start {
        if ($script:Mode -eq 'start-failure') { throw 'fake-private-start-text' }
        return $true
    }
    $fake | Add-Member ScriptMethod WaitForExit { $this.HasExited=$true }
    $fake | Add-Member ScriptMethod Dispose { $this.Disposed=$true }
    $script:Child = $fake
    return $fake
}
$root = Split-Path -Parent $PSScriptRoot
$env:TEST_DB_PASSWORD = 'synthetic-inherited-secret'
$env:TEST_DBA_DB_PASSWORD = 'synthetic-dba-secret'
$env:JUKEBOX_RUN_BATCH_B_MIGRATION = '1'
try {
    $outcome = Invoke-BatchBAcceptance $root
    Assert-Bridge ($outcome.ExitCode -eq 23) 'Exit code changed'
    Assert-Bridge ($outcome.Json -notlike '*private*') 'Raw stderr escaped'
    Assert-Bridge $script:Child.Disposed 'Child not disposed'
    $info = $script:Child.StartInfo
    Assert-Bridge ($info.Arguments -eq '-B -m tests.batch_b_acceptance') 'Wrong entry'
    Assert-Bridge ($info.Arguments -notlike '*secret*') 'Secret argument'
    Assert-Bridge ($info.EnvironmentVariables['JUKEBOX_RUN_BATCH_B_MIGRATION'] -eq '0') 'Migration enabled'
    Assert-Bridge (-not $info.EnvironmentVariables.ContainsKey('TEST_DB_PASSWORD')) 'Inherited application password'
    Assert-Bridge (-not $info.EnvironmentVariables.ContainsKey('TEST_DBA_DB_PASSWORD')) 'Inherited DBA password'
    $lines = $script:Child.StandardInput.ToString().Trim().Split("`n")
    Assert-Bridge ($lines.Count -eq 4) 'Wrong private framing'
    Assert-Bridge (($lines[0] | ConvertFrom-Json).authorization -eq 'BATCH_B_A_PLUS_B') 'Missing authorization'
    Assert-Bridge (($lines[1] | ConvertFrom-Json) -eq 'fake-secret-1') 'IPC lost credential'
    $unicode = 'synthetic-' + [char]0x4E2D + [char]0xD83D + [char]0xDD0D + "`n`"\"
    $encoded = ConvertTo-AsciiJson $unicode
    Assert-Bridge (-not [regex]::IsMatch($encoded, '[^\x00-\x7F]')) 'Non-ASCII pipe encoding'
    Assert-Bridge (($encoded | ConvertFrom-Json) -ceq $unicode) 'Unicode/quotes/newline corrupted'
    $invalid = $false
    try { $null = Convert-AcceptanceReport '{"stage":"A_PRECHECK_FAILED","password":"fake"}' } catch { $invalid=$true }
    Assert-Bridge $invalid 'Unexpected report field escaped'
    $diagnostic = Convert-AcceptanceReport '{"stage":"A_PRECHECK_FAILED","error_type":"OperationalError","failure_step":"A_EXISTING_ACCOUNTS","mysql_error_number":1142}'
    Assert-Bridge (($diagnostic | ConvertFrom-Json).mysql_error_number -eq 1142) 'Driver number lost'
    Assert-Bridge (($diagnostic | ConvertFrom-Json).failure_step -eq 'A_EXISTING_ACCOUNTS') 'Failure step lost'
    foreach ($bad in @('{"stage":"A_PRECHECK_FAILED","failure_step":"SELECT secret FROM private"}',
        '{"stage":"A_PRECHECK_FAILED","mysql_error_number":"fake-private-driver-text"}',
        '{"stage":"A_PRECHECK_FAILED","mysql_error_number":true}',
        '{"stage":"A_PRECHECK_FAILED","mysql_error_number":-1}')) {
        $rejected = $false
        try { $null = Convert-AcceptanceReport $bad } catch { $rejected=$true }
        Assert-Bridge $rejected 'Unsafe diagnostic field accepted'
    }
    foreach ($secret in $script:Secrets) {
        $disposed = $false
        try { $null = $secret.Copy() } catch { $disposed=$true }
        Assert-Bridge $disposed 'SecureString not disposed'
    }
    $script:Mode = 'start-failure'
    $failed = $false
    try { $null = Invoke-BatchBAcceptance $root } catch { $failed=$true }
    Assert-Bridge $failed 'Start failure suppressed'
    Assert-Bridge $script:Child.Disposed 'Failure process not disposed'
    foreach ($secret in $script:Secrets) {
        $disposed = $false
        try { $null = $secret.Copy() } catch { $disposed=$true }
        Assert-Bridge $disposed 'Failure SecureString not disposed'
    }
    $script:Mode = 'normal'
    $script:ExpectedMode = 'read'
    $before = $script:Secrets.Count
    $null = Invoke-BatchBAcceptance $root -RecoveryJournal 'synthetic-incident.json'
    Assert-Bridge ($script:Secrets.Count -eq $before + 1) 'Recovery requested unnecessary DBA/App secrets'
    Assert-Bridge ($script:Child.StartInfo.Arguments -eq '-B -m tests.batch_b_acceptance --inspect-recovery') 'Recovery entered write mode'
    $frames = $script:Child.StandardInput.ToString().Trim().Split("`n")
    Assert-Bridge ($frames.Count -eq 2) 'Recovery IPC needs only header and one credential'
    $header = $frames[0] | ConvertFrom-Json
    Assert-Bridge ($header.authorization -eq 'BATCH_B_RECOVERY_READONLY') 'Recovery intent missing'
    Assert-Bridge ($header.journal -eq 'synthetic-incident.json') 'Recovery journal changed'
    Assert-Bridge ($header.account -eq 'jukebox101_test_readonly') 'Recovery must use the dedicated read-only account'
    Assert-Bridge ($script:Child.StartInfo.EnvironmentVariables['JUKEBOX_RUN_BATCH_B_MIGRATION'] -eq '0') 'Recovery enabled migration'
    $script:ExpectedMode = 'write'
    $before = $script:Secrets.Count
    $null = Invoke-BatchBAcceptance $root -RecoveryJournal 'synthetic-incident.json' -RestoreRecovery
    Assert-Bridge ($script:Secrets.Count -eq $before + 1) 'Restore requires only existing Harness credential'
    Assert-Bridge ($script:Child.StartInfo.Arguments -eq '-B -m tests.batch_b_acceptance --restore-recovery') 'Wrong restore entry'
    $restoreFrames = $script:Child.StandardInput.ToString().Trim().Split("`n")
    $restoreHeader = $restoreFrames[0] | ConvertFrom-Json
    Assert-Bridge ($restoreHeader.authorization -eq 'BATCH_B_RECOVERY_WRITE') 'Restore intent missing'
    Assert-Bridge ($restoreHeader.confirmation -eq 'RESTORE 14d8aee30b034a9883e9f71cdec8146e') 'Wrong incident confirmation'
    Assert-Bridge $restoreHeader.exclusive_no_ddl 'Missing exclusive metadata attestation'
    Assert-Bridge ($restoreHeader.schema_fingerprint -eq ('a' * 64)) 'Approved schema pin lost'
    $script:Mode = 'reject-confirm'
    $before = $script:Secrets.Count
    $rejected = $false
    try { $null = Invoke-BatchBAcceptance $root -RecoveryJournal 'synthetic-incident.json' -RestoreRecovery } catch { $rejected=$true }
    Assert-Bridge $rejected 'Unconfirmed restore accepted'
    Assert-Bridge ($script:Secrets.Count -eq $before) 'Credential requested before explicit restore confirmation'
    $rejected = $false
    try { $null = Invoke-BatchBAcceptance $root -RestoreRecovery } catch { $rejected=$true }
    Assert-Bridge $rejected 'Restore without journal fell back to A/B'
    $script:Mode = 'normal'
    $script:ExpectedMode = 'lab'
    $before = $script:Secrets.Count
    $null = Invoke-BatchBAcceptance $root -FkLab
    Assert-Bridge ($script:Secrets.Count -eq $before + 1) 'Lab must request only one DBA credential'
    Assert-Bridge ($script:Child.StartInfo.Arguments -eq '-B -m tests.batch_b_acceptance --fk-lab') 'Lab dispatched business writes'
    $labFrames = $script:Child.StandardInput.ToString().Trim().Split("`n")
    Assert-Bridge ($labFrames.Count -eq 2) 'Lab requires only header and one private credential'
    Assert-Bridge (($labFrames[0] | ConvertFrom-Json).authorization -eq 'FK_LAB_CREATE_AND_TEST') 'Lab authorization missing'
    Assert-Bridge ($script:Child.StartInfo.Arguments -notlike '*secret*') 'Lab exposed credential argument'
    Assert-Bridge (-not $script:Child.StartInfo.EnvironmentVariables.ContainsKey('TEST_DB_PASSWORD')) 'Lab inherited application secret'
    $rejected = $false
    try { $null = Invoke-BatchBAcceptance $root -FkLab -RecoveryJournal 'synthetic' } catch { $rejected=$true }
    Assert-Bridge $rejected 'Lab mixed with recovery mode'
    Assert-Bridge ($script:Secrets.Count -eq $before + 1) 'Mode conflict prompted for a credential'
    $script:ExpectedMode = 'repair'
    $before = $script:Secrets.Count
    $null = Invoke-BatchBAcceptance $root -RecoveryJournal 'synthetic-incident.json' -RepairRecoveryFk
    Assert-Bridge ($script:Secrets.Count -eq $before + 2) 'FK repair needs existing DBA and Harness only'
    Assert-Bridge ($script:Child.StartInfo.Arguments -eq '-B -m tests.batch_b_acceptance --repair-recovery-fk') 'FK repair dispatched old restore or A/B'
    $repairFrames = $script:Child.StandardInput.ToString().Trim().Split("`n")
    Assert-Bridge ($repairFrames.Count -eq 3) 'Wrong repair credential framing'
    Assert-Bridge (($repairFrames[1] | ConvertFrom-Json) -eq ('fake-secret-' + ($script:InputIndex - 1))) 'Repair DBA credential order changed'
    Assert-Bridge (($repairFrames[2] | ConvertFrom-Json) -eq ('fake-secret-' + $script:InputIndex)) 'Repair Harness credential order changed'
    $repairHeader = $repairFrames[0] | ConvertFrom-Json
    Assert-Bridge ($repairHeader.authorization -eq 'BATCH_B_RECOVERY_FK_REPAIR') 'Missing separate DDL authorization'
    Assert-Bridge ($repairHeader.server_uuid -eq 'ab73bd54-c3c3-11f1-bc47-005056c00001') 'Repair did not pin approved UUID'
    Assert-Bridge ($repairHeader.schema_fingerprint -eq '5f201eabfb5828f80029497e3eca81432ed99a81aebbd15f2a23515cc93c62a8') 'Repair schema approval changed'
    Assert-Bridge ($script:Child.StartInfo.EnvironmentVariables['JUKEBOX_RUN_BATCH_B_MIGRATION'] -eq '0') 'Repair enabled migration'
    foreach ($arguments in @(@{RepairRecoveryFk=$true}, @{RepairRecoveryFk=$true;RecoveryJournal='x';RestoreRecovery=$true}, @{RepairRecoveryFk=$true;FkLab=$true;RecoveryJournal='x'})) {
        $rejected=$false
        try { $null=Invoke-BatchBAcceptance $root @arguments } catch { $rejected=$true }
        Assert-Bridge $rejected 'FK repair mode conflict was accepted'
    }
    foreach ($secret in $script:Secrets) {
        $disposed=$false
        try { $null=$secret.Copy() } catch { $disposed=$true }
        Assert-Bridge $disposed 'Repair credential not disposed'
    }
    $bridgeReport = Convert-AcceptanceReport '{"stage":"R_FK_FAILED_BLOCKED","fk_verified_state":"BRIDGE","fk_ddl_progress":[{"direction":"cascade_ddl_outcome","step":1,"sql":"ALTER TABLE `jukebox101_test`.`jb2_versions` ADD CONSTRAINT `fk_jb2_version_root_recovery` FOREIGN KEY (`canonical_id`) REFERENCES `jb2_versions` (`version_id`) ON DELETE CASCADE","outcome":"ACKNOWLEDGED"}]}'
    Assert-Bridge (($bridgeReport | ConvertFrom-Json).fk_verified_state -eq 'BRIDGE') 'Bridge verification state lost'
    Assert-Bridge (($bridgeReport | ConvertFrom-Json).fk_ddl_progress[0].outcome -eq 'ACKNOWLEDGED') 'Separate DDL step evidence lost'
    $script:Mode = 'normal'
    $script:ExpectedMode = 'stage-b'
    $stageOutcome = Invoke-BatchBAcceptance $root -StageBOnly
    Assert-Bridge ($stageOutcome.ExitCode -eq 23) 'Stage B original exit code lost'
    Assert-Bridge ($script:Child.StartInfo.Arguments -eq '-B -m tests.batch_b_acceptance --stage-b-only') 'Stage B fell through to A+B'
    $stageLines = $script:Child.StandardInput.ToString().Trim().Split("`n")
    $stageHeader = $stageLines[0] | ConvertFrom-Json
    Assert-Bridge ($stageLines.Count -eq 4) 'Stage B role credential framing changed'
    Assert-Bridge ($stageHeader.authorization -eq 'BATCH_B_STAGE_B_ONLY') 'Stage B authority lost'
    Assert-Bridge ($stageHeader.confirmation -eq ('STAGE_B_ONLY ' + $stageHeader.session_id)) 'Stage B confirmation not bound to new session'
    Assert-Bridge ($stageHeader.session_id -cmatch '^[0-9a-f-]{36}$') 'Stage B session is not a UUID'
    Assert-Bridge ($stageHeader.server_uuid -eq 'ab73bd54-c3c3-11f1-bc47-005056c00001') 'Stage B UUID pin changed'
    Assert-Bridge ($stageHeader.schema_fingerprint -eq '5f201eabfb5828f80029497e3eca81432ed99a81aebbd15f2a23515cc93c62a8') 'Stage B Schema pin changed'
    Assert-Bridge $stageHeader.exclusive_no_ddl 'Stage B exclusive access missing'
    Assert-Bridge ($null -eq $stageHeader.PSObject.Properties['journal']) 'Stage B reused old incident evidence path'
    Assert-Bridge ($script:Child.StartInfo.EnvironmentVariables['JUKEBOX_RUN_BATCH_B_MIGRATION'] -eq '0') 'Stage B enabled migration'
    Assert-Bridge (-not $script:Child.StartInfo.EnvironmentVariables.ContainsKey('TEST_DB_PASSWORD')) 'Stage B inherited private app credential'
    $null = Invoke-BatchBAcceptance $root -StageBOnly
    $nextHeader = ($script:Child.StandardInput.ToString().Trim().Split("`n")[0]) | ConvertFrom-Json
    Assert-Bridge ($nextHeader.session_id -ne $stageHeader.session_id) 'Stage B reused a session UUID'
    foreach ($conflict in @(@{StageBOnly=$true;FkLab=$true}, @{StageBOnly=$true;RecoveryJournal='old.json'},
        @{StageBOnly=$true;RestoreRecovery=$true}, @{StageBOnly=$true;RepairRecoveryFk=$true;RecoveryJournal='old.json'})) {
        $rejected = $false
        try { $null = Invoke-BatchBAcceptance $root @conflict } catch { $rejected=$true }
        Assert-Bridge $rejected 'Stage B accepted conflicting mode'
    }
    $script:Mode = 'reject-stage-confirm'
    $rejected = $false
    try { $null = Invoke-BatchBAcceptance $root -StageBOnly } catch { $rejected=$true }
    Assert-Bridge $rejected 'Stage B ignored failed per-session confirmation'
    $script:Mode = 'normal'
    $script:ExpectedMode = 'normal'
    $rejected = $false
    try { $null = Invoke-BatchBAcceptance $root -StageBOnly } catch { $rejected=$true }
    Assert-Bridge $rejected 'Stage B accepted an A+B worker report'
    $completeStage = Convert-AcceptanceReport '{"stage":"B_ONLY_COMPLETE_MANUAL_ACCEPTANCE_PENDING","session_id":"11111111-2222-4333-8444-555555555555","previous_session_blocked":true,"session_blocked":false,"tests_verified":true}'
    Assert-Bridge (($completeStage | ConvertFrom-Json).previous_session_blocked -eq $true) 'Stage B lost old Session Block'
    Write-Output ('PASS: ' + $script:Checks + ' offline assertions; no process or MySQL started.')
}
finally {
    Remove-Item Env:TEST_DB_PASSWORD,Env:TEST_DBA_DB_PASSWORD -ErrorAction SilentlyContinue
    $env:JUKEBOX_RUN_BATCH_B_MIGRATION = '0'
}
