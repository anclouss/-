"""Read effective launcher settings without exposing API keys or environment dumps."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app import db

db.init()
settings = db.settings()
print(json.dumps({
    'host': os.getenv('HOST', '127.0.0.1'),
    'port': int(os.getenv('PORT', '8000')),
    'analysis_provider': settings['analysis_provider'],
    'transcription_provider': settings['transcription_provider'],
    'ollama_model': settings['ollama_model'],
    'ollama_base_url': os.getenv('OLLAMA_BASE_URL', 'http://127.0.0.1:11434').rstrip('/'),
    'openai_key_present': bool(os.getenv('OPENAI_API_KEY', '').strip()),
}))
