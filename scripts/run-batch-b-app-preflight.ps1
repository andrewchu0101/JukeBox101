# Run only the existing, explicitly authorized application read-only inventory.
# Credentials live in the owned child environment; raw stderr is never displayed.
Set-StrictMode -Version Latest

function Get-PreflightSwitches {
    # Return all known and inherited execution gates, closed unless explicitly selected below.
    $known = @('JUKEBOX_RUN_BATCH_B_MIGRATION', 'JUKEBOX_RUN_BATCH_B_MIGRATION_PREFLIGHT',
        'JUKEBOX_RUN_BATCH_B_PREFLIGHT', 'JUKEBOX_RUN_MYSQL_TEST', 'JUKEBOX_RUN_REPOSITORY_MYSQL',
        'JUKEBOX_RUN_MYSQL_READONLY', 'JUKEBOX_TEST_SCHEMA_READY', 'JUKEBOX_DISPOSABLE_DATABASE')
    $inherited = @(Get-ChildItem Env: | Where-Object Name -Like 'JUKEBOX_RUN_*' | Select-Object -ExpandProperty Name)
    return @($known + $inherited | Sort-Object -Unique)
}

function New-PreflightStartInfo {
    param([string]$ProjectRoot, [Security.SecureString]$Secret)
    # [owned child configuration]: No caller-controlled executable or command arguments.
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    $info.Arguments = '-B -m tests.batch_b_preflight'
    $info.WorkingDirectory = $ProjectRoot
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $info.StandardErrorEncoding = New-Object System.Text.UTF8Encoding($false)
    foreach ($gate in Get-PreflightSwitches) { $info.EnvironmentVariables[$gate] = '0' }
    foreach ($key in @($info.EnvironmentVariables.Keys)) {
        if ($key -like 'TEST_MIGRATION_DB_*' -or $key -in @('PYTHONPATH', 'PYTHONHOME')) { $info.EnvironmentVariables.Remove($key) }
    }
    $info.EnvironmentVariables['TEST_DB_HOST'] = '127.0.0.1'
    $info.EnvironmentVariables['TEST_DB_PORT'] = '3306'
    $info.EnvironmentVariables['TEST_DB_USER'] = 'jukebox101_test_app'
    $info.EnvironmentVariables['TEST_DB_NAME'] = 'jukebox101_test'
    $info.EnvironmentVariables['TEST_DB_PASSWORD'] = [System.Net.NetworkCredential]::new('', $Secret).Password
    $info.EnvironmentVariables['JUKEBOX_RUN_BATCH_B_PREFLIGHT'] = '1'
    $info.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
    return $info
}

function New-PreflightProcess {
    # Owned process creation is a narrow mock seam; no configurable executable is accepted.
    return (New-Object System.Diagnostics.Process)
}

function Start-PreflightChild {
    param([System.Diagnostics.ProcessStartInfo]$StartInfo)
    # [owned process]: Consume both pipes concurrently to prevent pipe-buffer deadlock.
    $child = New-PreflightProcess
    $child.StartInfo = $StartInfo
    $started = $false
    try {
        $started = $child.Start()
        if (-not $started) { throw 'CHILD_START_FAILED' }
        $stdoutTask = $child.StandardOutput.ReadToEndAsync()
        $stderrTask = $child.StandardError.ReadToEndAsync()
        if (-not $child.WaitForExit(120000)) { throw 'CHILD_TIMEOUT' }
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $null = $stderrTask.GetAwaiter().GetResult()
        return @{ Output = $stdout; ExitCode = $child.ExitCode }
    }
    finally {
        $StartInfo.EnvironmentVariables.Remove('TEST_DB_PASSWORD')
        try {
            if ($started -and -not $child.HasExited) { $child.Kill(); $null = $child.WaitForExit(5000) }
        }
        finally { $child.Dispose() }
    }
}

function Convert-PreflightOutput {
    param([string]$Output)
    # [trusted output shape]: Invalid output and raw tracebacks never enter console or report files.
    $report = $Output | ConvertFrom-Json -ErrorAction Stop
    if ($null -eq $report -or $report -is [array] -or $report.status -notin @('STOP', 'READONLY_EMPTY_BASELINE_REVIEW_REQUIRED')) {
        throw 'INVALID_PREFLIGHT_REPORT'
    }
    $allowed = @('status', 'error_type', 'mysql_error_number', 'reason', 'identity', 'privileges', 'tables',
        'unknown_tables', 'baseline', 'structure', 'migration_file_checksum', 'migration', 'stop_reasons',
        'application_readiness', 'migration_readiness', 'migration_authorized')
    if (@($report.PSObject.Properties.Name | Where-Object { $_ -notin $allowed }).Count -ne 0) { throw 'UNEXPECTED_REPORT_FIELDS' }
    return ($report | ConvertTo-Json -Depth 30)
}

function Save-PreflightReport {
    param([string]$Json)
    # [nonsecret report]: Only the existing entry point's parsed JSON may be persisted.
    $reportPath = Join-Path ([System.IO.Path]::GetTempPath()) ('jukebox101-app-preflight-' + [guid]::NewGuid().ToString('N') + '.json')
    [System.IO.File]::WriteAllText($reportPath, $Json, (New-Object System.Text.UTF8Encoding($false)))
    return $reportPath
}

function Invoke-AppPreflight {
    param([string]$ProjectRoot)
    $ErrorActionPreference = 'Stop'
    # [cleanup ownership]: Never set the password in the parent's environment.
    $secret = $null
    $info = $null
    $nativeExitCode = 2
    $reportPath = $null
    try {
        foreach ($gate in Get-PreflightSwitches) { Set-Item -LiteralPath ('Env:' + $gate) -Value '0' }
        Remove-Item Env:TEST_DB_PASSWORD -ErrorAction SilentlyContinue
        if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot '.venv\Scripts\python.exe'))) { throw 'PYTHON_MISSING' }
        $secret = Read-Host 'Application test account password (hidden)' -AsSecureString
        $info = New-PreflightStartInfo -ProjectRoot $ProjectRoot -Secret $secret
        $outcome = Start-PreflightChild -StartInfo $info
        $nativeExitCode = $outcome.ExitCode
        $json = Convert-PreflightOutput -Output $outcome.Output
        $reportPath = Save-PreflightReport -Json $json
    }
    catch { $json = '{"status":"STOP","reason":"PREFLIGHT_LAUNCH_OR_REPORT_FAILED"}' }
    finally {
        Remove-Item Env:TEST_DB_PASSWORD -ErrorAction SilentlyContinue
        if ($null -ne $info) { $info.EnvironmentVariables.Remove('TEST_DB_PASSWORD') }
        foreach ($gate in Get-PreflightSwitches) { Set-Item -LiteralPath ('Env:' + $gate) -Value '0' }
        if ($null -ne $secret) { $secret.Dispose() }
    }
    return @{ Json = $json; ExitCode = $nativeExitCode; ReportPath = $reportPath }
}

# Dot-sourcing defines functions only, allowing offline mocks to avoid launching Python.
if ($MyInvocation.InvocationName -ne '.') {
    $projectRoot = Split-Path -Parent $PSScriptRoot
    $result = Invoke-AppPreflight -ProjectRoot $projectRoot
    Write-Output $result.Json
    Write-Output ('Exit code: ' + $result.ExitCode)
    Write-Output ('Report path: ' + $result.ReportPath)
    exit $result.ExitCode
}
