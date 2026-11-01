"""HTTP boundary tests. AI inference is not substituted into these API tests."""
from io import BytesIO
from pathlib import Path
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import config, db, main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DATA', tmp_path)
    monkeypatch.setattr(db, 'DATA', tmp_path)
    monkeypatch.setattr(main, 'DATA', tmp_path)
    monkeypatch.setattr(main, 'MAX_BYTES', 32)
    monkeypatch.setattr(main, 'probe', lambda path: {'duration': 120., 'width': 320, 'height': 180, 'has_audio': True})
    with TestClient(main.app) as connection:
        yield connection


def seed_project(ident='project', status='completed'):
    source = config.DATA / 'sources' / f'{ident}.mp4'
    source.write_bytes(b'source video bytes')
    p = main.new_project('История', 'stream.mp4', source, duration=120)
    p.update(id=ident, status=status, stage='done', progress=100)
    db.put('projects', p)
    return p


def seed_clip(project_id='project', ident='clip-one', status='ready', start=10., end=60.):
    c = {'id': ident, 'project_id': project_id, 'title': 'Кульминация', 'reason': 'История с завершением',
         'score': 77, 'start': start, 'end': end, 'status': status, 'error': None,
         'revision': 1, 'settings': dict(config.CLIP_DEFAULTS),
         'words': [{'start': 10., 'end': 11., 'text': 'Привет!'}]}
    db.put('clips', c)
    (config.DATA / 'clips' / f'{ident}.mp4').write_bytes(b'0123456789abcdef')
    return c


def edit_body(**changes):
    return {'start': 10, 'end': 60, 'settings': dict(config.CLIP_DEFAULTS), **changes}


def test_upload_queues_persistent_project_without_exposing_storage_paths(client):
    result = client.post('/api/projects/upload', files={'file': ('стрим.mp4', b'valid media bytes', 'video/mp4')}, data={'title': 'Мой стрим'})
    assert result.status_code == 201, result.text
    p = result.json()
    assert p['title'] == 'Мой стрим'
    assert p['status'] == 'queued'
    assert p['duration'] == 120
    assert p['clips'] == []
    assert 'source_path' not in p
    assert 'source_url' not in p
    assert db.active(p['id'])
    assert Path(db.get('projects', p['id'])['source_path']).read_bytes() == b'valid media bytes'
    assert client.get(f"/api/projects/{p['id']}").json()['id'] == p['id']
    assert client.get('/api/projects').json()[0]['clip_count'] == 0


def test_failed_uploads_remove_partial_files_and_never_enqueue(client, monkeypatch):
    unsupported = client.post('/api/projects/upload', files={'file': ('playlist.m3u8', b'#EXTM3U')})
    assert unsupported.status_code == 400
    oversized = client.post('/api/projects/upload', files={'file': ('stream.mp4', b'x' * 33)})
    assert oversized.status_code == 413
    monkeypatch.setattr(main, 'probe', lambda path: {'duration': 120, 'has_audio': False})
    silent = client.post('/api/projects/upload', files={'file': ('stream.mp4', b'small')})
    assert silent.status_code == 400
    assert client.get('/api/projects').json() == []
    assert list((config.DATA / 'sources').iterdir()) == []


@pytest.mark.parametrize('url', [
    'http://youtube.com/watch?v=test', 'https://127.0.0.1/private',
    'https://youtube.com.evil.invalid/watch?v=test',
    'https://youtube.com@evil.invalid/watch?v=test',
    'https://user:pass@youtube.com/watch?v=test',
    'https://youtube.com:8443/watch?v=test', 'file:///etc/passwd',
    'https://youtube.com/', 'https://youtube.com:bad/watch?v=test', 'https://[invalid',
])
def test_remote_import_rejects_ssrf_and_malformed_urls(client, url):
    response = client.post('/api/projects/url', json={'url': url})
    assert response.status_code == 400, response.text
    assert db.all_projects() == []


def test_public_recording_import_queues_url_without_leaking_it(client):
    response = client.post('/api/projects/url', json={'url': 'https://www.youtube.com/watch?v=public', 'title': 'Запись'})
    assert response.status_code == 201
    p = response.json()
    assert p['status'] == 'queued'
    assert 'source_url' not in p
    assert db.get('projects', p['id'])['source_url'].endswith('v=public')


def test_mutations_reject_unrelated_browser_origins(client):
    forbidden = client.patch('/api/settings', headers={'Origin': 'https://unrelated.invalid'}, json={'max_clips': 2})
    assert forbidden.status_code == 403
    assert db.settings()['max_clips'] != 2
    allowed = client.patch('/api/settings', headers={'Origin': 'http://127.0.0.1:5173'}, json={'max_clips': 2})
    assert allowed.status_code == 200
    assert db.settings()['max_clips'] == 2


def test_settings_validate_values_and_do_not_return_api_secret(client, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'private-test-value-do-not-display')
    async def unavailable(*args, **kwargs):
        raise httpx.ConnectError('not running')
    monkeypatch.setattr(httpx.AsyncClient, 'get', unavailable)
    response = client.get('/api/settings')
    assert response.status_code == 200
    assert response.json()['providers']['openai'] is True
    assert 'private-test-value' not in response.text
    assert client.patch('/api/settings', json={'max_clips': 0}).status_code == 422
    assert client.patch('/api/settings', json={'analysis_provider': 'fake'}).status_code == 422
    assert client.patch('/api/settings', json={'OPENAI_API_KEY': 'bad'}).status_code == 422
    assert client.patch('/api/settings', json={'analysis_provider': 'heuristic', 'retention_days': 4}).status_code == 200
    assert db.settings()['retention_days'] == 4


def test_preview_and_source_support_byte_ranges_and_download_disposition(client):
    seed_project()
    seed_clip()
    response = client.get('/media/clips/clip-one', headers={'Range': 'bytes=2-5'})
    assert response.status_code == 206
    assert response.content == b'2345'
    assert response.headers['content-range'] == 'bytes 2-5/16'
    source = client.get('/media/source/project', headers={'Range': 'bytes=0-5'})
    assert source.status_code == 206
    assert source.content == b'source'
    download = client.get('/api/clips/clip-one/download')
    assert download.status_code == 200
    assert 'attachment' in download.headers['content-disposition']
    assert download.content == b'0123456789abcdef'
    assert client.get('/media/clips/missing').status_code == 404


def test_zip_selection_cannot_include_another_project_and_temp_archive_is_cleaned(client):
    seed_project()
    seed_clip(ident='first0001')
    seed_clip(ident='second002')
    seed_project('another')
    seed_clip('another', 'foreign03')
    response = client.get('/api/projects/project/download?ids=second002')
    assert response.status_code == 200
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        assert archive.namelist() == ['clipforge-second00.mp4']
        assert archive.read(archive.namelist()[0]) == b'0123456789abcdef'
    assert list((config.DATA / 'tmp').iterdir()) == []
    assert client.get('/api/projects/project/download?ids=foreign03').status_code == 400
    all_clips = client.get('/api/projects/project/download')
    with zipfile.ZipFile(BytesIO(all_clips.content)) as archive:
        assert len(archive.namelist()) == 2


def test_editor_persists_range_text_style_and_queues_render(client):
    seed_project()
    seed_clip()
    words = [{'start': 12, 'end': 13, 'text': 'Исправленный текст'}]
    response = client.post('/api/clips/clip-one/render', json=edit_body(start=12, end=65, title='Новый заголовок', words=words))
    assert response.status_code == 200, response.text
    item = response.json()
    assert item['status'] == 'queued'
    assert item['start'] == 12
    assert item['end'] == 65
    assert item['words'] == words
    assert item['title'] == 'Новый заголовок'
    assert item['download_url'] is None
    assert db.claim()['kind'] == 'render'
    assert client.post('/api/clips/clip-one/render', json=edit_body()).status_code == 409
    assert client.delete('/api/clips/clip-one').status_code == 409
    assert client.delete('/api/projects/project').status_code == 409


@pytest.mark.parametrize('changes', [
    {'start': 70, 'end': 60}, {'end': 130}, {'start': -1}, {'end': 10.5},
    {'settings': {**config.CLIP_DEFAULTS, 'face_box': [.8, 0, .5, .5]}},
    {'words': [{'start': 2, 'end': 1, 'text': 'wrong'}]},
    {'words': [{'start': 1, 'end': 121, 'text': 'outside'}]},
    {'words': [{'start': 10, 'end': 11, 'text': 'first'}, {'start': 2, 'end': 3, 'text': 'second'}]},
])
def test_editor_rejects_invalid_ranges_settings_and_word_timing(client, changes):
    seed_project()
    seed_clip()
    response = client.post('/api/clips/clip-one/render', json=edit_body(**changes))
    assert response.status_code == 422, response.text
    assert not db.active('project')
    assert db.get('clips', 'clip-one')['status'] == 'ready'


def test_deleted_source_blocks_reexport_but_preserves_downloads(client):
    p = seed_project()
    seed_clip()
    Path(p['source_path']).unlink()
    assert client.post('/api/clips/clip-one/render', json=edit_body()).status_code == 410
    assert client.get('/media/source/project').status_code == 410
    assert client.get('/api/clips/clip-one/download').status_code == 200
    assert client.get('/api/projects/project').json()['source_available'] is False


def test_delete_removes_files_and_persistent_metadata(client):
    p = seed_project()
    seed_clip()
    assert client.delete('/api/projects/project').status_code == 200
    assert client.get('/api/projects/project').status_code == 404
    assert client.get('/api/clips/clip-one').status_code == 404
    assert not Path(p['source_path']).exists()
    assert not (config.DATA / 'clips' / 'clip-one.mp4').exists()


def test_retry_failed_project_queues_once_and_rejects_missing_source(client):
    p = seed_project(status='failed')
    first = client.post('/api/projects/project/retry')
    assert first.status_code == 200
    assert first.json()['status'] == 'queued'
    assert db.active('project')
    assert client.post('/api/projects/project/retry').status_code == 409
    running = db.claim()
    db.finish(running['id'], 'failed')
    db.update('projects', 'project', status='failed')
    Path(p['source_path']).unlink()
    assert client.post('/api/projects/project/retry').status_code == 410
    assert not db.active('project')


def test_automatic_source_retention_preserves_exports_recent_and_active_projects(client, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from backend.app import worker
    monkeypatch.setattr(worker, 'DATA', config.DATA)
    old = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
    ready = seed_project('expired')
    seed_clip('expired', 'expired01')
    db.update('projects', ready['id'], updated_at=old)
    # db.update stamps updated_at by design; set this record's historic payload directly.
    ready['updated_at'] = old
    db.put('projects', ready)
    seed_project('recent')
    busy = seed_project('active', status='failed')
    busy['updated_at'] = old
    db.put('projects', busy)
    db.enqueue('active')
    result = client.delete('/api/storage/sources')
    assert result.status_code == 200
    assert result.json() == {'deleted': 1}
    assert not Path(ready['source_path']).exists()
    assert (config.DATA / 'clips' / 'expired01.mp4').is_file()
    assert db.get('projects', 'expired')['source_path'] is None
    assert db.get('projects', 'recent')['source_path']
    assert Path(busy['source_path']).is_file()
    assert client.delete('/api/storage/sources').json() == {'deleted': 0}


def test_dns_rebinding_host_is_rejected_even_for_matching_origin(client):
    response = client.patch('/api/settings', headers={
        'Host': 'attacker.invalid', 'Origin': 'http://attacker.invalid',
    }, json={'max_clips': 2})
    assert response.status_code == 400
    assert db.settings()['max_clips'] != 2


def test_concurrent_editor_requests_queue_exactly_one_render(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    seed_project()
    seed_clip()
    both_checked = Barrier(2)
    original_active = db.active
    def simultaneous_precheck(project_id, clip_id=None):
        value = original_active(project_id, clip_id)
        if clip_id == 'clip-one':
            both_checked.wait(timeout=5)
        return value
    monkeypatch.setattr(db, 'active', simultaneous_precheck)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(client.post, '/api/clips/clip-one/render', json=edit_body(title=f'Edit {i}')) for i in range(2)]
        responses = [future.result(timeout=10) for future in futures]
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert db.claim()['kind'] == 'render'
    assert db.claim() is None
