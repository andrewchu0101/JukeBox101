# Interactive, single-use credential bridge. Never dot-source with real credentials.
param([string]$RecoveryJournal = '', [switch]$RestoreRecovery, [switch]$FkLab, [switch]$RepairRecoveryFk, [switch]$StageBOnly)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function New-AcceptanceStartInfo {
    param([string]$ProjectRoot)
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    $info.Arguments = '-B -m tests.batch_b_acceptance'
    $info.WorkingDirectory = $ProjectRoot
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    foreach ($key in @($info.EnvironmentVariables.Keys)) {
        if ($key -like '*PASSWORD*' -or $key -like '*TOKEN*' -or $key -like '*SECRET*' -or $key -like '*API_KEY*' -or
            $key -like 'TEST_*DB_*' -or $key -like 'PYTEST_*' -or $key -like 'COVERAGE_*' -or
            $key -in @('PYTHONPATH','PYTHONHOME','PYTHONINSPECT')) {
            $info.EnvironmentVariables.Remove($key)
        }
        elseif ($key -like 'JUKEBOX_RUN_*') { $info.EnvironmentVariables[$key] = '0' }
    }
    $info.EnvironmentVariables['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    $info.EnvironmentVariables['PYTEST_ADDOPTS'] = ''
    $info.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
    return $info
}

function Send-AcceptanceCredential {
    param([System.IO.TextWriter]$Writer, [Security.SecureString]$Secret)
    # [brief plaintext window]: Managed strings cannot be reliably wiped; no environment or file copy.
    $plain = $null
    try {
        $plain = [System.Net.NetworkCredential]::new('', $Secret).Password
        $Writer.WriteLine((ConvertTo-AsciiJson $plain))
        $Writer.Flush()
    }
    finally { $plain = $null }
}

function ConvertTo-AsciiJson {
    param([object]$Value)
    # PS 5.1 lacks StandardInputEncoding; ASCII JSON avoids OEM/UTF-8 corruption.
    $json = ConvertTo-Json -InputObject $Value -Compress
    try {
        return [regex]::Replace($json, '[^\x00-\x7F]', {
            param($match)
            '\u{0:x4}' -f [int][char]$match.Value
        })
    }
    finally { $json = $null }
}

function Convert-AcceptanceReport {
    param([string]$Output)
    $report = $Output | ConvertFrom-Json -ErrorAction Stop
    $allowed = @('stage','a_completed','b_started','baseline_restored','error_type','safety_code',
        'account_created','harness_relocked','tests','journals','pytest_exit_code',
        'current_step','failure_step','mysql_error_number','dba_close_failed','dba_close_error_type','dba_close_mysql_error_number',
        'test_reports','read_only','recovery_report','run_id','journal_sha256','baseline_diff',
        'session_blocked','cleanup_progress','recovery_journal','commit_outcome','rollback_outcome',
        'rollback_error_type','final_counts','read_only_baseline_matches','read_only_investigation_error_type',
        'manifest_sha256','approved_schema_fingerprint','self_fk_plan','recovery_journal_error_type','lab_cleaned','original_schema','cascade_schema','fk_restoration_pending',
        'cascade_ddl_outcome','restore_ddl_outcome','cascade_verified','data_baseline_verified','fk_restore_error_type',
        'fk_restore_mysql_error_number','connection_close_failed','journal_error_type','restore_phase_started','fk_ddl_progress','fk_verified_state','session_id','session_journal','runs','previous_session_blocked','tests_verified')
    if ($null -eq $report -or $report -is [array] -or
        @($report.PSObject.Properties.Name | Where-Object { $_ -notin $allowed }).Count -ne 0 -or
        $report.stage -notin @('A_PRECHECK_FAILED','A_PROVISION_FAILED','A_PRIVILEGE_VERIFY_FAILED',
            'B_ONLY_FAILED_BLOCKED','B_ONLY_COMPLETE_MANUAL_ACCEPTANCE_PENDING','B_BASELINE_FAILED','B_TEST_FAILED','B_CLEANUP_FAILED','B_RESTORE_FAILED','B_VERIFY_FAILED','COMPLETE_MANUAL_ACCEPTANCE_PENDING',
            'R_FK_FAILED_BLOCKED','R_FK_RESTORED_VERIFIED_BLOCKED','FK_LAB_FAILED','FK_LAB_COMPLETE','R_READ_ONLY_FAILED','R_READ_ONLY_COMPLETE','R_RECOVERY_FAILED_BLOCKED',
            'R_RECOVERY_COMMIT_UNKNOWN_BLOCKED','R_RECOVERY_RESTORED_VERIFIED_BLOCKED','REFUSED_ASSERTIONS_DISABLED')) {
        throw 'INVALID_WORKER_REPORT'
    }
    foreach ($field in @('current_step','failure_step','dba_close_error_type','error_type')) {
        $property = $report.PSObject.Properties[$field]
        if ($null -ne $property -and ($property.Value -isnot [string] -or $property.Value -cnotmatch '^[A-Za-z][A-Za-z0-9_]*$')) {
            throw 'INVALID_DIAGNOSTIC_LABEL'
        }
    }
    foreach ($field in @('mysql_error_number','dba_close_mysql_error_number')) {
        $property = $report.PSObject.Properties[$field]
        if ($null -ne $property -and (($property.Value -isnot [int] -and $property.Value -isnot [long]) -or
            $property.Value -lt 0 -or $property.Value -gt 65535)) { throw 'INVALID_DRIVER_NUMBER' }
    }
    return ($report | ConvertTo-Json -Depth 20)
}

function New-AcceptanceProcess {
    # Narrow offline test seam; the live entry always owns this exact child.
    return (New-Object System.Diagnostics.Process)
}

function Close-AcceptanceGates {
    foreach ($gate in @(Get-ChildItem Env: | Where-Object Name -Like 'JUKEBOX_RUN_*')) {
        Set-Item -LiteralPath ('Env:' + $gate.Name) -Value '0'
    }
}

function Assert-AcceptanceMode {
    param($Options)
    $selected = @(@($Options.StageBOnly, $Options.FkLab, $Options.RepairRecoveryFk) | Where-Object { $_ })
    if ($selected.Count -gt 1) { throw 'EXECUTION_MODE_CONFLICT' }
    if ($Options.StageBOnly -and ($Options.RecoveryJournal -or $Options.RestoreRecovery)) { throw 'STAGE_B_MODE_CONFLICT' }
    if ($Options.FkLab -and ($Options.RestoreRecovery -or $Options.RecoveryJournal)) { throw 'FK_LAB_MODE_CONFLICT' }
    if ($Options.RepairRecoveryFk -and (-not $Options.RecoveryJournal -or $Options.RestoreRecovery)) { throw 'FK_REPAIR_MODE_CONFLICT' }
    if ($Options.RestoreRecovery -and -not $Options.RecoveryJournal) { throw 'RESTORE_JOURNAL_REQUIRED' }
}

function Read-StageBRequest {
    $sessionId = [guid]::NewGuid().ToString()
    $confirmation = 'STAGE_B_ONLY ' + $sessionId
    if ((Read-Host ('After approved Stage B authorization, type ' + $confirmation)) -cne $confirmation) { throw 'STAGE_B_CONFIRMATION_REQUIRED' }
    if ((Read-Host 'Confirm exclusive access and no competing writers/DDL; type EXCLUSIVE_NO_DDL') -cne 'EXCLUSIVE_NO_DDL') { throw 'EXCLUSIVE_REQUIRED' }
    $header = @{authorization='BATCH_B_STAGE_B_ONLY';session_id=$sessionId;confirmation=$confirmation;exclusive_no_ddl=$true;
        server_uuid='ab73bd54-c3c3-11f1-bc47-005056c00001';
        schema_fingerprint='5f201eabfb5828f80029497e3eca81432ed99a81aebbd15f2a23515cc93c62a8';
        dba_user=(Read-Host 'Local DBA user (existing account only)')}
    return @{Header=$header;Prompts=@('Existing DBA password for authorized Stage B FK window (hidden)',
        'TEST jukebox101_test_app password, not production (hidden)', 'Existing jukebox101_test_harness password (hidden)')}
}

function Read-RepairRequest {
    param([string]$RecoveryJournal)
    $header = @{authorization='BATCH_B_RECOVERY_FK_REPAIR';journal=$RecoveryJournal;
        schema_fingerprint='5f201eabfb5828f80029497e3eca81432ed99a81aebbd15f2a23515cc93c62a8';
        server_uuid='ab73bd54-c3c3-11f1-bc47-005056c00001';exclusive_no_ddl=$true;
        dba_user=(Read-Host 'Local DBA user (existing account only)')}
    return @{Header=$header;Prompts=@('Existing DBA password for separately authorized FK repair (hidden)',
        'Existing jukebox101_test_harness password (hidden)')}
}

function Read-RecoveryRequest {
    param([string]$RecoveryJournal, [bool]$RestoreRecovery)
    if ($RestoreRecovery) {
        $required = 'RESTORE 14d8aee30b034a9883e9f71cdec8146e'
        if ((Read-Host ('After approved recovery authorization, stop all writers; type ' + $required)) -cne $required) { throw 'RESTORE_CONFIRMATION_REQUIRED' }
        if ((Read-Host 'Confirm exclusive access and no DDL since DBA metadata review; type EXCLUSIVE_NO_DDL') -cne 'EXCLUSIVE_NO_DDL') { throw 'EXCLUSIVE_REQUIRED' }
        $approvedSchema = Read-Host 'Previously reviewed read-only schema_fingerprint (64 lowercase hex)'
        if ($approvedSchema -cnotmatch '^[0-9a-f]{64}$') { throw 'APPROVED_SCHEMA_FINGERPRINT_REQUIRED' }
        $header = @{authorization='BATCH_B_RECOVERY_WRITE';journal=$RecoveryJournal;confirmation=$required;
            schema_fingerprint=$approvedSchema;exclusive_no_ddl=$true}
        return @{Header=$header;Prompts=@('Existing jukebox101_test_harness password (hidden)')}
    }
    if ((Read-Host 'Stop all test DB writers/DDL; type READONLY to inspect only') -cne 'READONLY') { throw 'READONLY_REQUIRED' }
    # [dedicated account]: Inspection never uses the harness identity; the account name is checked again by Python before connecting.
    $header = @{authorization='BATCH_B_RECOVERY_READONLY';journal=$RecoveryJournal;account='jukebox101_test_readonly'}
    return @{Header=$header;Prompts=@('Existing read-only account password (hidden)')}
}

function Read-StandardRequest {
    if ((Read-Host 'Stop all other test DB writers/DDL; type EXCLUSIVE to continue') -cne 'EXCLUSIVE') { throw 'EXCLUSIVE_REQUIRED' }
    $header = @{dba_user=(Read-Host 'Local DBA user (not Application/Migrator/Harness)');
        server_uuid=(Read-Host 'Previously approved MySQL server UUID');authorization='BATCH_B_A_PLUS_B'}
    return @{Header=$header;Prompts=@('DBA password (hidden)', 'TEST jukebox101_test_app password, not production (hidden)',
        'TEST Harness password (hidden; existing account is never recreated)')}
}

function Get-AcceptanceRequest {
    param($Options)
    if ($Options.StageBOnly) { return (Read-StageBRequest) }
    if ($Options.RepairRecoveryFk) { return (Read-RepairRequest $Options.RecoveryJournal) }
    if ($Options.FkLab) {
        return @{Header=@{authorization='FK_LAB_CREATE_AND_TEST';dba_user=(Read-Host 'Local DBA user (existing account only)');
            server_uuid=(Read-Host 'Previously approved MySQL server UUID')};
            Prompts=@('DBA password for separately authorized isolated FK lab (hidden)')}
    }
    if ($Options.RecoveryJournal) { return (Read-RecoveryRequest $Options.RecoveryJournal $Options.RestoreRecovery) }
    return (Read-StandardRequest)
}

function Set-AcceptanceMode {
    param($Child, $Options)
    if ($Options.RecoveryJournal) { $Child.StartInfo.Arguments = '-B -m tests.batch_b_acceptance --inspect-recovery' }
    if ($Options.RestoreRecovery) { $Child.StartInfo.Arguments = '-B -m tests.batch_b_acceptance --restore-recovery' }
    if ($Options.FkLab) { $Child.StartInfo.Arguments = '-B -m tests.batch_b_acceptance --fk-lab' }
    if ($Options.RepairRecoveryFk) { $Child.StartInfo.Arguments = '-B -m tests.batch_b_acceptance --repair-recovery-fk' }
    if ($Options.StageBOnly) { $Child.StartInfo.Arguments = '-B -m tests.batch_b_acceptance --stage-b-only' }
}

function Get-AcceptanceResult {
    param($Child, $Options, $Output, $Errors)
    $Child.WaitForExit()
    $null = $Errors.GetAwaiter().GetResult() # Never display raw Python/driver exceptions.
    $json = Convert-AcceptanceReport $Output.GetAwaiter().GetResult()
    $stage = ($json | ConvertFrom-Json).stage
    # [approved refusal]: Assertion-stripped refusal is exit 3 only; it is never a success and never remapped to 0.
    if ($stage -eq 'REFUSED_ASSERTIONS_DISABLED') {
        if ($Child.ExitCode -ne 3) { throw 'REFUSAL_EXIT_MISMATCH' }
        return @{Json=$json;ExitCode=3}
    }
    if ($Options.StageBOnly -and $stage -notlike 'B_ONLY_*') { throw 'STAGE_B_REPORT_MODE_MISMATCH' }
    if ($Options.RestoreRecovery -and $stage -notlike 'R_RECOVERY_*') { throw 'RESTORE_REPORT_MODE_MISMATCH' }
    if ($Options.RecoveryJournal -and -not $Options.RestoreRecovery -and -not $Options.RepairRecoveryFk -and $stage -notlike 'R_READ_ONLY_*') { throw 'READ_REPORT_MODE_MISMATCH' }
    if ($Options.RepairRecoveryFk -and $stage -notlike 'R_FK_*') { throw 'FK_REPAIR_REPORT_MODE_MISMATCH' }
    if ($Options.FkLab -and $stage -notlike 'FK_LAB_*') { throw 'FK_LAB_REPORT_MODE_MISMATCH' }
    return @{Json=$json;ExitCode=$Child.ExitCode}
}

function Close-AcceptanceProcess {
    param($Child, $Secrets, [bool]$Started)
    Close-AcceptanceGates
    foreach ($secret in $Secrets) { $secret.Dispose() }
    if ($Started) { $Child.StandardInput.Close() }
    if ($Started -and -not $Child.HasExited) { Write-Warning 'Worker may still be running; do not restart or clean manually.' }
    $Child.Dispose()
}

function Send-AcceptanceSecrets {
    param($Child, $Secrets)
    foreach ($secret in $Secrets) {
        Send-AcceptanceCredential $Child.StandardInput $secret
        $secret.Dispose()
    }
}

function Get-InterpreterAssertionStatus {
    param([string]$ProjectRoot)
    # [interpreter truth]: The launcher's own Python reports -O, -OO and PYTHONOPTIMIZE through its flags before any credential is requested.
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    $info.Arguments = '-B -m tests.batch_b_interpreter_check'
    $info.WorkingDirectory = $ProjectRoot
    $info.UseShellExecute = $false
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    foreach ($key in @($info.EnvironmentVariables.Keys)) {
        if ($key -like 'TEST_*' -or $key -like 'DB_*' -or $key -like 'JUKEBOX_RUN*' -or $key -like 'DISCORD_*') { $info.EnvironmentVariables.Remove($key) }
    }
    $probe = New-Object System.Diagnostics.Process
    $probe.StartInfo = $info
    try {
        $null = $probe.Start()
        $probe.StandardInput.Close()
        $stdout, $stderr = $probe.StandardOutput.ReadToEndAsync(), $probe.StandardError.ReadToEndAsync()
        $probe.WaitForExit()
        $null = $stdout.GetAwaiter().GetResult(); $null = $stderr.GetAwaiter().GetResult()
        return $probe.ExitCode
    }
    finally { $probe.Dispose() }
}

function Invoke-BatchBAcceptance {
    param([string]$ProjectRoot, [string]$RecoveryJournal = '', [switch]$RestoreRecovery, [switch]$FkLab, [switch]$RepairRecoveryFk, [switch]$StageBOnly)
    $options = @{RecoveryJournal=$RecoveryJournal;RestoreRecovery=$RestoreRecovery.IsPresent;FkLab=$FkLab.IsPresent;
        RepairRecoveryFk=$RepairRecoveryFk.IsPresent;StageBOnly=$StageBOnly.IsPresent}
    $child, $started, $secrets = (New-AcceptanceProcess), $false, @()
    try {
        Close-AcceptanceGates
        Assert-AcceptanceMode $options
        if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot '.venv\Scripts\python.exe'))) { throw 'PYTHON_MISSING' }
        # [pre-prompt gate]: Refuse before any credential prompt when the launcher's interpreter strips asserts; unknown status fails closed.
        $interpreterStatus = Get-InterpreterAssertionStatus $ProjectRoot
        if ($interpreterStatus -eq 3) { return @{Json='{"stage":"REFUSED_ASSERTIONS_DISABLED","safety_code":"ASSERTIONS_DISABLED"}';ExitCode=3} }
        if ($interpreterStatus -ne 0) { throw 'INTERPRETER_CHECK_FAILED' }
        $request = Get-AcceptanceRequest $options
        foreach ($prompt in $request.Prompts) { $secrets += Read-Host $prompt -AsSecureString }
        $child.StartInfo = New-AcceptanceStartInfo $ProjectRoot
        Set-AcceptanceMode $child $options
        $started = $child.Start()
        if (-not $started) { throw 'CHILD_START_FAILED' }
        $stdout, $stderr = $child.StandardOutput.ReadToEndAsync(), $child.StandardError.ReadToEndAsync()
        $child.StandardInput.WriteLine((ConvertTo-AsciiJson $request.Header))
        Send-AcceptanceSecrets $child $secrets
        $child.StandardInput.Close()
        return (Get-AcceptanceResult $child $options $stdout $stderr)
    }
    finally { Close-AcceptanceProcess $child $secrets $started }
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        $outcome = Invoke-BatchBAcceptance (Split-Path -Parent $PSScriptRoot) -RecoveryJournal $RecoveryJournal -RestoreRecovery:$RestoreRecovery -FkLab:$FkLab -RepairRecoveryFk:$RepairRecoveryFk -StageBOnly:$StageBOnly
        if ($FkLab) {
            $report = $outcome.Json | ConvertFrom-Json
            Write-Output ('Stage: ' + $report.stage + '; Session Block unchanged; business DB untouched.')
            if ($null -ne $report.PSObject.Properties['tests']) {
                foreach ($result in $report.tests) {
                    Write-Output ($result.case + ': Passed=' + $result.passed + '; MySQL=' + $result.mysql_error_number + '; Remaining=' + $result.remaining + '; Rollback=' + $result.rollback)
                }
            }
            if ($null -ne $report.PSObject.Properties['failure_step']) { Write-Output ('Stopped at: ' + $report.failure_step) }
        }
        else { Write-Output $outcome.Json }
        $code = $outcome.ExitCode
        if ($FkLab) { Write-Output ('Exit code: ' + $code) }
    }
    catch {
        $failedStage = if ($StageBOnly) { 'B_ONLY_FAILED_BLOCKED' } elseif ($RepairRecoveryFk) { 'R_FK_FAILED_BLOCKED' } elseif ($FkLab) { 'FK_LAB_FAILED' } elseif ($RestoreRecovery) { 'R_RECOVERY_FAILED_BLOCKED' } elseif ($RecoveryJournal) { 'R_READ_ONLY_FAILED' } else { 'A_PRECHECK_FAILED' }
        Write-Output ('{"stage":"' + $failedStage + '","safety_code":"LOCAL_LAUNCH_FAILED"}')
        $code = 1
    }
    exit $code
}
