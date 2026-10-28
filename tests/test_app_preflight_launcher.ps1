# Offline mocks only: never starts Python, opens sockets, or stores a real secret.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\scripts\run-batch-b-app-preflight.ps1')
$script:RealChildRunner = ${function:Start-PreflightChild}
$script:Cases = 0
$script:Root = Split-Path -Parent $PSScriptRoot
$script:CapturedInfo = $null
$script:CreatedSecret = $null
$script:Mode = 'normal'
$script:MockExitCode = 0
$script:SavedReports = 0

function Assert-Launcher {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}
function Read-Host {
    param([string]$Prompt, [switch]$AsSecureString)
    Assert-Launcher $AsSecureString.IsPresent 'Secure input required'
    $script:CreatedSecret = ConvertTo-SecureString 'synthetic-password-only' -AsPlainText -Force
    return $script:CreatedSecret
}
function Start-PreflightChild {
    param([System.Diagnostics.ProcessStartInfo]$StartInfo)
    $script:CapturedInfo = $StartInfo
    Assert-Launcher (-not (Test-Path Env:TEST_DB_PASSWORD)) 'Parent password must be absent'
    Assert-Launcher ($StartInfo.Arguments -eq '-B -m tests.batch_b_preflight') 'Wrong entry'
    Assert-Launcher ($StartInfo.WorkingDirectory -eq $script:Root) 'Wrong cwd'
    Assert-Launcher ($StartInfo.EnvironmentVariables['TEST_DB_PASSWORD'] -eq 'synthetic-password-only') 'Missing synthetic child credential'
    foreach ($key in (Get-PreflightSwitches)) {
        $expected = if ($key -eq 'JUKEBOX_RUN_BATCH_B_PREFLIGHT') { '1' } else { '0' }
        Assert-Launcher ($StartInfo.EnvironmentVariables[$key] -eq $expected) 'Unsafe child gate'
    }
    foreach ($pair in @(@('HOST','127.0.0.1'), @('PORT','3306'), @('USER','jukebox101_test_app'), @('NAME','jukebox101_test'))) {
        Assert-Launcher ($StartInfo.EnvironmentVariables['TEST_DB_' + $pair[0]] -eq $pair[1]) 'Wrong fixed target'
    }
    Assert-Launcher (-not $StartInfo.EnvironmentVariables.ContainsKey('TEST_MIGRATION_DB_PASSWORD')) 'Migrator credential inherited'
    if ($script:Mode -eq 'throw') { throw 'synthetic-private-driver-exception' }
    if ($script:Mode -eq 'invalid') { return @{ Output = 'synthetic-private-traceback'; ExitCode = $script:MockExitCode } }
    if ($script:Mode -eq 'unexpected') { return @{ Output = '{"status":"STOP","password":"synthetic-private-value"}'; ExitCode = 1 } }
    return @{ Output = '{"status":"STOP","reason":"MOCK_ONLY"}'; ExitCode = $script:MockExitCode }
}
function Save-PreflightReport {
    param([string]$Json)
    $script:SavedReports++
    Assert-Launcher ($Json -notlike '*synthetic-password*') 'Credential in report'
    if ($script:Mode -eq 'report-failure') { throw 'synthetic-private-path-error' }
    return 'MOCK_REPORT_PATH_NO_FILE_WRITTEN'
}
function Assert-Cleanup {
    Assert-Launcher (-not (Test-Path Env:TEST_DB_PASSWORD)) 'Password not removed'
    foreach ($gate in Get-PreflightSwitches) {
        Assert-Launcher ((Get-Item -LiteralPath ('Env:' + $gate)).Value -eq '0') 'Gate not closed'
    }
    Assert-Launcher (-not $script:CapturedInfo.EnvironmentVariables.ContainsKey('TEST_DB_PASSWORD')) 'Child password retained'
    $disposed = $false
    try { $null = $script:CreatedSecret.Copy() } catch { $disposed = $true }
    Assert-Launcher $disposed 'SecureString not disposed'
}
function New-PreflightProcess {
    # Fake every process and pipe operation; never instantiate a native child.
    $fake = [pscustomobject]@{ StartInfo = $null; HasExited = $false; ExitCode = 23; Disposed = $false; Killed = $false;
        StandardOutput = [pscustomobject]@{}; StandardError = [pscustomobject]@{} }
    $fake.StandardOutput | Add-Member ScriptMethod ReadToEndAsync { return [System.Threading.Tasks.Task]::FromResult([string]'{"status":"STOP"}') }
    $fake.StandardError | Add-Member ScriptMethod ReadToEndAsync { return [System.Threading.Tasks.Task]::FromResult([string]'synthetic-private-stderr') }
    $fake | Add-Member ScriptMethod Start { if ($script:Mode -eq 'start-failure') { throw 'synthetic-start-error' }; return $true }
    $fake | Add-Member ScriptMethod WaitForExit { param($milliseconds); $this.HasExited = $script:Mode -ne 'timeout'; return $this.HasExited }
    $fake | Add-Member ScriptMethod Kill { $this.Killed = $true }
    $fake | Add-Member ScriptMethod Dispose { $this.Disposed = $true }
    $script:FakeProcess = $fake
    return $fake
}

try {
    foreach ($code in @(0,1,17)) {
        $script:Mode = 'normal'
        $script:MockExitCode = $code
        $env:JUKEBOX_RUN_FUTURE_WRITE_TEST = '1'
        $env:TEST_DB_PASSWORD = 'synthetic-parent-trap'
        $env:TEST_MIGRATION_DB_PASSWORD = 'synthetic-migrator-trap'
        $result = Invoke-AppPreflight -ProjectRoot $script:Root
        Assert-Launcher ($result.ExitCode -eq $code) 'Original exit code lost'
        Assert-Cleanup
        $script:Cases++
    }
    foreach ($mode in @('throw','invalid','unexpected','report-failure')) {
        $script:Mode = $mode
        $script:MockExitCode = 19
        $result = Invoke-AppPreflight -ProjectRoot $script:Root
        Assert-Launcher ($result.Json -notlike '*synthetic-private*') 'Raw error leaked'
        Assert-Launcher ($result.Json -like '*PREFLIGHT_LAUNCH_OR_REPORT_FAILED*') 'Missing safe failure'
        $expectedCode = if ($mode -eq 'throw') { 2 } elseif ($mode -eq 'unexpected') { 1 } else { 19 }
        Assert-Launcher ($result.ExitCode -eq $expectedCode) 'Error exit code wrong'
        Assert-Cleanup
        $script:Cases++
    }
    $result = Invoke-AppPreflight -ProjectRoot (Join-Path $script:Root 'nonexistent-offline-fixture')
    Assert-Launcher ($result.ExitCode -eq 2) 'Missing Python must fail safely'
    $script:Cases++
    foreach ($mode in @('native-success','start-failure','timeout')) {
        $script:Mode = $mode
        $secret = ConvertTo-SecureString 'synthetic-password-only' -AsPlainText -Force
        $info = New-PreflightStartInfo -ProjectRoot $script:Root -Secret $secret
        $threw = $false
        try { $outcome = & $script:RealChildRunner -StartInfo $info } catch { $threw = $true }
        Assert-Launcher ($threw -eq ($mode -ne 'native-success')) 'Native mock result wrong'
        if (-not $threw) { Assert-Launcher ($outcome.ExitCode -eq 23) 'Native exit lost' }
        Assert-Launcher $script:FakeProcess.Disposed 'Process not disposed'
        Assert-Launcher (-not $info.EnvironmentVariables.ContainsKey('TEST_DB_PASSWORD')) 'Native cleanup retained password'
        if ($mode -eq 'timeout') { Assert-Launcher $script:FakeProcess.Killed 'Timed-out child not killed' }
        $secret.Dispose()
        $script:Cases++
    }
    $tokens = $null
    $parseErrors = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $script:Root 'scripts\run-batch-b-app-preflight.ps1'), [ref]$tokens, [ref]$parseErrors)
    Assert-Launcher ($parseErrors.Count -eq 0) 'PowerShell parse error'
    $script:Cases++
    Write-Output ($script:Cases.ToString() + ' Passed, 0 Failed, 0 Skipped; mocked child only; no database connection')
}
finally {
    Remove-Item Env:TEST_DB_PASSWORD, Env:TEST_MIGRATION_DB_PASSWORD -ErrorAction SilentlyContinue
    $env:JUKEBOX_RUN_FUTURE_WRITE_TEST = '0'
}
