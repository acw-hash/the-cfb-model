#!/usr/bin/env pwsh
# ATS-GRADE-FIX v2 reruns (market-aware, A3, A6) — sequential, logs wall clocks.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot/..
$logDir = "docs/notes/_artifacts/ats_grade_fix"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$runs = @(
  @{ cfg = "task23_market_aware_full_reduced_v2"; stack = "market_aware" },
  @{ cfg = "task23_A3_market_features_off_reduced_v2"; stack = "market_aware" },
  @{ cfg = "task23_A6_cfbd_open_close_reduced_v2"; stack = "market_aware" }
)
$results = @()
foreach ($r in $runs) {
  $t0 = Get-Date
  Write-Host "=== START $($r.cfg) $($t0.ToString('o')) ==="
  uv run ncaa-quant backtest run --config $($r.cfg) --stack $($r.stack) --label "ensemble_scope=REDUCED_PER_ADR_0013;grade_fix=v2"
  if ($LASTEXITCODE -ne 0) {
    Write-Error "backtest failed for $($r.cfg) exit=$LASTEXITCODE"
    exit $LASTEXITCODE
  }
  $elapsed = (Get-Date) - $t0
  $sec = [math]::Round($elapsed.TotalSeconds, 1)
  Write-Host "=== DONE $($r.cfg) wall_clock_sec=$sec ==="
  $results += [ordered]@{ config = $r.cfg; wall_clock_sec = $sec; finished_at = (Get-Date).ToString("o") }
}
$results | ConvertTo-Json -Depth 3 | Set-Content "$logDir/v2_wall_clocks.json" -Encoding utf8
