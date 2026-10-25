<#
  One-click demo for judges and anyone WITHOUT the hardware (Windows).

    judge_demo.bat                 plays the "spoof" scenario
    judge_demo.bat theft           or: spoof | theft | door_ajar | compressor_fail | authorised_stop | freeze |
                                       stopped_theft | replay | sensor_disagree | normal
    judge_demo.bat spoof -Port 8001   if port 8000 is already used

  What it does: sets up a Python environment the first time (4 small packages), starts a throwaway server with its own
  database (data/judge_demo.db, wiped every start, Telegram off, no login), plays the scenario instantly with the
  simulator, and opens the dashboard. Close the "ColdTrust judge demo" window to stop the server.
  It never touches the hardware demo (demo.bat), data/official.db or any server that is already running.
#>
param(
    [ValidateSet('spoof', 'theft', 'door_ajar', 'compressor_fail', 'authorised_stop', 'freeze', 'stopped_theft',
                 'replay', 'sensor_disagree', 'normal')][string]$Scenario = 'spoof',
    [int]$Port = 8000,
    [switch]$NoBrowser
)

$root = $PSScriptRoot
Set-Location $root          # the simulator and the server must both run from this folder, wherever the script was started from
$venv = Join-Path $root '.venv'
$py = Join-Path $venv 'Scripts\python.exe'
$url = "http://127.0.0.1:$Port"

# ---- 1. first run: create the environment (about 1 minute, needs internet)
if (-not (Test-Path $py)) {
    Write-Host 'First run: setting up Python (4 small packages, about a minute)...' -ForegroundColor Yellow
    $starter = $null
    foreach ($candidate in @(@('py', '-3'), @('python'))) {
        $exe = Get-Command $candidate[0] -ErrorAction SilentlyContinue
        if (-not $exe) { continue }
        $args = @($candidate | Select-Object -Skip 1) + @('--version')
        $out = & $exe.Source @args 2>&1 | Out-String
        if ($LASTEXITCODE -eq 0 -and $out -match 'Python 3\.(\d+)' -and [int]$Matches[1] -ge 10) { $starter = $candidate; break }
    }
    if (-not $starter) {
        Write-Host 'Python 3.10 or newer was not found. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run this again.' -ForegroundColor Red
        exit 1
    }
    $exe = (Get-Command $starter[0]).Source
    & $exe @($starter | Select-Object -Skip 1) -m venv $venv
    & $py -m pip install --quiet -r (Join-Path $root 'requirements-demo.txt')
    if ($LASTEXITCODE -ne 0) { Write-Host 'Installing the packages failed. Check your internet connection and run this again.' -ForegroundColor Red; exit 1 }
}

# ---- 2. the port must be free (we never stop anybody else's server)
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "Port $Port is already in use. Close that program, or run: judge_demo.bat $Scenario -Port 8001" -ForegroundColor Red
    exit 1
}

# ---- 3. start a throwaway server in its own window
Write-Host "Starting the demo server (scenario: $Scenario)..." -ForegroundColor Yellow
$cmd = "title ColdTrust judge demo (close this window to stop) & cd /d `"$root`" & `"$py`" -m backend --timelapse --reset --no-telegram --no-login --port $Port --db data/judge_demo.db"
Start-Process -FilePath 'cmd.exe' -ArgumentList @('/k', $cmd) -WindowStyle Normal | Out-Null

$up = $false
for ($i = 0; $i -lt 40; $i++) {
    try { Invoke-RestMethod "$url/api/health" -TimeoutSec 2 | Out-Null; $up = $true; break } catch { Start-Sleep -Milliseconds 750 }
}
if (-not $up) { Write-Host "The server did not start. Read the 'ColdTrust judge demo' window for the error." -ForegroundColor Red; exit 1 }

# ---- 4. play the scenario instantly (virtual time), then open the dashboard
& $py -m sim.sim $Scenario --timelapse --url $url
if (-not $NoBrowser) { Start-Process $url }

Write-Host ''
Write-Host "Dashboard: $url" -ForegroundColor Cyan
Write-Host 'Look at: Overview > Data trust card, the audit log, and the Approvals card.' -ForegroundColor Cyan
Write-Host 'To try another scenario, run judge_demo.bat again (it restarts with a clean database).' -ForegroundColor Gray
