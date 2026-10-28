$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$projectPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
$portablePython = Join-Path $PSScriptRoot '.runtime/python.exe'
$workspacePython = Join-Path $PSScriptRoot '../../work/venv/Scripts/python.exe'
if (Test-Path -LiteralPath $portablePython) {
    $runner = $portablePython
} elseif (Test-Path -LiteralPath $projectPython) {
    $runner = $projectPython
} elseif (Test-Path -LiteralPath $workspacePython) {
    $runner = (Resolve-Path -LiteralPath $workspacePython).Path
} else {
    $bundledPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
    if (Test-Path -LiteralPath $bundledPython) { $bootstrap = $bundledPython } else { $bootstrap = 'python' }
    & $bootstrap -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.10+ is required.' }
    $runner = $projectPython
    $dependencyFile = if (Test-Path -LiteralPath 'requirements.lock') { 'requirements.lock' } else { 'requirements.txt' }
    & $runner -m pip install -r $dependencyFile
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
& $runner restore_data.py --if-needed
if ($LASTEXITCODE -ne 0) { throw 'Transfer snapshot restore failed; startup was stopped.' }
& $runner manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
& $runner manage.py seed_demo
if ($LASTEXITCODE -ne 0) { throw 'Demo initialization failed.' }
Write-Host 'Open http://127.0.0.1:8765/ in your browser.'
Write-Host 'Local demo account passwords: data/demo-accounts.txt'
$loginUrl = 'http://127.0.0.1:8765/login/'
function Test-OrderServer {
    try {
        $response = Invoke-WebRequest -Uri $loginUrl -UseBasicParsing -TimeoutSec 2
        return ($response.StatusCode -eq 200 -and $response.Content.Contains('id_username') -and $response.Content.Contains('/static/app.css'))
    } catch { return $false }
}
function Save-OrderServerPid {
    $listener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction Stop | Select-Object -First 1
    $activeServer = Get-CimInstance Win32_Process -Filter ("ProcessId = " + $listener.OwningProcess)
    $expectedManage = Join-Path $PSScriptRoot 'manage.py'
    if (-not $activeServer -or -not $activeServer.CommandLine.Contains($expectedManage) -or -not $activeServer.CommandLine.Contains('runserver 127.0.0.1:8765')) {
        throw 'Port owner does not match this project; server identity was not saved.'
    }
    # Windows venv launchers can spawn a child that owns the actual listener.
    $activeServer.ProcessId | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'data/server.pid') -Encoding ascii
}
if (Test-OrderServer) {
    Save-OrderServerPid
    Write-Host 'Order tracker is already running.'
    exit 0
}
if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Port 8765 is in use by a different service; nothing was stopped.'
}
$manageScript = Join-Path $PSScriptRoot 'manage.py'
$serverArgs = @(('"{0}"' -f $manageScript), 'runserver', '127.0.0.1:8765', '--noreload', '--insecure')
$logDirectory = Join-Path $PSScriptRoot 'data'
$serverProcess = Start-Process -FilePath $runner -ArgumentList $serverArgs -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory 'server.stdout.log') -RedirectStandardError (Join-Path $logDirectory 'server.stderr.log') -PassThru
$serverProcess.Id | Set-Content -LiteralPath (Join-Path $logDirectory 'server.pid') -Encoding ascii
for ($attempt = 0; $attempt -lt 12; $attempt++) {
    Start-Sleep -Milliseconds 300
    if (Test-OrderServer) {
        Save-OrderServerPid
        Write-Host 'Ready. The server continues in the background after this window closes.'
        Write-Host 'Use stop.ps1 to stop the local server. Only 127.0.0.1 is exposed.'
        exit 0
    }
    $serverProcess.Refresh()
    if ($serverProcess.HasExited) { break }
}
throw 'Server did not become ready. See data/server.stderr.log.'
