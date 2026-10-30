# Dataviv AI OS - start the API (with the in-process background worker) and open the test console.
# Stop with Ctrl+C. Requires bootstrap.bat (backend) and frontend.bat (console build) to have run once.
#
# Port: 8010 by default (8000 is often taken by other local services). Set DATAVIV_PORT to choose another.
# If the port is busy, the next free one up to +20 is used, and the console URL printed below says which.
$ErrorActionPreference = 'Continue'
$root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$vpy = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $vpy)) { Write-Host 'No .venv yet: run scripts\windows\bootstrap.bat first.' -ForegroundColor Red; exit 1 }
if (-not (Test-Path (Join-Path $root 'frontend\dist\index.html'))) {
    Write-Host 'Console not built yet: run scripts\windows\frontend.bat first. Starting the API anyway (Swagger at /docs).' -ForegroundColor Yellow
}

function Get-PortOwner([int]$p) {
    $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $c) { return $null }
    $proc = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
    if ($proc) { return "$($proc.ProcessName) (pid $($c.OwningProcess))" }
    return "pid $($c.OwningProcess)"
}

$preferred = 8010
if ($env:DATAVIV_PORT) { $preferred = [int]$env:DATAVIV_PORT }
$port = $null
for ($p = $preferred; $p -le $preferred + 20; $p++) {
    $owner = Get-PortOwner $p
    if (-not $owner) { $port = $p; break }
    Write-Host "Port $p is in use by $owner - trying the next one." -ForegroundColor Yellow
}
if (-not $port) {
    Write-Host "No free port between $preferred and $($preferred + 20). Close something, or set DATAVIV_PORT." -ForegroundColor Red
    exit 1
}

Write-Host 'Refreshing seed data (idempotent)...' -ForegroundColor Yellow
& $vpy (Join-Path $root 'scripts\seed_dev.py') | Out-Null
$url = "http://127.0.0.1:$port/ui/"
# Open the browser only once the API answers /health, so the first page is never an error.
$opener = "for (`$i = 0; `$i -lt 60; `$i++) { try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://127.0.0.1:$port/health' | Out-Null; Start-Process '$url'; break } catch { Start-Sleep 1 } }"
Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile', '-Command', $opener
Write-Host ''
Write-Host "  Dataviv API      http://127.0.0.1:$port   (Swagger: /docs)" -ForegroundColor Green
Write-Host "  Test console     $url" -ForegroundColor Green
Write-Host '  Press Ctrl+C to stop.' -ForegroundColor Green
Write-Host ''
Set-Content -Path (Join-Path $root 'logs\console_url.txt') -Value $url -Encoding ascii -ErrorAction SilentlyContinue
Set-Location (Join-Path $root 'backend')
& $vpy -m uvicorn app.main:app --host 127.0.0.1 --port $port
