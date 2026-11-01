# Windows PowerShell 5.1+. Called by the double-click CMD in the project root.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
$processes = @()
$ownsMutex = $false
$mutex = $null
$exitCode = 0
$logs = Join-Path $root '.logs'
$python = Join-Path $root '.venv\Scripts\python.exe'

function Refresh-ToolPath {
    $paths = @($env:PATH, [Environment]::GetEnvironmentVariable('Path', 'Machine'), [Environment]::GetEnvironmentVariable('Path', 'User'))
    $paths += @( (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links'), (Join-Path $env:LOCALAPPDATA 'Programs\Ollama'), (Join-Path $env:ProgramFiles 'nodejs') )
    $env:PATH = ($paths | Where-Object { $_ }) -join ';'
}

function Invoke-Checked {
    param([string]$Command, [string[]]$Arguments)
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Команда завершилась с ошибкой ($LASTEXITCODE): $Command. Проверьте сообщения выше." }
}

function Install-Tool {
    param([string]$Package)
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        throw "Для первой установки нужен winget (Установщик приложений Microsoft). Установите инструменты по README и запустите снова."
    }
    Write-Host "Устанавливаем $Package из официального каталога winget..." -ForegroundColor Cyan
    & winget.exe install --id $Package --exact --source winget --accept-source-agreements --accept-package-agreements --disable-interactivity
    if ($LASTEXITCODE -notin @(0, 3010)) { throw "winget не установил $Package (код $LASTEXITCODE). См. вывод выше или установите инструмент по README." }
    Refresh-ToolPath
}

function Find-Python312 {
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        try {
            $found = & py.exe -3.12 -c 'import sys; print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $found -and (Test-Path -LiteralPath $found)) { return [string]$found }
        } catch { <# Python launcher exists but this version may not be installed. #> }
    }
    $candidates = @((Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'), (Join-Path $env:ProgramFiles 'Python312\python.exe'))
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command -and $command.Source -notlike '*\WindowsApps\*') { $candidates += $command.Source }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            try {
                $version = & $candidate -c 'import sys; print(str(sys.version_info.major)+chr(46)+str(sys.version_info.minor))' 2>$null
                if ($LASTEXITCODE -eq 0 -and $version -eq '3.12') { return $candidate }
            } catch { <# Continue checking other Python installations. #> }
        }
    }
    return $null
}

function Get-Fingerprint {
    param([string[]]$Files)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $joined = ($Files | Sort-Object | ForEach-Object {
            if (Test-Path -LiteralPath $_ -PathType Leaf) { "$_=" + (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash }
        }) -join "`n"
        return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($joined)))).Replace('-', '')
    } finally { $sha.Dispose() }
}

function Marker-Matches {
    param([string]$Path, [string]$Fingerprint)
    return (Test-Path -LiteralPath $Path) -and ((Get-Content -LiteralPath $Path -Raw).Trim() -eq $Fingerprint)
}

function Start-Owned {
    param([string]$Executable, [string[]]$Arguments, [string]$Name)
    # The launcher passes only fixed module names or validated host/port here;
    # paths containing spaces stay in FilePath / WorkingDirectory, not argv.
    $process = Start-Process -FilePath $Executable -ArgumentList $Arguments -WorkingDirectory $root -NoNewWindow -PassThru -RedirectStandardOutput (Join-Path $logs "$Name.log") -RedirectStandardError (Join-Path $logs "$Name.error.log")
    $script:processes += $process
    return $process
}

function Get-OllamaTags {
    param([string]$Base)
    try { return Invoke-RestMethod -Uri "$Base/api/tags" -TimeoutSec 3 -Method Get } catch { return $null }
}

try {
    # A second click never launches another queue worker or installs concurrently.
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $identity = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($root.ToLowerInvariant()))).Replace('-', '') }
    finally { $sha.Dispose() }
    $mutex = New-Object System.Threading.Mutex($false, "Local\ClipForge-$identity")
    try { $ownsMutex = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
    if (-not $ownsMutex) {
        Write-Host 'ClipForge уже запущен или выполняет первую настройку.' -ForegroundColor Yellow
        $endpointFile = Join-Path $logs 'browser-url.txt'
        if (Test-Path -LiteralPath $endpointFile) {
            $existing = (Get-Content -LiteralPath $endpointFile -Raw).Trim()
            if ($existing -match '^http://(127\.0\.0\.1|localhost):[0-9]{1,5}/$') { Start-Process $existing }
        }
    } else {
        Write-Host "`nCLIPFORGE — автоматический запуск`n" -ForegroundColor Green
        Write-Host 'Первый запуск: установка зависимостей и, при локальном AI, загрузка модели (несколько ГБ).'
        Write-Host 'Нужен интернет. Windows может запросить разрешение на установку инструментов.'
        New-Item -ItemType Directory -Force -Path $logs | Out-Null
        Remove-Item -LiteralPath (Join-Path $logs 'browser-url.txt') -Force -ErrorAction SilentlyContinue
        Refresh-ToolPath
        $basePython = Find-Python312
        if (-not $basePython) { Install-Tool 'Python.Python.3.12'; $basePython = Find-Python312 }
        if (-not $basePython) { throw 'Python 3.12 не найден после установки. Перезапустите Windows и нажмите кнопку ещё раз.' }
        if (-not (Get-Command node.exe -ErrorAction SilentlyContinue) -or -not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) { Install-Tool 'OpenJS.NodeJS.LTS' }
        foreach ($tool in @('node.exe','npm.cmd')) {
            if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool не найден. Перезапустите окно или установите Node.js по README." }
        }
        $nodeMajor = & node.exe -p 'process.versions.node.split(String.fromCharCode(46))[0]'
        if ($LASTEXITCODE -ne 0 -or [int]$nodeMajor -lt 22) { throw 'Нужен Node.js 22 или новее. Обновите Node.js LTS и запустите снова.' }
        if (-not (Get-Command ffmpeg.exe -ErrorAction SilentlyContinue) -or -not (Get-Command ffprobe.exe -ErrorAction SilentlyContinue)) { Install-Tool 'Gyan.FFmpeg' }
        foreach ($tool in @('ffmpeg.exe','ffprobe.exe')) {
            if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool не найден после установки. Перезапустите Windows и повторите запуск." }
        }
        if (-not (Test-Path -LiteralPath $python)) {
            if (Test-Path -LiteralPath (Join-Path $root '.venv')) { throw 'В .venv нет Windows Python. Сохраните или переименуйте это окружение и повторите запуск.' }
            Write-Host 'Создаём Python-окружение...'
            Invoke-Checked $basePython @('-m','venv',(Join-Path $root '.venv'))
        }
        $venvVersion = & $python -c 'import sys; print(str(sys.version_info.major)+chr(46)+str(sys.version_info.minor))'
        if ($LASTEXITCODE -ne 0 -or $venvVersion -ne '3.12') { throw 'Существующее .venv использует не Python 3.12. Переименуйте окружение и повторите запуск.' }
        $backendHash = Get-Fingerprint @('backend\requirements.txt')
        $backendMarker = Join-Path $root '.venv\clipforge-requirements.sha256'
        if (-not (Marker-Matches $backendMarker $backendHash)) {
            Write-Host 'Устанавливаем Python-зависимости...' -ForegroundColor Cyan
            Invoke-Checked $python @('-m','ensurepip','--upgrade')
            Invoke-Checked $python @('-m','pip','install','--disable-pip-version-check','-r','backend\requirements.txt')
            Set-Content -LiteralPath $backendMarker -Value $backendHash -Encoding ASCII
        }
        $nodeMarker = Join-Path $root 'frontend\node_modules\.clipforge-lock.sha256'
        $nodeHash = Get-Fingerprint @('frontend\package-lock.json','frontend\package.json')
        if (-not (Marker-Matches $nodeMarker $nodeHash)) {
            Write-Host 'Устанавливаем frontend-зависимости...' -ForegroundColor Cyan
            Invoke-Checked 'npm.cmd' @('ci','--prefix','frontend','--no-audit','--no-fund')
            Set-Content -LiteralPath $nodeMarker -Value $nodeHash -Encoding ASCII
        }
        $buildFiles = @(Get-ChildItem -LiteralPath (Join-Path $root 'frontend\src') -Recurse -File | ForEach-Object { $_.FullName })
        $buildFiles += @('frontend\package.json','frontend\package-lock.json','frontend\index.html','frontend\tsconfig.json','frontend\vite.config.ts')
        $buildHash = Get-Fingerprint $buildFiles
        $buildMarker = Join-Path $root 'frontend\dist\.clipforge-build.sha256'
        if (-not (Test-Path -LiteralPath (Join-Path $root 'frontend\dist\index.html')) -or -not (Marker-Matches $buildMarker $buildHash)) {
            Write-Host 'Собираем интерфейс...' -ForegroundColor Cyan
            Invoke-Checked 'npm.cmd' @('--prefix','frontend','run','build')
            Set-Content -LiteralPath $buildMarker -Value $buildHash -Encoding ASCII
        }
        if (-not (Test-Path -LiteralPath (Join-Path $root '.env'))) { Copy-Item -LiteralPath (Join-Path $root '.env.example') -Destination (Join-Path $root '.env') }
        $configText = & $python (Join-Path $PSScriptRoot 'launcher-config.py')
        if ($LASTEXITCODE -ne 0) { throw 'Не удалось прочитать настройки. Проверьте .env и Python-зависимости.' }
        $config = $configText | ConvertFrom-Json
        if ($config.host -notin @('127.0.0.1','localhost') -or $config.port -lt 1 -or $config.port -gt 65535) { throw 'Для запуска одной кнопкой задайте HOST=127.0.0.1 и допустимый PORT в .env.' }
        $browserUrl = "http://$($config.host):$($config.port)/"
        $listener = New-Object System.Net.Sockets.TcpListener([Net.IPAddress]::Loopback, [int]$config.port)
        try { $listener.Start() } catch { throw "Порт $($config.port) уже занят. Остановите старый запуск или измените PORT в .env." }
        finally { $listener.Stop() }
        if ($config.analysis_provider -eq 'openai' -or $config.transcription_provider -eq 'openai') {
            if (-not $config.openai_key_present) { throw 'Выбран OpenAI, но ключ не задан. Добавьте OPENAI_API_KEY в .env. Не отправляйте ключ в чат.' }
        }
        if ($config.analysis_provider -eq 'ollama') {
            $base = [string]$config.ollama_base_url
            $uri = [Uri]$base
            if ($uri.Scheme -notin @('http','https') -or $uri.UserInfo -or $uri.Query -or $uri.Fragment) { throw 'Проверьте OLLAMA_BASE_URL в .env: нужен HTTP(S)-адрес сервера без логина и пароля.' }
            $localOllama = $uri.Host -in @('127.0.0.1','localhost')
            $tags = Get-OllamaTags $base
            if (-not $tags -and $localOllama) {
                if (-not (Get-Command ollama.exe -ErrorAction SilentlyContinue)) { Install-Tool 'Ollama.Ollama' }
                if (-not (Get-Command ollama.exe -ErrorAction SilentlyContinue)) { throw 'Ollama не найдена после установки. Перезапустите Windows и запустите снова.' }
                # The installer may already have started the user's Ollama service.
                $tags = Get-OllamaTags $base
                if (-not $tags) {
                    $previousOllamaHost = $env:OLLAMA_HOST
                    try {
                        $env:OLLAMA_HOST = $base
                        $null = Start-Owned (Get-Command ollama.exe).Source @('serve') 'ollama'
                    } finally { $env:OLLAMA_HOST = $previousOllamaHost }
                    $deadline = (Get-Date).AddSeconds(45)
                    do { $tags = Get-OllamaTags $base; if (-not $tags) { Start-Sleep -Seconds 1 } } while (-not $tags -and (Get-Date) -lt $deadline)
                }
            }
            if (-not $tags) { throw 'Сервер Ollama недоступен. Проверьте OLLAMA_BASE_URL и сеть; удалённый сервер запускается отдельно.' }
            $model = [string]$config.ollama_model
            if ($model -notmatch '^[a-zA-Z0-9._:/-]{1,100}$' -or $model.StartsWith('-')) { throw 'Некорректное имя модели Ollama в настройках приложения.' }
            $modelNames = @($tags.models | ForEach-Object { $_.name })
            $expected = $model
            if (($model -split '/')[-1] -notmatch ':') { $expected += ':latest' }
            if ($model -notin $modelNames -and $expected -notin $modelNames) {
                if (-not $localOllama) { throw "Модель $model отсутствует на удалённом сервере. Загрузите её на этом сервере и повторите запуск." }
                if (-not (Get-Command ollama.exe -ErrorAction SilentlyContinue)) { Install-Tool 'Ollama.Ollama' }
                Write-Host "Загружаем модель $model. Это может занять время и несколько ГБ..." -ForegroundColor Cyan
                $previousOllamaHost = $env:OLLAMA_HOST
                try { $env:OLLAMA_HOST = $base; Invoke-Checked 'ollama.exe' @('pull',$model) }
                finally { $env:OLLAMA_HOST = $previousOllamaHost }
            }
        }
        if ($config.transcription_provider -eq 'local') { Write-Host 'Whisper загрузит выбранную модель автоматически при обработке первого видео.' }
        $null = Start-Owned $python @('-u','-m','uvicorn','backend.app.main:app','--host',[string]$config.host,'--port',[string]$config.port) 'api'
        $null = Start-Owned $python @('-u','-m','backend.app.worker') 'worker'
        $deadline = (Get-Date).AddSeconds(60)
        $ready = $false
        do {
            foreach ($process in $processes) { if ($process.HasExited) { throw 'Один из сервисов остановился. Подробности в .logs\*.log.' } }
            try {
                $health = Invoke-RestMethod -Uri ($browserUrl + 'api/health') -TimeoutSec 2
                $web = Invoke-WebRequest -Uri $browserUrl -TimeoutSec 2 -UseBasicParsing
                $ready = $health.status -eq 'ok' -and $health.ffmpeg -and $web.StatusCode -eq 200 -and $web.Content.Contains('id="root"')
            } catch { $ready = $false }
            if (-not $ready) { Start-Sleep -Seconds 1 }
        } while (-not $ready -and (Get-Date) -lt $deadline)
        if (-not $ready) { throw 'Приложение не ответило за 60 секунд. Подробности в .logs\*.log.' }
        Set-Content -LiteralPath (Join-Path $logs 'browser-url.txt') -Value $browserUrl -Encoding ASCII
        try { Start-Process $browserUrl } catch { Write-Host "Откройте браузер: $browserUrl" }
        Write-Host "`nClipForge запущен: $browserUrl" -ForegroundColor Green
        Write-Host 'Оставьте это окно открытым. Для остановки нажмите Ctrl+C. Логи: .logs\'
        while ($true) {
            foreach ($process in $processes) { if ($process.HasExited) { throw 'Один из сервисов остановился. Подробности в .logs\*.log.' } }
            Start-Sleep -Seconds 2
        }
    }
} catch {
    $exitCode = 1
    Write-Host "`nНе удалось запустить ClipForge: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'Логи: .logs\ | Инструкция: README.md'
} finally {
    foreach ($process in $processes) {
        try {
            if (-not $process.HasExited) { & taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null }
        } catch { Write-Warning "Не удалось остановить процесс $($process.Id)." }
    }
    if ($ownsMutex) {
        Remove-Item -LiteralPath (Join-Path $logs 'browser-url.txt') -Force -ErrorAction SilentlyContinue
        $mutex.ReleaseMutex()
    }
    if ($mutex) { $mutex.Dispose() }
}
exit $exitCode
