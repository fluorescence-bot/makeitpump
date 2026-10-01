@echo off
rem Windows launcher for run.ps1 - double-click this, or run "run.cmd [--clean]".
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
set "rc=%errorlevel%"
if not "%rc%"=="0" pause
exit /b %rc%
