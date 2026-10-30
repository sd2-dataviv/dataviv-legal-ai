# Dataviv AI OS - full first run on Windows: setup -> database -> lint/types/tests.
# Every step's output is written to logs\ so it can be reviewed afterwards.
$ErrorActionPreference = 'Continue'
$root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
Set-Location $root
New-Item -ItemType Directory -Force logs | Out-Null
$status = Join-Path $root 'logs\bootstrap_status.txt'
"started $(Get-Date -Format s)" | Set-Content $status
function Mark($s) { "$s $(Get-Date -Format s)" | Add-Content $status; Write-Host "`n=== $s ===" -ForegroundColor Yellow }

Mark 'STEP 1/5 setup'
try { & "$PSScriptRoot\setup.ps1" *>&1 | Tee-Object -FilePath logs\01_setup.log; Mark 'setup OK' }
catch { $_ | Out-String | Add-Content logs\01_setup.log; Mark "setup FAILED: $($_.Exception.Message)" }

Mark 'STEP 2/5 database'
try { & "$PSScriptRoot\setup_db.ps1" *>&1 | Tee-Object -FilePath logs\02_database.log; Mark 'database OK' }
catch { $_ | Out-String | Add-Content logs\02_database.log; Mark "database FAILED: $($_.Exception.Message)" }

$vpy = Join-Path $root '.venv\Scripts\python.exe'
Push-Location backend
Mark 'STEP 3/5 format, lint + types'
& $vpy -m ruff format . *>&1 | Tee-Object -FilePath ..\logs\03_format_applied.log
& $vpy -m ruff check --fix . *>&1 | Tee-Object -FilePath ..\logs\03_fix_applied.log
& $vpy -m ruff check . *>&1 | Tee-Object -FilePath ..\logs\03_ruff.log
Mark "ruff exit=$LASTEXITCODE"
& $vpy -m ruff format --check . *>&1 | Tee-Object -FilePath ..\logs\03_ruff_format.log
Mark "ruff-format exit=$LASTEXITCODE"
& $vpy -m mypy app *>&1 | Tee-Object -FilePath ..\logs\03_mypy.log
if ($LASTEXITCODE -eq 2) {
    # exit 2 = mypy itself crashed (often a stale cache after upgrades): clear the cache, retry with a traceback
    Mark 'mypy crashed (exit 2): clearing .mypy_cache and retrying'
    Remove-Item -Recurse -Force .mypy_cache, ..\.mypy_cache -ErrorAction SilentlyContinue
    & $vpy -m mypy app --show-traceback *>&1 | Tee-Object -FilePath ..\logs\03_mypy.log
}
Mark "mypy exit=$LASTEXITCODE"

Mark 'STEP 4/5 tests'
& $vpy -m pytest -rA --tb=short --color=no *>&1 | Tee-Object -FilePath ..\logs\04_pytest.log
Mark "pytest exit=$LASTEXITCODE"
Pop-Location

Mark 'STEP 5/5 model provider check (one tiny call)'
& $vpy scripts\check_llm.py *>&1 | Tee-Object -FilePath logs\05_check_llm.log
Mark "llm-check exit=$LASTEXITCODE"
Mark 'FINISHED'
Write-Host "`nAll done. Logs are in $root\logs - you can close this window." -ForegroundColor Green
