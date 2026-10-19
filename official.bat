@echo off
rem Double-click to run: switch.ps1 official
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0switch.ps1" official %*
pause
