@echo off
rem Double-click for a demo WITHOUT hardware. Or run:  judge_demo.bat theft
rem Scenarios: spoof (default) | theft | door_ajar | compressor_fail | authorised_stop | freeze | stopped_theft | replay | sensor_disagree | normal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0judge_demo.ps1" %*
pause
