# Dataviv AI OS - one-time developer setup on Windows (no Docker needed).
# Run from the repo root:  powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1
# Creates .venv in the repo root, installs backend + dev dependencies, creates backend\.env,
# installs the recommended VS Code extensions, and makes the first git commit if needed.
$ErrorActionPreference = 'Continue'   # native tools write notices to stderr; failures are checked via exit codes
$root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
Set-Location $root
$steps = 6; $n = 0
function Step($msg) { $script:n++; Write-Progress -Activity 'Dataviv AI OS setup' -Status $msg -PercentComplete (100 * $script:n / $steps); Write-Host "`n[$script:n/$steps] $msg" -ForegroundColor Cyan }

Step 'Checking Python (3.12 or newer)'
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py -or $py -like '*WindowsApps*') { throw 'Python not found on PATH. Install from python.org (tick "Add to PATH") and reopen this window.' }
$ver = & python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$ver -lt [version]'3.12') { throw "Python $ver found; 3.12+ is required." }
Write-Host "  Python $ver at $py"

Step 'Creating virtual environment .venv'
if (-not (Test-Path '.venv\Scripts\python.exe')) { & python -m venv .venv } else { Write-Host '  .venv already exists - reusing it' }
$vpy = Join-Path $root '.venv\Scripts\python.exe'

Step 'Installing backend and dev dependencies (a few minutes on first run)'
& $vpy -m pip install --upgrade pip --progress-bar on
& $vpy -m pip install -e ".\backend[dev]" --progress-bar on
if ($LASTEXITCODE -ne 0) { throw 'pip install failed - see the messages above.' }

Step 'Creating backend\.env from .env.example'
if (-not (Test-Path 'backend\.env')) { Copy-Item 'backend\.env.example' 'backend\.env'; Write-Host '  created backend\.env' } else { Write-Host '  backend\.env already exists - left unchanged' }

Step 'Installing recommended VS Code extensions'
if (Get-Command code -ErrorAction SilentlyContinue) {
  $ext = (Get-Content '.vscode\extensions.json' -Raw | ConvertFrom-Json).recommendations
  $i = 0
  foreach ($e in $ext) { $i++; Write-Host ("  ({0}/{1}) {2}" -f $i, $ext.Count, $e); & code --install-extension $e --force | Out-Null }
} else { Write-Host '  VS Code "code" command not on PATH - install extensions from the Extensions panel when prompted.' }

Step 'Git identity and repository'
$name = (& git config --global user.name)
if (-not $name) {
  & git config --global user.name (Read-Host '  Your name for git commits')
  & git config --global user.email (Read-Host '  Your email for git commits')
} else { Write-Host "  git user: $name" }
if (-not (Test-Path '.git')) {
  & git init -q -b main; & git add -A; & git commit -q -m 'Initial import'
  Write-Host '  initialised repo and made the first commit'
} else { Write-Host "  repo present: $(& git log --oneline -1)" }

Write-Progress -Activity 'Dataviv AI OS setup' -Completed
Write-Host "`nSetup complete. Next: install PostgreSQL 16, then run scripts\windows\setup_db.ps1" -ForegroundColor Green
