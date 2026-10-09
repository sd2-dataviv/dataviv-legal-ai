# Dataviv AI OS - one-time developer setup on Windows (no Docker needed).
# Run from the repo root:  powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1
# Creates .venv in the repo root, installs backend + dev dependencies, creates backend\.env,
# installs the recommended VS Code extensions, and makes the first git commit if needed.
$ErrorActionPreference = 'Continue'   # native tools write notices to stderr; failures are checked via exit codes
$root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
Set-Location $root
$steps = 7; $n = 0
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

Step 'Smart App Control check (Windows blocks some unsigned compiled modules)'
$sac = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' -ErrorAction SilentlyContinue).VerifiedAndReputablePolicyState
if ($sac -in 1, 2) {
  Write-Host '  Smart App Control is on - switching affected packages to their pure-Python builds'
  $wheels = Join-Path $root '.venv\pure-wheels'
  New-Item -ItemType Directory -Force $wheels | Out-Null
  foreach ($pkg in 'sqlalchemy', 'mypy') {
    $v = (& $vpy -c "import importlib.metadata as m; print(m.version('$pkg'))").Trim()
    Write-Host "  - $pkg $v"
    & $vpy -m pip download "$pkg==$v" --no-deps --only-binary=:all: --platform any --implementation py --python-version $ver -d $wheels -q
    $whl = Get-ChildItem $wheels -Filter "*-$v-py3-none-any.whl" | Where-Object { $_.Name -like "$pkg*" -or $_.Name -like "$($pkg.Substring(0,1).ToUpper())$($pkg.Substring(1))*" } | Select-Object -First 1
    if (-not $whl) { $whl = Get-ChildItem $wheels -Filter "*$v-py3-none-any.whl" | Select-Object -First 1 }
    if ($whl) { & $vpy -m pip install --force-reinstall --no-deps $whl.FullName -q } else { Write-Host "    no pure-Python wheel found for $pkg $v" -ForegroundColor Red }
  }
  $blocked = & $vpy -c "import importlib
bad = []
for m in ('sqlalchemy', 'sqlalchemy.ext.asyncio', 'asyncpg', 'pydantic_core', 'greenlet', 'mypy.api', 'httptools', 'watchfiles', 'yaml'):
    try:
        importlib.import_module(m)
    except Exception as e:
        bad.append(m + ': ' + str(e)[:100])
print('\n'.join(bad) if bad else 'ALL OK')"
  Write-Host "  import check: $blocked"
} else { Write-Host '  Smart App Control is off - nothing to do' }

Step 'Creating backend\.env from .env.example'
if (-not (Test-Path 'backend\.env')) { Copy-Item 'backend\.env.example' 'backend\.env'; Write-Host '  created backend\.env' } else { Write-Host '  backend\.env already exists - left unchanged' }

Step 'Installing recommended VS Code extensions'
if (Get-Command code -ErrorAction SilentlyContinue) {
  $ext = (Get-Content '.vscode\extensions.json' -Raw | ConvertFrom-Json).recommendations
  $have = @(& code --list-extensions 2>$null) | ForEach-Object { $_.ToLower() }
  $missing = @($ext | Where-Object { $have -notcontains $_.ToLower() })
  if (-not $missing) { Write-Host '  all recommended extensions already installed' }
  $i = 0
  foreach ($e in $missing) { $i++; Write-Host ("  ({0}/{1}) {2}" -f $i, $missing.Count, $e); & code --install-extension $e 2>$null | Out-Null }
} else { Write-Host '  VS Code "code" command not on PATH - install extensions from the Extensions panel when prompted.' }

Step 'Git (skipped - configure later with: git config --global user.name / user.email)'

Write-Progress -Activity 'Dataviv AI OS setup' -Completed
Write-Host "`nSetup complete. Next: scripts\windows\setup_db.ps1 (database)" -ForegroundColor Green
