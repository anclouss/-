"""Local Whisper diagnostic. Never prints credentials or raw network exceptions."""
from __future__ import annotations

import argparse
import importlib
import os
from pathlib import Path
import platform
import sys
import tempfile
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def native_check():
    """Exit3 specifically indicates a Windows native DLL dependency failure."""
    for name in ('ctranslate2', 'onnxruntime', 'faster_whisper'):
        try:
            importlib.import_module(name)
            print(f'OK: {name}', flush=True)
        except Exception as exc:
            text = str(exc).lower()  # classify internally; never print raw exception
            dll = getattr(exc, 'winerror', None) in (126,127) or any(s in text for s in ('dll load failed','vcruntime','msvcp','winerror 126','winerror 127'))
            print(f'FAIL: {name}, {type(exc).__name__}', flush=True)
            if dll:
                print('Install Microsoft Visual C++ Redistributable x64:', flush=True)
                print('winget install --id Microsoft.VCRedist.2015+.x64 --exact', flush=True)
                return 3
            print('Run the launcher again to install Python dependencies. See README.', flush=True)
            return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-only', action='store_true')
    parser.add_argument('--model', choices=['tiny','base','small','medium','large-v3','turbo'])
    parser.add_argument('--audio', type=Path, help='Optional local video/audio file for actual recognition')
    args = parser.parse_args()
    # Load .env/cache configuration before native imports initialize HF constants.
    try:
        from backend.app import config
    except Exception as exc:
        print(f'FAIL: configuration dependencies ({type(exc).__name__}). Run the launcher first.', flush=True)
        return 1
    code = native_check()
    if code or args.native_only:
        return code
    from backend.app import db
    from backend.app.ai import local_transcription_error
    from faster_whisper import WhisperModel
    db.init()
    settings = db.settings()
    name = args.model or settings['whisper_model']
    print(f'Python: {platform.python_version()} ({platform.machine()})', flush=True)
    print(f'Whisper model: {name}', flush=True)
    print('1. Downloading/loading model. First download can take several minutes.', flush=True)
    device = os.getenv('WHISPER_DEVICE','cpu')
    compute = os.getenv('WHISPER_COMPUTE_TYPE','int8' if device=='cpu' else 'float16')
    try:
        model = WhisperModel(name, device=device, compute_type=compute,
                             cpu_threads=max(1,min(4,os.cpu_count() or 1)),
                             download_root=os.getenv('WHISPER_CACHE_DIR') or None)
    except Exception as exc:
        print(str(local_transcription_error(exc,'load',name)), flush=True)
        return 1
    print('OK: model loaded.', flush=True)
    print('2. Checking speech recognition and VAD.', flush=True)
    try:
        with tempfile.TemporaryDirectory(prefix='clipforge-whisper-check-') as directory:
            if args.audio:
                if not args.audio.is_file():
                    print('FAIL: input file not found.',flush=True)
                    return 1
                audio = args.audio.resolve()
            else:
                audio = Path(directory)/'silence.wav'
                with wave.open(str(audio),'wb') as out:
                    out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000)
                    out.writeframes(b'\0\0'*16000)
            language = settings.get('language')
            segments,info = model.transcribe(str(audio),language=None if language=='auto' else language,
                                            vad_filter=True,word_timestamps=True,beam_size=5)
            count = sum(len(segment.words or []) for segment in segments)
    except Exception as exc:
        print(str(local_transcription_error(exc,'inference',name)),flush=True)
        return 1
    if args.audio:
        print(f'OK: recognized {count} words; transcript text is not included in this report.',flush=True)
        if not count:
            print('No speech detected. Check the selected audio track and language.',flush=True)
            return 1
    else:
        print('OK: model and VAD ran on silence. This checks startup, not speech accuracy.',flush=True)
        print('Drag a video file onto the diagnostic CMD to test actual speech.',flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
