$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Create .venv and install backend/requirements.txt first. See README." }
foreach ($command in @("npm.cmd", "ffmpeg", "ffprobe")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) { throw "Missing command: $command" }
}
if (-not (Test-Path "frontend\node_modules")) { throw "Run npm.cmd ci --prefix frontend first." }

$endpointJson = & $python -c "import json,os; from dotenv import load_dotenv; load_dotenv('.env'); print(json.dumps({'host':os.getenv('HOST','127.0.0.1'),'port':int(os.getenv('PORT','8000'))}))"
if ($LASTEXITCODE -ne 0) { throw "Cannot read HOST/PORT. Check .env and Python dependencies." }
$endpoint = $endpointJson | ConvertFrom-Json
if ($endpoint.port -lt 1 -or $endpoint.port -gt 65535 -or $endpoint.host -match '\s') { throw "Invalid HOST/PORT." }
$requestHost = $endpoint.host
if ($requestHost -eq "0.0.0.0") { $requestHost = "127.0.0.1" }
$oldTarget = $env:VITE_API_PROXY_TARGET
$env:VITE_API_PROXY_TARGET = "http://${requestHost}:$($endpoint.port)"
New-Item -ItemType Directory -Force ".logs" | Out-Null
$processes = @()

try {
    $processes += Start-Process -FilePath $python -ArgumentList @("-u", "-m", "uvicorn", "backend.app.main:app", "--host", $endpoint.host, "--port", "$($endpoint.port)") -WorkingDirectory $root -PassThru -NoNewWindow -RedirectStandardOutput ".logs\api.log" -RedirectStandardError ".logs\api.error.log"
    $processes += Start-Process -FilePath $python -ArgumentList @("-u", "-m", "backend.app.worker") -WorkingDirectory $root -PassThru -NoNewWindow -RedirectStandardOutput ".logs\worker.log" -RedirectStandardError ".logs\worker.error.log"
    $processes += Start-Process -FilePath $env:ComSpec -ArgumentList @("/d", "/s", "/c", "npm.cmd --prefix frontend run dev -- --host 127.0.0.1 --port 5173 --strictPort") -WorkingDirectory $root -PassThru -NoNewWindow -RedirectStandardOutput ".logs\frontend.log" -RedirectStandardError ".logs\frontend.error.log"
    $deadline = (Get-Date).AddSeconds(60)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        foreach ($process in $processes) {
            if ($process.HasExited) { throw "A service stopped. Check .logs/*.log." }
        }
        try {
            $api = Invoke-WebRequest -Uri "$env:VITE_API_PROXY_TARGET/api/health" -UseBasicParsing -TimeoutSec 2
            $web = Invoke-WebRequest -Uri "http://127.0.0.1:5173" -UseBasicParsing -TimeoutSec 2
            if ($api.StatusCode -eq 200 -and $web.StatusCode -eq 200) { $ready = $true; break }
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ready) { throw "Services did not become available in 60 seconds. Check .logs/*.log." }
    Write-Host "ClipForge: http://127.0.0.1:5173"
    Write-Host "Logs: .logs/ | Ctrl+C stops the services started by this script."
    Write-Host "AI requires Ollama with the selected model or a configured OpenAI key."
    while ($true) {
        foreach ($process in $processes) {
            if ($process.HasExited) { throw "A service stopped. Check .logs/*.log." }
        }
        Start-Sleep -Seconds 2
    }
} finally {
    foreach ($process in $processes) {
        if (-not $process.HasExited) {
            & taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null
        }
    }
    if ($null -eq $oldTarget) { Remove-Item Env:VITE_API_PROXY_TARGET -ErrorAction SilentlyContinue }
    else { $env:VITE_API_PROXY_TARGET = $oldTarget }
}
