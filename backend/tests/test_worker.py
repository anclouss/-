"""Pipeline integration: simulated speech/model responses, real HTTP/queue/FFmpeg.

These tests do not claim to validate speech recognition or LLM editorial quality.
Only provider responses are fixtures; analysis, storage and exports execute normally.
"""
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
import shutil
import subprocess
import zipfile

from fastapi.testclient import TestClient
import pytest

from backend.app import ai, config, db, main, worker
from backend.app.media import probe


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    root = tmp_path / 'data'
    for module in (config, db, main, worker):
        monkeypatch.setattr(module, 'DATA', root)
    config.prepare()
    db.init()
    db.save_settings({'analysis_provider': 'ollama', 'max_clips': 2})
    return root


@pytest.fixture
def real_video(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg is required for pipeline export tests')
    path = tmp_path / 'fixture.mp4'
    subprocess.run([
        'ffmpeg', '-v', 'error', '-nostdin', '-y',
        '-f', 'lavfi', '-i', 'testsrc2=size=480x270:rate=12:duration=8',
        '-f', 'lavfi', '-i', 'sine=frequency=550:sample_rate=16000:duration=8',
        '-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(path),
    ], check=True, capture_output=True, timeout=30)
    return path


def test_upload_recovered_queue_analyze_export_edit_and_zip(pipeline, real_video, monkeypatch):
    provider_calls = []
    transcript = [
        {'start': .3, 'end': 1.1, 'text': 'Смотрите,'},
        {'start': 1.2, 'end': 2., 'text': 'что'},
        {'start': 2.1, 'end': 3., 'text': 'случилось!'},
        {'start': 3.5, 'end': 4.3, 'text': 'Это'},
        {'start': 4.4, 'end': 5.5, 'text': 'неожиданная'},
        {'start': 5.6, 'end': 7.2, 'text': 'победа.'},
    ]

    def simulated_transcription(audio, settings, progress):
        # Still require the actual extracted 16 kHz PCM audio.
        import wave
        with wave.open(str(audio)) as wav:
            assert wav.getframerate() == 16000
            assert wav.getnchannels() == 1
            assert wav.getnframes() >= 8 * 16000
        progress(.5)
        progress(1.)
        return transcript

    def simulated_model(system, prompt, settings):
        provider_calls.append(prompt)
        assert settings['analysis_provider'] == 'ollama'
        assert '<untrusted_transcript>' in prompt
        assert 'неожиданная' in prompt
        return {'candidates': [{
            'start': .3, 'end': 7.2, 'title': 'Неожиданная победа',
            'reason': 'Есть понятная завязка и завершение истории.',
            'rubric': {key: 8 for key in ('hook', 'context', 'emotion', 'novelty', 'payoff')},
        }]}

    monkeypatch.setattr(worker, 'transcribe', simulated_transcription)
    monkeypatch.setattr(ai, '_llm', simulated_model)

    with TestClient(main.app) as client:
        with real_video.open('rb') as source:
            result = client.post('/api/projects/upload', data={'title': 'Pipeline fixture', 'defer_render': 'false'},
                                 files={'file': ('recording.mp4', source, 'video/mp4')})
        assert result.status_code == 201, result.text
        project_id = result.json()['id']
        assert result.json()['status'] == 'queued'
        assert result.json()['clips'] == []
    # Browser session is closed. Work remains persisted in SQLite.
    interrupted = db.claim()
    assert interrupted['kind'] == 'analyze'
    assert interrupted['project_id'] == project_id
    assert db.claim() is None
    # Equivalent to a restarted, exclusively locked worker recovering an interrupted job.
    db.init()
    db.recover()
    recovered = db.claim()
    assert recovered['id'] == interrupted['id']
    worker.run_job(recovered)

    project = db.get('projects', project_id)
    assert project['status'] == 'completed', project.get('error')
    assert project['stage'] == 'done'
    assert project['progress'] == 100
    assert len(provider_calls) == 1
    assert not db.active(project_id)
    assert not (pipeline / 'audio' / f'{project_id}.wav').exists()
    assert (pipeline / 'transcripts' / f'{project_id}.json').is_file()

    with TestClient(main.app) as client:
        details = client.get(f'/api/projects/{project_id}').json()
        assert details['source_available'] is True
        assert len(details['clips']) == 1
        clip = details['clips'][0]
        clip_id = clip['id']
        assert clip['status'] == 'ready'
        assert clip['revision'] == 1
        assert clip['score'] == 80
        # A source shorter than 30 seconds is the documented duration exception.
        assert clip['start'] == 0
        assert clip['end'] == pytest.approx(8, abs=.1)
        output = pipeline / 'clips' / f'{clip_id}.mp4'
        info = probe(output)
        assert (info['width'], info['height']) == (1080, 1920)
        assert info['video_codec'] == 'h264' and info['has_audio']
        download = client.get(clip['download_url'])
        assert download.status_code == 200
        assert download.content == output.read_bytes()
        assert 'attachment' in download.headers['content-disposition']
        assert client.get(clip['preview_url'], headers={'Range': 'bytes=0-31'}).status_code == 206
        edit = client.post(f'/api/clips/{clip_id}/render', json={
            'start': 1., 'end': 7., 'title': 'Ручная правка',
            'settings': {**config.CLIP_DEFAULTS, 'layout': 'split', 'subtitle_size': 64,
                         'subtitle_color': '#60D7FF', 'auto_zoom': True},
            'words': [{'start': 1.2, 'end': 2., 'text': 'Исправлено'},
                      {'start': 2.1, 'end': 3., 'text': 'в редакторе!'}],
        })
        assert edit.status_code == 200, edit.text
        assert edit.json()['status'] == 'queued'
        assert client.get(f'/api/clips/{clip_id}/download').status_code == 409
    render = db.claim()
    assert render['kind'] == 'render' and render['clip_id'] == clip_id
    worker.run_job(render)
    assert db.claim() is None

    with TestClient(main.app) as client:
        result = client.get(f'/api/clips/{clip_id}').json()
        assert result['status'] == 'ready', result.get('error')
        assert result['revision'] == 2
        assert result['title'] == 'Ручная правка'
        assert result['words'][0]['text'] == 'Исправлено'
        assert result['settings']['layout'] == 'split'
        assert '?v=2' in result['preview_url']
        assert probe(output)['duration'] == pytest.approx(6., abs=.1)
        selected = client.get(f'/api/projects/{project_id}/download', params={'ids': clip_id})
        assert selected.status_code == 200
        with zipfile.ZipFile(BytesIO(selected.content)) as archive:
            assert archive.namelist() == [f'clipforge-{clip_id[:8]}.mp4']
            assert archive.read(archive.namelist()[0]) == output.read_bytes()
        all_exports = client.get(f'/api/projects/{project_id}/download')
        assert all_exports.status_code == 200
        assert list((pipeline / 'tmp').iterdir()) == []


def test_cleanup_skips_active_projects_and_preserves_exports(pipeline):
    old = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    db.save_settings({'retention_days': 7})
    sources = {}
    for ident, status in (('expired', 'completed'), ('rendering', 'completed'),
                          ('processing', 'processing'), ('recent', 'completed')):
        source = pipeline / 'sources' / f'{ident}.mp4'
        source.write_bytes(b'original')
        sources[ident] = source
        p = main.new_project(ident, 'stream.mp4', source, duration=120.)
        p.update(id=ident, status=status, updated_at=db.now() if ident == 'recent' else old)
        db.put('projects', p)
        (pipeline / 'audio' / f'{ident}.wav').write_bytes(b'temporary audio')
    db.enqueue('rendering', 'render', 'active-clip')
    export = pipeline / 'clips' / 'saved-clip.mp4'
    export.write_bytes(b'finished export stays downloadable')
    db.put('clips', {'id': 'saved-clip', 'project_id': 'expired', 'status': 'ready', 'revision': 1,
                     'start': 0, 'end': 30, 'settings': dict(config.CLIP_DEFAULTS), 'words': []})
    assert worker.cleanup_sources() == 1
    assert not sources['expired'].exists()
    assert db.get('projects', 'expired')['source_path'] is None
    assert not (pipeline / 'audio' / 'expired.wav').exists()
    assert export.read_bytes() == b'finished export stays downloadable'
    for ident in ('rendering', 'processing', 'recent'):
        assert sources[ident].is_file()
        assert (pipeline / 'audio' / f'{ident}.wav').is_file()
    with TestClient(main.app) as client:
        assert client.get('/api/clips/saved-clip/download').content == export.read_bytes()
        assert client.get('/media/source/expired').status_code == 410
        assert client.post('/api/clips/saved-clip/render', json={
            'start': 0, 'end': 30, 'settings': dict(config.CLIP_DEFAULTS),
        }).status_code == 410
    assert worker.cleanup_sources() == 0
