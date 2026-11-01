#!/usr/bin/env bash
set -euo pipefail
cd /workspace/-
command -v ffmpeg >/dev/null
command -v ffprobe >/dev/null
export UV_CACHE_DIR=/tmp/clipforge-uv
export UV_LINK_MODE=copy
if [[ ! -x .venv/bin/python ]]; then
  uv venv --python 3.12 .venv
fi
uv pip sync --python .venv/bin/python backend/requirements.txt
npm ci --prefix frontend --cache /tmp/clipforge-npm
npm --prefix frontend run build
.venv/bin/python -c 'from faster_whisper import WhisperModel; from backend.app.main import app; print("Backend dependencies OK")'
# Model weights are intentionally not downloaded here. See README for Ollama /
# Whisper setup and the required network access; no paid API calls occur.
