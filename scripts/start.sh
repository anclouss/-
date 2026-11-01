#!/usr/bin/env bash
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

python_bin=".venv/bin/python"
if [[ ! -x "$python_bin" ]]; then
  echo "Создайте .venv и установите backend/requirements.txt; см. README." >&2
  exit 1
fi
command -v npm >/dev/null || { echo "Не найден npm." >&2; exit 1; }
command -v ffmpeg >/dev/null || { echo "Не найден FFmpeg в PATH." >&2; exit 1; }
command -v ffprobe >/dev/null || { echo "Не найден FFprobe в PATH." >&2; exit 1; }
[[ -d frontend/node_modules ]] || { echo "Выполните npm ci --prefix frontend." >&2; exit 1; }

endpoint_file="$(mktemp)"
trap 'rm -f -- "$endpoint_file"' EXIT
"$python_bin" - <<'PY' > "$endpoint_file"
import os
from dotenv import load_dotenv
load_dotenv('.env')
host = os.getenv('HOST', '127.0.0.1')
port = int(os.getenv('PORT', '8000'))
if not 1 <= port <= 65535 or any(c.isspace() for c in host):
    raise ValueError('Некорректные HOST/PORT')
print(host)
print(port)
PY
api_host="$(sed -n '1p' "$endpoint_file")"
api_port="$(sed -n '2p' "$endpoint_file")"
rm -f -- "$endpoint_file"
api_request_host="$api_host"
[[ "$api_host" == "0.0.0.0" ]] && api_request_host="127.0.0.1"
export VITE_API_PROXY_TARGET="http://${api_request_host}:${api_port}"
mkdir -p .logs
pids=()

stop_tree() {
  local child
  if command -v pgrep >/dev/null; then
    for child in $(pgrep -P "$1" || true); do stop_tree "$child"; done
  fi
  kill -TERM "$1" 2>/dev/null || true
}
cleanup() {
  trap - EXIT INT TERM
  for pid in "${pids[@]}"; do stop_tree "$pid"; done
  for pid in "${pids[@]}"; do wait "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

"$python_bin" -u -m uvicorn backend.app.main:app --host "$api_host" --port "$api_port" >.logs/api.log 2>&1 &
pids+=("$!")
"$python_bin" -u -m backend.app.worker >.logs/worker.log 2>&1 &
pids+=("$!")
npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173 --strictPort >.logs/frontend.log 2>&1 &
pids+=("$!")

"$python_bin" - "$VITE_API_PROXY_TARGET/api/health" <<'PY'
import sys, time, urllib.request
urls = [sys.argv[1], 'http://127.0.0.1:5173']
deadline = time.monotonic() + 60
while urls and time.monotonic() < deadline:
    remaining = []
    for url in urls:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status != 200:
                    remaining.append(url)
        except Exception:
            remaining.append(url)
    urls = remaining
    if urls:
        time.sleep(1)
if urls:
    raise SystemExit('Сервисы не ответили за 60 секунд; проверьте .logs/*.log')
PY
echo "ClipForge: http://127.0.0.1:5173"
echo "Логи: .logs/ — Ctrl+C останавливает эти процессы."
echo "Для AI должна быть запущена Ollama с выбранной моделью либо настроен OpenAI."
while true; do
  for pid in "${pids[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "Один из процессов остановился. Проверьте .logs/*.log" >&2
      exit 1
    fi
  done
  sleep 2
done
