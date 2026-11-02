from pathlib import Path
import os
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
_data_path = Path(os.getenv("DATA_DIR", str(ROOT / "data"))).expanduser()
DATA = (_data_path if _data_path.is_absolute() else ROOT / _data_path).resolve()
os.environ.setdefault("WHISPER_CACHE_DIR", str(DATA / "models" / "whisper"))
os.environ.setdefault("HF_HOME", str(DATA / "models" / "huggingface"))
MAX_UPLOAD_GB = float(os.getenv("MAX_UPLOAD_GB", "4"))
MAX_BYTES = int(MAX_UPLOAD_GB * 1024**3)
MAX_DURATION = 12 * 3600
DEFAULTS = {
    "transcription_provider": os.getenv("TRANSCRIPTION_PROVIDER", "local"),
    "whisper_model": os.getenv("WHISPER_MODEL", "small"),
    "analysis_provider": os.getenv("ANALYSIS_PROVIDER", "ollama"),
    "openai_model": os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
    "ollama_model": os.getenv("OLLAMA_MODEL", "qwen2.5:7b"),
    "max_clips": int(os.getenv("MAX_CLIPS", "5")),
    "retention_days": int(os.getenv("RETENTION_DAYS", "7")),
    "language": "auto",
}
CLIP_DEFAULTS = {
    "layout": "crop",
    "crop_x": 0.5,
    "crop_y": 0.5,
    "face_box": [0.65, 0, 0.35, 0.4],
    "face_tracking": False,
    "subtitle_font": "Arial",
    "subtitle_size": 58,
    "subtitle_position": 0.76,
    "subtitle_color": "#B8FF60",
    "subtitles": True,
    "auto_zoom": False,
    "remove_silence": False,
}


def prepare():
    for folder in (
        "sources",
        "clips",
        "audio",
        "transcripts",
        "tmp",
        "signals",
        "waveforms",
        "proxies",
        "editor/manifests",
        "editor/results",
    ):
        (DATA / folder).mkdir(parents=True, exist_ok=True)
