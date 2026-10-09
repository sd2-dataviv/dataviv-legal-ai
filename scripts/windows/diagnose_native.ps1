# Diagnose Windows Application Control (Smart App Control) blocking Python native modules.
# Read-only except for one step: ruff auto-fix + format of the project's own source files.
$ErrorActionPreference = 'Continue'
$root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
Set-Location $root
New-Item -ItemType Directory -Force logs | Out-Null
$log = Join-Path $root 'logs\05_diagnose.log'
"diagnose $(Get-Date -Format s)" | Set-Content $log
function Log($s) { $s | Add-Content $log; Write-Host $s }
$vpy = Join-Path $root '.venv\Scripts\python.exe'

Log '### Smart App Control state (0=Off, 1=On, 2=Evaluation)'
try { Log ((Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' -ErrorAction Stop).VerifiedAndReputablePolicyState) } catch { Log "unknown: $($_.Exception.Message)" }

Log '### Windows Defender Application Control / Code Integrity recent blocks (last 20)'
try {
  Get-WinEvent -LogName 'Microsoft-Windows-CodeIntegrity/Operational' -MaxEvents 200 -ErrorAction Stop |
    Where-Object { $_.Id -in 3033, 3034, 3076, 3077 } | Select-Object -First 20 |
    ForEach-Object { Log ("{0} id={1} {2}" -f $_.TimeCreated.ToString('s'), $_.Id, (($_.Message -split "`n")[0])) }
} catch { Log "not readable: $($_.Exception.Message)" }

Log '### Native module import test (OK / BLOCKED / other error)'
$mods = 'pydantic_core', 'asyncpg', 'greenlet', 'sqlalchemy', 'yaml', 'markupsafe', 'httptools', 'watchfiles', 'websockets', 'mypy', 'ruff'
$i = 0
foreach ($m in $mods) {
  $i++; Write-Progress -Activity 'Testing native modules' -Status $m -PercentComplete (100 * $i / $mods.Count)
  $r = & $vpy -c "import importlib,sys
try:
    importlib.import_module('$m'); print('OK')
except Exception as e:
    print(('BLOCKED ' if 'Application Control' in str(e) else 'ERROR ') + type(e).__name__ + ': ' + str(e)[:160])" 2>&1 | Out-String
  Log ("{0,-14} {1}" -f $m, $r.Trim())
}
$env:DISABLE_SQLALCHEMY_CEXT_RUNTIME = '1'
$r = & $vpy -c "import sqlalchemy; print('OK', sqlalchemy.__version__)" 2>&1 | Out-String
Log ("{0,-14} {1}" -f 'sqlalchemy(pure)', $r.Trim())
Remove-Item Env:DISABLE_SQLALCHEMY_CEXT_RUNTIME
Write-Progress -Activity 'Testing native modules' -Completed

Log '### ruff auto-fix + format (project files only)'
Push-Location backend
Log ((& $vpy -m ruff check --fix . 2>&1 | Out-String).Trim())
Log ((& $vpy -m ruff format . 2>&1 | Out-String).Trim())
Log ((& $vpy -m ruff check . 2>&1 | Out-String).Trim())
Pop-Location
Push-Location scripts
Log ((& $vpy -m ruff format --config ..\backend\pyproject.toml . 2>&1 | Out-String).Trim())
Pop-Location
Log 'DONE'
