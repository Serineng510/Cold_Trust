<#
  Switch ColdTrust between DEMO and OFFICIAL (or stop everything).

    .\switch.ps1 demo       stop what is running, start the DEMO server (+ camera)
    .\switch.ps1 official   stop what is running, start the OFFICIAL server (+ camera)
    .\switch.ps1 stop       stop the server and the camera
    .\switch.ps1 status     show what is running right now

  Options:  -NoCamera   do not start the camera agent        -Camera 0   camera number (Logitech = 0)
  Easier:   double-click demo.bat / official.bat / stop.bat / status.bat

  DEMO     data/hardware.db, wiped at every start, mock Hold/Light chips work, Telegram as configured in .env
  OFFICIAL data/official.db, kept between runs, no mock (the server refuses it), never reset
#>
param(
    [Parameter(Mandatory = $true, Position = 0)][ValidateSet('demo', 'official', 'stop', 'status')][string]$Mode,
    [switch]$NoCamera,
    [int]$Camera = 0,
    [int]$Port = 8000
)

$root = $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\python.exe'
$url = "http://127.0.0.1:$Port"
if (-not (Test-Path $py)) { Write-Host "Python environment not found: $py" -ForegroundColor Red; exit 1 }

# Only this project's processes: the backend server and the camera agent.
$pattern = '-m backend(\s|$)|backend\\__main__\.py|-m camera\.camera_agent'
function Get-ColdTrustProcesses {
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -match $pattern }
}

function Show-Status {
    $procs = @(Get-ColdTrustProcesses)
    $server = $procs | Where-Object { $_.CommandLine -match 'backend' -and $_.CommandLine -notmatch 'camera' }
    $cam = $procs | Where-Object { $_.CommandLine -match 'camera\.camera_agent' }
    $mode = 'not running'
    try {
        $d = Invoke-RestMethod "$url/api/demo" -TimeoutSec 3
        $mode = if ($d.enabled) { 'DEMO (mock chips enabled)' } else { 'OFFICIAL / real readings only (mock disabled)' }
    } catch { }
    Write-Host ''
    Write-Host "  Server : $mode" -ForegroundColor Cyan
    $camText = 'not running'
    if ($cam) {
        $m = [regex]::Match(($cam | Select-Object -First 1).CommandLine, '--source\s+(\S+)')
        $camText = 'running' + $(if ($m.Success) { " (camera $($m.Groups[1].Value))" } else { '' })
    }
    Write-Host "  Camera : $camText" -ForegroundColor Cyan
    Write-Host "  Dashboard: $url" -ForegroundColor Cyan
    Write-Host ''
}

function Stop-ColdTrust {
    $procs = @(Get-ColdTrustProcesses)
    # also close the windows this script opened for them (titled "ColdTrust ...")
    $windows = @(Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" | Where-Object { $_.CommandLine -match 'title ColdTrust ' })
    foreach ($p in $windows + $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    # wait until the port is free so the next server can bind it
    for ($i = 0; $i -lt 20; $i++) {
        if (-not (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 500
    }
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        $owner = (Get-NetTCPConnection -LocalPort $Port -State Listen | Select-Object -First 1).OwningProcess
        Write-Host "Port $Port is still used by process $owner (not a ColdTrust process). Close it, then run this again." -ForegroundColor Red
        exit 1
    }
    Write-Host "Stopped $($procs.Count) ColdTrust process(es)." -ForegroundColor Green
}

function Start-Window([string]$title, [string]$arguments) {
    # a visible window per program, titled so you can tell DEMO from OFFICIAL at a glance
    $cmd = "title $title & cd /d `"$root`" & `"$py`" $arguments"
    Start-Process -FilePath 'cmd.exe' -ArgumentList @('/k', $cmd) -WindowStyle Normal | Out-Null
}

switch ($Mode) {
    'status' { Show-Status; exit 0 }
    'stop' { Stop-ColdTrust; Show-Status; exit 0 }
}

Stop-ColdTrust

if ($Mode -eq 'demo') {
    Write-Host 'Starting DEMO server (fresh database, mock chips on)...' -ForegroundColor Yellow
    Start-Window 'ColdTrust DEMO server' "-m backend --host 0.0.0.0 --port $Port --db data/hardware.db --reset --demo"
} else {
    Write-Host 'Starting OFFICIAL server (real readings only, database kept)...' -ForegroundColor Yellow
    Start-Window 'ColdTrust OFFICIAL server' "-m backend --host 0.0.0.0 --port $Port --db data/official.db"
}

$up = $false
for ($i = 0; $i -lt 40; $i++) {
    try { Invoke-RestMethod "$url/api/health" -TimeoutSec 2 | Out-Null; $up = $true; break } catch { Start-Sleep -Milliseconds 750 }
}
if (-not $up) {
    Write-Host "The server did not answer on $url. Read the server window for the error message." -ForegroundColor Red
    exit 1
}

if (-not $NoCamera) {
    Write-Host "Starting camera agent (camera $Camera)..." -ForegroundColor Yellow
    Start-Window "ColdTrust camera ($($Mode.ToUpper()))" "-m camera.camera_agent --url $url --source $Camera"
    Start-Sleep -Seconds 3
}

Show-Status
Write-Host "  Leave the new windows open. To stop: run stop.bat (or press Ctrl+C in each window)." -ForegroundColor Gray
