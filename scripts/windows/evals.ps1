# Dataviv AI OS - run the benchmark (Assistant answers + review-table extraction) and gate the release.
# Uses the TEST database, never your dev data. With Groq's developer tier this takes about 5-10 minutes
# because of the 8K tokens-per-minute limit; progress is shown as it goes. Report: logs\evals\eval-*.md
param([string]$Suite = 'all', [int]$Limit = 0, [string]$Backend = '')
$ErrorActionPreference = 'Continue'
$root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$vpy = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $vpy)) { Write-Host 'No .venv yet: run scripts\windows\bootstrap.bat first.' -ForegroundColor Red; exit 1 }
New-Item -ItemType Directory -Force (Join-Path $root 'logs') | Out-Null
$args2 = @('-m', 'evals.run', '--suite', $Suite)
if ($Limit -gt 0) { $args2 += @('--limit', "$Limit") }
if ($Backend) { $args2 += @('--backend', $Backend) }
Push-Location (Join-Path $root 'backend')
& $vpy @args2 *>&1 | Tee-Object -FilePath (Join-Path $root 'logs\30_evals.log')
$code = $LASTEXITCODE
Pop-Location
if ($code -eq 0) { Write-Host "`nBenchmark PASSED. Report in logs\evals\" -ForegroundColor Green }
else { Write-Host "`nBenchmark FAILED a gate (or could not run). See logs\30_evals.log and logs\evals\" -ForegroundColor Yellow }
exit $code
