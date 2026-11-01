@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
set "CLIPFORGE_FIX_ROOT=%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Положите этот файл в папку ClipForge рядом с Запустить ClipForge.cmd.
  goto fail
)
powershell.exe -NoLogo -NoProfile -Command "$folder=[IO.Path]::GetFullPath($env:CLIPFORGE_FIX_ROOT).TrimEnd([char]92)+'\.venv\'; $active=@(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object { $_.Name -in @('python.exe','pythonw.exe') -and $_.ExecutablePath -and $_.ExecutablePath.StartsWith($folder,[StringComparison]::OrdinalIgnoreCase) }); if($active.Count -gt 0){ Write-Host 'Сначала остановите ClipForge через Ctrl+C в окне запуска. Затем нажмите этот файл снова.'; exit 2 }"
if errorlevel 1 goto fail
echo Исправляем несовместимость аудиобиблиотеки: PyAV 19 -^> PyAV 18.
".venv\Scripts\python.exe" -m ensurepip --upgrade
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check "av==18.0.0"
if errorlevel 1 goto fail
echo Проверяем настоящее чтение WAV без загрузки моделей...
".venv\Scripts\python.exe" -c "import io,wave; from faster_whisper.audio import decode_audio; b=io.BytesIO(); w=wave.open(b,'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b'\0\0'*1600); w.close(); b.seek(0); a=decode_audio(b,sampling_rate=16000); assert len(a)==1600; print('OK: WAV decoding works')"
if errorlevel 1 goto fail
echo Исправление установлено. Видео, настройки и скачанные модели сохранены.
if exist "Запустить ClipForge.cmd" (
  call "%~dp0Запустить ClipForge.cmd"
  exit /b
)
echo Запустите ClipForge обычным способом.
pause
exit /b 0
:fail
echo Исправление не завершено. Скопируйте сообщение ошибки выше.
pause
exit /b 1
