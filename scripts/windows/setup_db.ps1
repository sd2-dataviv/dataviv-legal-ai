# Dataviv AI OS - prepare a NATIVE PostgreSQL 16 on Windows (no Docker).
# Run from the repo root:  powershell -ExecutionPolicy Bypass -File scripts\windows\setup_db.ps1
# Creates roles `dataviv` (schema owner) and `app_user` (what the API connects as, so RLS applies),
# databases `dataviv` and `dataviv_test`, applies migrations, and seeds dev data. Safe to re-run.
$ErrorActionPreference = 'Continue'   # native tools write notices to stderr; failures are checked via exit codes
$root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
Set-Location $root
$steps = 6; $n = 0
function Step($msg) { $script:n++; Write-Progress -Activity 'Database setup' -Status $msg -PercentComplete (100 * $script:n / $steps); Write-Host "`n[$script:n/$steps] $msg" -ForegroundColor Cyan }

Step 'Finding PostgreSQL'
$psql = Get-ChildItem 'C:\Program Files\PostgreSQL\*\bin\psql.exe' -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
if (-not $psql) { throw 'PostgreSQL not found. Install it:  winget install PostgreSQL.PostgreSQL.16 --interactive' }
Write-Host "  using $($psql.FullName)"
$svc = Get-Service -Name 'postgresql*' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($svc -and $svc.Status -ne 'Running') { throw "Service $($svc.Name) is $($svc.Status). Start it from Services (services.msc)." }

Step 'Checking for existing dev databases'
$env:PGPASSWORD = 'dataviv_dev_only'
$null = & $psql.FullName -h localhost -U dataviv -d dataviv_test -t -A -c 'SELECT 1' 2>&1
$ready = ($LASTEXITCODE -eq 0)
Remove-Item Env:PGPASSWORD
if ($ready) {
  Write-Host '  roles and databases already exist - skipping superuser steps (no password needed)'
  $n += 2
} else {
  Step 'Connecting as the postgres superuser'
  $sec = Read-Host '  Password for the "postgres" user (set during PostgreSQL install)' -AsSecureString
  $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
  function Psql([string]$db, [string]$sql) {
    $ErrorActionPreference = 'Continue'   # psql NOTICEs go to stderr; judge by exit code instead
    $out = & $psql.FullName -h localhost -U postgres -d $db -v ON_ERROR_STOP=1 -q -t -A -c $sql 2>&1
    if ($LASTEXITCODE -ne 0) { throw "psql failed: $out" }
    return $out
  }
  Psql 'postgres' 'SELECT 1' | Out-Null
  Write-Host '  connected'

  Step 'Creating roles and databases'
  $roleSql = @"
DO `$`$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dataviv') THEN CREATE ROLE dataviv LOGIN PASSWORD 'dataviv_dev_only'; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN CREATE ROLE app_user LOGIN PASSWORD 'app_user_dev_only'; END IF;
END `$`$;
"@
  Psql 'postgres' $roleSql | Out-Null
  foreach ($db in 'dataviv', 'dataviv_test') {
    $exists = Psql 'postgres' "SELECT 1 FROM pg_database WHERE datname = '$db'"
    if (-not $exists) { Psql 'postgres' "CREATE DATABASE $db OWNER dataviv" | Out-Null; Write-Host "  created database $db" } else { Write-Host "  database $db already exists" }
  }
  Remove-Item Env:PGPASSWORD

}

Step 'Applying migrations to dataviv'
$vpy = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $vpy)) { throw 'Run scripts\windows\setup.ps1 first (no .venv found).' }
Push-Location backend
& $vpy -m alembic upgrade head
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { throw 'Migration failed - see above.' }

Step 'Seeding dev firm, users and matters'
& $vpy scripts\seed_dev.py
Write-Progress -Activity 'Database setup' -Completed
Write-Host "`nDatabase ready. In VS Code press F5 ('API: FastAPI') and open http://127.0.0.1:8010/docs" -ForegroundColor Green
