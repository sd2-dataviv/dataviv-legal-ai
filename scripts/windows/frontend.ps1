# Dataviv AI OS - install and build the React test console (frontend\). Logs go to logs\.
# After a successful build FastAPI serves the console at /ui (console.bat starts it, on port 8010 by default)
$ErrorActionPreference = 'Continue'
$root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
Set-Location $root
New-Item -ItemType Directory -Force logs | Out-Null
$status = Join-Path $root 'logs\frontend_status.txt'
"started $(Get-Date -Format s)" | Set-Content $status
function Mark($s) { "$s $(Get-Date -Format s)" | Add-Content $status; Write-Host "`n=== $s ===" -ForegroundColor Yellow }

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Mark 'npm not found: install Node.js 20+ from nodejs.org, reopen this window and run again'
    exit 1
}
Mark "node $(node -v), npm $(npm -v)"

Push-Location frontend
Mark 'STEP 1/3 npm install (first run downloads about 40 MB)'
npm install --no-audit --no-fund *>&1 | Tee-Object -FilePath ..\logs\10_npm_install.log
$installExit = $LASTEXITCODE
Mark "npm install exit=$installExit"

Mark 'STEP 2/3 type check'
npm run typecheck *>&1 | Tee-Object -FilePath ..\logs\11_typecheck.log
Mark "typecheck exit=$LASTEXITCODE"

Mark 'STEP 3/3 build'
npm run build *>&1 | Tee-Object -FilePath ..\logs\12_build.log
$buildExit = $LASTEXITCODE
Mark "build exit=$buildExit"
Pop-Location

# Smart App Control can block the unsigned native binaries Vite relies on (esbuild.exe, rollup .node).
$logs = (Get-Content logs\10_npm_install.log, logs\12_build.log -Raw -ErrorAction SilentlyContinue) -join "`n"
if ($buildExit -ne 0 -and $logs -match '(esbuild|rollup).*(blocked|not allowed|denied|EPERM|0x800711C7|Application Control)') {
    Mark 'BLOCKED: Windows Smart App Control stopped esbuild/rollup. Security settings were not changed. See logs\12_build.log'
}
if ($buildExit -eq 0 -and (Test-Path frontend\dist\index.html)) {
    Mark 'FINISHED: console built. Run scripts\windows\console.bat, it opens the console in your browser (port 8010 by default)'
} else {
    Mark 'FINISHED WITH ERRORS: see logs\10_npm_install.log, 11_typecheck.log and 12_build.log'
}
