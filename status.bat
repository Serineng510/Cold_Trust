@echo off
rem Shows status and keeps the window open
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0switch.ps1" status
pause
