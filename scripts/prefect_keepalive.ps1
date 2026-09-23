#Requires -Version 5.1
<#
.SYNOPSIS
  Durable supervisor for Prefect API server + ingest_odds serve + default-pool worker.

.DESCRIPTION
  Ensures:
    1) `prefect server start --host 127.0.0.1` is healthy on :4200
    2) `python -m ncaa_quant.pipelines.odds` (serve_ingest_odds) is running
    3) `prefect worker start --pool default` is running (odds_cadence_watchdog)

  Does NOT start serve_all / predict_publish / postgame / weekly / settle.

  Intended as a forever-loop under Windows Task Scheduler so reboot, sleep
  wake, and process death recover without manual intervention.
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [int]$PollSeconds = 15,
    [switch]$Once
)

if (-not $RepoRoot) {
    $here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
    $RepoRoot = Split-Path -Parent $here
}

$ErrorActionPreference = "Stop"
$LogDir = Join-Path $RepoRoot "data\pipeline_state\prefect_logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-KeepAliveLog {
    param([string]$Message)
    $line = "{0:o} {1}" -f [DateTimeOffset]::UtcNow, $Message
    Add-Content -Path (Join-Path $LogDir "keepalive.log") -Value $line
    Write-Host $line
}

function Get-UvPath {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $fallback = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
    if (Test-Path $fallback) { return $fallback }
    throw "uv not found on PATH"
}

function Test-PrefectApiHealthy {
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:4200/api/health" -UseBasicParsing -TimeoutSec 3
        return ($resp.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Get-PrefectServerPids {
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Where-Object {
            $_.CommandLine -and (
                $_.CommandLine -match 'prefect(\.exe)?(\s+".*")?\s+server\s+start' -or
                $_.CommandLine -match 'prefect\.server'
            )
        } |
        Select-Object -ExpandProperty ProcessId
}

function Get-IngestOddsWorkerPids {
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Where-Object {
            $_.CommandLine -and (
                $_.CommandLine -match 'ncaa_quant\.pipelines\.odds' -or
                $_.CommandLine -match 'serve_ingest_odds'
            )
        } |
        Select-Object -ExpandProperty ProcessId
}

function Get-DefaultPoolWorkerPids {
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Where-Object {
            $_.CommandLine -and (
                $_.CommandLine -match 'worker\s+start' -and
                $_.CommandLine -match '--pool\s+default|pool[`"'']?default'
            )
        } |
        Select-Object -ExpandProperty ProcessId
}

function Start-PrefectServer {
    $uv = Get-UvPath
    $out = Join-Path $LogDir "server.out.log"
    $err = Join-Path $LogDir "server.err.log"
    Write-KeepAliveLog "starting prefect server"
    $args = @(
        "run", "--directory", $RepoRoot,
        "prefect", "server", "start", "--host", "127.0.0.1"
    )
    Start-Process -FilePath $uv -ArgumentList $args `
        -WorkingDirectory $RepoRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $out `
        -RedirectStandardError $err |
        Out-Null
}

function Start-IngestOddsWorker {
    $uv = Get-UvPath
    $out = Join-Path $LogDir "ingest_odds_worker.out.log"
    $err = Join-Path $LogDir "ingest_odds_worker.err.log"
    Write-KeepAliveLog "starting ingest_odds worker (serve_ingest_odds only; no predict_publish)"
    $env:PREFECT_API_URL = "http://127.0.0.1:4200/api"
    $args = @(
        "run", "--directory", $RepoRoot,
        "python", "-m", "ncaa_quant.pipelines.odds"
    )
    Start-Process -FilePath $uv -ArgumentList $args `
        -WorkingDirectory $RepoRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $out `
        -RedirectStandardError $err |
        Out-Null
}

function Start-DefaultPoolWorker {
    $uv = Get-UvPath
    $out = Join-Path $LogDir "default_pool_worker.out.log"
    $err = Join-Path $LogDir "default_pool_worker.err.log"
    Write-KeepAliveLog "starting prefect worker for work pool 'default' (cadence watchdog)"
    $env:PREFECT_API_URL = "http://127.0.0.1:4200/api"
    $args = @(
        "run", "--directory", $RepoRoot,
        "prefect", "worker", "start", "--pool", "default"
    )
    Start-Process -FilePath $uv -ArgumentList $args `
        -WorkingDirectory $RepoRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $out `
        -RedirectStandardError $err |
        Out-Null
}

function Wait-PrefectApi {
    param([int]$TimeoutSec = 90)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-PrefectApiHealthy) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Ensure-Once {
    if (-not (Test-PrefectApiHealthy)) {
        $serverPids = @(Get-PrefectServerPids)
        if ($serverPids.Count -eq 0) {
            Start-PrefectServer
        } else {
            Write-KeepAliveLog "server process present (pids=$($serverPids -join ',')) but /api/health not ready"
        }
        if (-not (Wait-PrefectApi -TimeoutSec 90)) {
            Write-KeepAliveLog "ERROR: prefect API not healthy after start attempt"
            return $false
        }
        Write-KeepAliveLog "prefect API healthy"
    } else {
        Write-KeepAliveLog "prefect API already healthy"
    }

    $workerPids = @(Get-IngestOddsWorkerPids)
    if ($workerPids.Count -eq 0) {
        Start-IngestOddsWorker
        Start-Sleep -Seconds 5
        $workerPids = @(Get-IngestOddsWorkerPids)
        if ($workerPids.Count -eq 0) {
            Write-KeepAliveLog "ERROR: ingest_odds worker failed to stay up"
            return $false
        }
        Write-KeepAliveLog "ingest_odds worker started pids=$($workerPids -join ',')"
    } else {
        Write-KeepAliveLog "ingest_odds worker already running pids=$($workerPids -join ',')"
    }

    $poolPids = @(Get-DefaultPoolWorkerPids)
    if ($poolPids.Count -eq 0) {
        Start-DefaultPoolWorker
        Start-Sleep -Seconds 5
        $poolPids = @(Get-DefaultPoolWorkerPids)
        if ($poolPids.Count -eq 0) {
            Write-KeepAliveLog "ERROR: default pool worker failed to stay up"
            return $false
        }
        Write-KeepAliveLog "default pool worker started pids=$($poolPids -join ',')"
    } else {
        Write-KeepAliveLog "default pool worker already running pids=$($poolPids -join ',')"
    }
    return $true
}

Set-Location $RepoRoot
$env:PREFECT_API_URL = "http://127.0.0.1:4200/api"
Write-KeepAliveLog "keepalive start repo=$RepoRoot poll=${PollSeconds}s once=$Once"

if ($Once) {
    $ok = Ensure-Once
    if (-not $ok) { exit 1 }
    exit 0
}

while ($true) {
    try {
        [void](Ensure-Once)
    } catch {
        Write-KeepAliveLog "ERROR: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds $PollSeconds
}
