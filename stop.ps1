$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $PSScriptRoot 'data/server.pid'
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Host 'No tracked local server.'; exit 0 }
$trackedPid = [int]([System.IO.File]::ReadAllText($pidFile).Trim())
$trackedProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $trackedPid"
if (-not $trackedProcess) { Write-Host 'Server is already stopped.'; exit 0 }
$expectedManage = Join-Path $PSScriptRoot 'manage.py'
if (-not $trackedProcess.CommandLine.Contains($expectedManage) -or -not $trackedProcess.CommandLine.Contains('runserver 127.0.0.1:8765')) {
    throw 'Process identity does not match this project. Nothing was stopped.'
}
Stop-Process -Id $trackedPid
Write-Host 'Local order tracker stopped.'
