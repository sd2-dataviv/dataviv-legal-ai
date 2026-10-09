# Dataviv AI OS - read-only requirements check.
# Changes nothing on the system. Writes requirements_report.txt next to this script.
$ErrorActionPreference = 'Continue'
$out = Join-Path $PSScriptRoot 'requirements_report.txt'
$lines = New-Object System.Collections.Generic.List[string]

$checks = [ordered]@{
  'Windows'                = { Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, OSArchitecture | Format-List | Out-String }
  'CPU'                    = { Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors | Format-List | Out-String }
  'RAM (GB)'               = { [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1) }
  'Disk C: free (GB)'      = { [math]::Round((Get-PSDrive C).Free / 1GB, 1) }
  'GPU'                    = { Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, AdapterRAM | Format-List | Out-String }
  'nvidia-smi'             = { nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv }
  'Virtualisation in BIOS' = { (Get-CimInstance Win32_Processor).VirtualizationFirmwareEnabled }
  'WSL status'             = { wsl --status }
  'py launcher (all Pythons)' = { py -0p }
  'python on PATH'         = { python --version; (Get-Command python -ErrorAction Stop).Source }
  'Python 3.12'            = { py -3.12 --version }
  'Python 3.13'            = { py -3.13 --version }
  'python -> real path'    = { python -c "import sys, platform; print(sys.executable); print(platform.python_version(), platform.architecture()[0])" }
  'pip'                    = { python -m pip --version }
  'git'                    = { git --version }
  'docker'                 = { docker --version }
  'docker compose'         = { docker compose version }
  'docker engine running'  = { docker info --format '{{.ServerVersion}} | {{.OperatingSystem}} | CPUs {{.NCPU}} | Mem {{.MemTotal}}' }
  'node'                   = { node --version }
  'npm'                    = { npm --version }
  'VS Code'                = { code --version }
  'VS Code extensions'     = { code --list-extensions }
  'make'                   = { make --version }
  'psql'                   = { psql --version }
  'PostgreSQL installs'    = { Get-ChildItem 'C:\Program Files\PostgreSQL' -ErrorAction Stop | ForEach-Object { $_.FullName } }
  'PostgreSQL services'    = { Get-Service -Name 'postgresql*' -ErrorAction Stop | Format-Table Name, Status, StartType -AutoSize | Out-String }
  'git user'               = { git config --global user.name; git config --global user.email }
  'Port 5432 listening'    = { Get-NetTCPConnection -LocalPort 5432 -State Listen -ErrorAction Stop | ForEach-Object { "$($_.LocalAddress) pid $($_.OwningProcess) $((Get-Process -Id $_.OwningProcess).ProcessName)" } }
  'Port 8000 listening'    = { Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction Stop | ForEach-Object { "$($_.LocalAddress) pid $($_.OwningProcess) $((Get-Process -Id $_.OwningProcess).ProcessName)" } }
  'PyPI reachable'         = { (Invoke-WebRequest 'https://pypi.org/simple/fastapi/' -UseBasicParsing -TimeoutSec 15).StatusCode }
  'Docker Hub reachable'   = { try { (Invoke-WebRequest 'https://registry-1.docker.io/v2/' -UseBasicParsing -TimeoutSec 15).StatusCode } catch { if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { throw } } }
  'Execution policy'       = { Get-ExecutionPolicy -List | Out-String }
}

$i = 0; $n = $checks.Count
foreach ($k in $checks.Keys) {
  $i++
  Write-Progress -Activity 'Dataviv AI OS requirements check' -Status $k -PercentComplete (100 * $i / $n)
  Write-Host ("[{0,2}/{1}] {2}" -f $i, $n, $k)
  try {
    $v = (& $checks[$k] 2>&1 | Out-String).Trim()
    if (-not $v) { $v = '(no output)' }
  } catch {
    $v = 'NOT AVAILABLE: ' + $_.Exception.Message
  }
  $lines.Add("### $k"); $lines.Add($v); $lines.Add('')
}
Write-Progress -Activity 'Dataviv AI OS requirements check' -Completed
$lines | Set-Content -Encoding UTF8 $out
Write-Host ''
Write-Host "Done. Report saved to $out" -ForegroundColor Green
