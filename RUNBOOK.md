# Day runbook: demo run and official run

Same laptop, same board, same port 8000. One script switches between them and stops everything.
Run it from the `cold-chain` folder, or just double-click the `.bat` files.

| Want | Double-click | Or type in a terminal |
|---|---|---|
| Demo (presentation, mock chips) | `demo.bat` | `.\switch.ps1 demo` |
| Official (real readings only) | `official.bat` | `.\switch.ps1 official` |
| Stop everything | `stop.bat` | `.\switch.ps1 stop` |
| What is running now? | `status.bat` | `.\switch.ps1 status` |

Each of `demo` and `official` first stops whatever is running (server and camera), then starts the new server and the camera
agent in their own windows titled "ColdTrust DEMO server" / "ColdTrust OFFICIAL server". Open http://127.0.0.1:8000.
Options: `-NoCamera` (no camera agent), `-Camera 1` (another camera number). Camera 0 is the Logitech C310.

| | DEMO | OFFICIAL |
|---|---|---|
| Database | `data/hardware.db`, wiped at every start | `data/official.db`, kept between runs |
| Mock Hold / Light chips | work (mocked values are labelled) | refused by the server (HTTP 403) |
| Readings | real or mocked | real only |
| Server flags behind it | `--host 0.0.0.0 --db data/hardware.db --reset --demo` | `--host 0.0.0.0 --db data/official.db` |

## The day
1. Before: power the board. Run `demo.bat` (rehearsal) or `official.bat` (real run). Wait for **Live** on the dashboard.
2. Switch any time: run the other `.bat`. It stops the old server first, so nothing clashes. Refresh the dashboard (Ctrl+F5).
3. Demo: click the **Hold** chip / **Light** chip, tick Mock, move the slider. Theft story: mock light on with someone in
   front of the camera, truck away from a stop. Show the **Evidence** tab. Untick the mocks when done.
4. End of day: `stop.bat`.

## Rules
- Never use `--reset` with `data/official.db`, and never use `data/official.db` for demos (the script already keeps them apart).
- The vehicle, driver, packages and route are the built-in sample data in BOTH runs.
- The real DHT11 reads about 1.3 °C (below the 2 °C limit), so a real run raises freeze alerts; use a mock for demos.
- If something looks wrong, run `status.bat`. Two servers or two camera agents at once cause blank or flickering video.
- If a script says port 8000 is used by another program, close that program (it is not ColdTrust), then run again.

## Manual commands (what the script runs)
```
.venv\Scripts\python -m backend --host 0.0.0.0 --db data/hardware.db --reset --demo      (demo)
.venv\Scripts\python -m backend --host 0.0.0.0 --db data/official.db                     (official)
.venv\Scripts\python -m camera.camera_agent --url http://127.0.0.1:8000 --source 0       (camera)
```
