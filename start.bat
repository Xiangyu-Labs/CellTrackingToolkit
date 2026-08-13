@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap.ps1" %*
set "CELLTRACK_STATUS=%ERRORLEVEL%"

if not "%CELLTRACK_STATUS%"=="0" (
  echo.
  pause
)

exit /b %CELLTRACK_STATUS%
