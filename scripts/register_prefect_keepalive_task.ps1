#Requires -Version 5.1
<#
.SYNOPSIS
  Register / update the Windows Task Scheduler job for Prefect keep-alive.

.DESCRIPTION
  Creates task NCAAQuant-PrefectKeepAlive that:
    - Runs scripts/prefect_keepalive.ps1 (forever loop; ingest_odds only)
    - Triggers at user logon and every 5 minutes (skipped if already running)
    - Restarts on failure every 1 minute (up to 999 times)
    - Allows start when on battery; does not stop on battery
    - WakeToRun enabled so sleep can yield a recovery pass

  Does not register predict_publish.
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$TaskName = "NCAAQuant-PrefectKeepAlive"
)

if (-not $RepoRoot) {
    $here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
    $RepoRoot = Split-Path -Parent $here
}

$ErrorActionPreference = "Stop"
$scriptPath = Join-Path $RepoRoot "scripts\prefect_keepalive.ps1"
if (-not (Test-Path $scriptPath)) {
    throw "Missing keepalive script: $scriptPath"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`"" `
    -WorkingDirectory $RepoRoot

$triggerLogon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$triggerRepeat = New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 0) `
    -MultipleInstances IgnoreNew `
    -WakeToRun

# Compatibility: some hosts reject -ExecutionTimeLimit 0; clear via CIM later if needed.
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger @($triggerLogon, $triggerRepeat) `
    -Settings $settings `
    -Principal $principal `
    -Description "Keep Prefect API (:4200) + ingest_odds worker alive. No predict_publish. Survives reboot/sleep via logon + 5-min watchdog + RestartOnFailure." `
    -Force | Out-Null

# Ensure unlimited run time where supported
try {
    $task = Get-ScheduledTask -TaskName $TaskName
    $task.Settings.ExecutionTimeLimit = "PT0S"
    $task.Settings.DisallowStartIfOnBatteries = $false
    $task.Settings.StopIfGoingOnBatteries = $false
    $task.Settings.WakeToRun = $true
    Set-ScheduledTask -InputObject $task | Out-Null
} catch {
    Write-Warning "Could not patch ExecutionTimeLimit/WakeToRun: $($_.Exception.Message)"
}

$info = Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo
Write-Host "Registered task: $TaskName"
Write-Host "State: $((Get-ScheduledTask -TaskName $TaskName).State)"
Write-Host "LastResult: $($info.LastTaskResult)"
Write-Host "Script: $scriptPath"
Write-Host "Triggers: AtLogOn + every 5 minutes (IgnoreNew if already running)"
Write-Host "RestartOnFailure: every 1 minute, count 999"
Write-Host "Re-attach: keepalive waits for http://127.0.0.1:4200/api/health then starts python -m ncaa_quant.pipelines.odds with PREFECT_API_URL=http://127.0.0.1:4200/api"
