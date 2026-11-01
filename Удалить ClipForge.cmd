@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "CLIPFORGE_REMOVE_ROOT=%~dp0"
set "CLIPFORGE_REMOVE_SELF=%~f0"
cd /d "%TEMP%"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$text=[IO.File]::ReadAllText($env:CLIPFORGE_REMOVE_SELF,[Text.Encoding]::UTF8); $marker='# CLIPFORGE_REMOVE_PAYLOAD'; $body=$text.Substring($text.LastIndexOf($marker)+$marker.Length); $temp=Join-Path ([IO.Path]::GetTempPath()) ('clipforge-remove-'+[Guid]::NewGuid().ToString('N')+'.ps1'); [IO.File]::WriteAllText($temp,$body,(New-Object Text.UTF8Encoding($true))); Start-Process powershell.exe -WorkingDirectory ([IO.Path]::GetTempPath()) -ArgumentList ('-NoLogo -NoProfile -ExecutionPolicy Bypass -File '+[char]34+$temp+[char]34)"
if errorlevel 1 (
  echo Cannot start the uninstaller. The application was not removed.
  pause
)
exit /b
# CLIPFORGE_REMOVE_PAYLOAD
# Standalone payload; copied to TEMP by the CMD wrapper before the app is removed.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
$exitCode = 0
$self = $PSCommandPath

function Full-Path([string]$Value) {
    return [IO.Path]::GetFullPath($Value).TrimEnd([char[]]'\/')
}
function Is-Under([string]$Value, [string]$Directory) {
    if (-not $Value) { return $false }
    try { return (Full-Path $Value).StartsWith($Directory + '\', [StringComparison]::OrdinalIgnoreCase) }
    catch { return $false }
}
function Has-ArgumentPath([string]$Command, [string]$Path) {
    if (-not $Command) { return $false }
    $normalized = $Command.Replace('/', '\')
    return $normalized -match ('(?:^|[\s"''])' + [regex]::Escape($Path) + '(?:$|[\s"''])')
}

try {
    Set-Location -LiteralPath ([IO.Path]::GetTempPath())
    $root = Full-Path $env:CLIPFORGE_REMOVE_ROOT
    $volume = [IO.Path]::GetPathRoot($root)
    if ($root.StartsWith('\\') -or (New-Object IO.DriveInfo($volume)).DriveType -ne [IO.DriveType]::Fixed) {
        throw 'Удаление поддерживается только из отдельной папки на локальном диске.'
    }
    if (-not (Test-Path -LiteralPath $root -PathType Container)) { throw 'Папка приложения не найдена.' }
    $protected = @($volume, $env:USERPROFILE, $env:WINDIR, $env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:ProgramData, $env:LOCALAPPDATA, $env:APPDATA, [IO.Path]::GetTempPath())
    foreach ($name in @('DesktopDirectory','MyDocuments','MyPictures','MyMusic','MyVideos')) {
        $protected += [Environment]::GetFolderPath($name)
    }
    if ($env:USERPROFILE) { $protected += Join-Path $env:USERPROFILE 'Downloads' }
    $downloadKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders'
    $downloads = Get-ItemPropertyValue -LiteralPath $downloadKey -Name '{374DE290-123F-4565-9164-39C4925E467B}' -ErrorAction SilentlyContinue
    if ($downloads) { $protected += [Environment]::ExpandEnvironmentVariables([string]$downloads) }
    foreach ($folder in $protected) {
        if (-not $folder) { continue }
        $safe = Full-Path $folder
        if ($root.Equals($safe, [StringComparison]::OrdinalIgnoreCase) -or (Is-Under $safe $root)) {
            throw 'Нельзя удалять системную или общую пользовательскую папку. ClipForge должен находиться в отдельной папке.'
        }
    }
    # Never follow a junction/symlink in the target root or its parent chain.
    $entry = Get-Item -LiteralPath $root -Force
    while ($null -ne $entry) {
        if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Папка или её родитель являются ссылкой/junction. Автоматическое удаление остановлено.' }
        $entry = $entry.Parent
    }
    foreach ($relative in @('backend\app\main.py','backend\app\media.py','frontend\package.json','Запустить ClipForge.cmd')) {
        if (-not (Test-Path -LiteralPath (Join-Path $root $relative) -PathType Leaf)) {
            throw 'Положите Удалить ClipForge.cmd в папку приложения рядом с Запустить ClipForge.cmd и повторите запуск.'
        }
    }
    $package = Get-Content -LiteralPath (Join-Path $root 'frontend\package.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($package.name -ne 'clipforge-web') { throw 'Эта папка не распознана как ClipForge. Удаление остановлено.' }

    # Do not let shell deletion traverse any nested directory junction either.
    $directories = New-Object 'System.Collections.Generic.Stack[System.IO.DirectoryInfo]'
    $directories.Push((Get-Item -LiteralPath $root -Force))
    while ($directories.Count -gt 0) {
        foreach ($child in $directories.Pop().EnumerateFileSystemInfos()) {
            if (($child.Attributes -band [IO.FileAttributes]::Directory) -ne 0) {
                if (($child.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                    throw 'Внутри приложения найдена ссылка на другую папку. Удаление остановлено, чтобы не затронуть её содержимое.'
                }
                $directories.Push([IO.DirectoryInfo]$child)
            }
        }
    }
    Write-Host "Удаляем ClipForge из: $root" -ForegroundColor Yellow
    Write-Host 'Будут удалены файлы приложения, локальные видео, клипы, модели и настройки внутри этой папки.'
    Write-Host 'Общие Python, Node.js, FFmpeg, Ollama и внешние каталоги данных сохранятся.'
    # The original CMD needs time to exit so it cannot hold the folder open.
    Start-Sleep -Seconds 2
    $snapshot = @(Get-CimInstance Win32_Process)
    $selected = @{}
    foreach ($process in $snapshot) {
        if ($process.ProcessId -eq $PID) { continue }
        $belongs = Is-Under ([string]$process.ExecutablePath) $root
        if (-not $belongs -and $process.Name -in @('powershell.exe','pwsh.exe')) {
            foreach ($script in @('scripts\windows-launcher.ps1','scripts\start.ps1','scripts\check-whisper.py')) {
                if (Has-ArgumentPath ([string]$process.CommandLine) (Join-Path $root $script)) { $belongs = $true }
            }
        }
        if (-not $belongs -and $process.Name -in @('node.exe','npm.exe','cmd.exe')) {
            $command = ([string]$process.CommandLine).Replace('/', '\')
            $frontend = Join-Path $root 'frontend'
            # Only a frontend executable argument rooted in this installation.
            $belongs = $command -match ('(?:^|[\s"''])' + [regex]::Escape($frontend) + '\\')
        }
        if ($belongs) { $selected[[uint32]$process.ProcessId] = $process }
    }
    # Include ffmpeg and other children of verified application processes.
    do {
        $changed = $false
        foreach ($process in $snapshot) {
            if ($process.ProcessId -eq $PID) { continue }
            $runtimeChild = $process.Name -in @('ffmpeg.exe','ffprobe.exe','python.exe','pythonw.exe','node.exe','npm.exe','cmd.exe')
            $ownOllama = $process.Name -eq 'ollama.exe' -and ([string]$process.CommandLine) -match '\b(serve|pull|runner)\b'
            if (($runtimeChild -or $ownOllama) -and $selected.ContainsKey([uint32]$process.ParentProcessId) -and -not $selected.ContainsKey([uint32]$process.ProcessId)) {
                $parent = $selected[[uint32]$process.ParentProcessId]
                if ($process.CreationDate -ge $parent.CreationDate) {
                    $selected[[uint32]$process.ProcessId] = $process
                    $changed = $true
                }
            }
        }
    } while ($changed)
    # Work from newest children to parents; recheck creation to avoid PID reuse.
    foreach ($process in ($selected.Values | Sort-Object CreationDate -Descending)) {
        $current = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.ProcessId)" -ErrorAction SilentlyContinue
        if ($current -and $current.CreationDate -eq $process.CreationDate) {
            try { Stop-Process -Id ([int]$process.ProcessId) -Force -ErrorAction Stop }
            catch {
                # A launcher can finish cleaning up this child concurrently.
                $remaining = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.ProcessId)" -ErrorAction SilentlyContinue
                if ($remaining -and $remaining.CreationDate -eq $process.CreationDate) { throw }
            }
        }
    }
    Start-Sleep -Milliseconds 800
    Add-Type -AssemblyName Microsoft.VisualBasic
    # Ask the Windows shell to recycle where supported. Never explicitly fall
    # back to a recursive permanent delete if Windows reports an error/cancel.
    [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory(
        $root,
        [Microsoft.VisualBasic.FileIO.UIOption]::OnlyErrorDialogs,
        [Microsoft.VisualBasic.FileIO.RecycleOption]::SendToRecycleBin,
        [Microsoft.VisualBasic.FileIO.UICancelOption]::ThrowException
    )
    if (Test-Path -LiteralPath $root) { throw 'Папка осталась на диске. Возможно, файл занят другой программой. Закройте её и повторите.' }
    Write-Host 'ClipForge удалён.' -ForegroundColor Green
} catch {
    $exitCode = 1
    Write-Host "Удаление остановлено: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'Другие программы и каталоги вне папки приложения не удалялись.'
} finally {
    Remove-Item Env:CLIPFORGE_REMOVE_ROOT -ErrorAction SilentlyContinue
    try { Remove-Item -LiteralPath $self -Force -ErrorAction SilentlyContinue } catch { }
}
if ($exitCode -eq 0) { Start-Sleep -Seconds 3 }
else { Read-Host 'Нажмите Enter, чтобы закрыть окно' | Out-Null }
exit $exitCode
