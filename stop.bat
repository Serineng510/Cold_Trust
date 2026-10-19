@echo off
rem Double-click to run: switch.ps1 stop
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0switch.ps1" stop %*
pause
