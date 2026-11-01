@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Start ClipForge once before running this diagnostic.
  pause
  exit /b 1
)
if "%~1"=="" (
  ".venv\Scripts\python.exe" -u "scripts\check-whisper.py"
) else (
  ".venv\Scripts\python.exe" -u "scripts\check-whisper.py" --audio "%~1"
)
echo.
echo Copy the last OK / FAIL / error message if you need help.
pause
endlocal
