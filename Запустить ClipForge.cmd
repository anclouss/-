@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows-launcher.ps1"
if errorlevel 1 (
  echo.
  echo ClipForge failed to start. See the error above and README.md.
  pause
  exit /b 1
)
endlocal
