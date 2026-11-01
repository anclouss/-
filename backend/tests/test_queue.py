"""Durable queue invariants: claims, crash recovery, isolation and cascades."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.app import config, db


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DATA', tmp_path)
    monkeypatch.setattr(db, 'DATA', tmp_path)
    db.init()
    return db


def project(ident):
    return {'id': ident, 'title': ident, 'status': 'queued', 'created_at': db.now()}


def test_claims_are_atomic_and_jobs_survive_connection_restart(store):
    store.put('projects', project('stream'))
    for number in range(12):
        store.enqueue('stream', kind='render', clip_id=f'clip{number}')
    # Competing connections must never claim the same persistent job twice.
    with ThreadPoolExecutor(max_workers=6) as pool:
        claimed = list(pool.map(lambda _: store.claim(), range(18)))
    jobs = [job for job in claimed if job]
    assert len(jobs) == 12
    assert len({job['id'] for job in jobs}) == 12
    assert store.claim() is None
    assert store.active('stream')
    for job in jobs:
        store.finish(job['id'])
    assert not store.active('stream')
    # Reinitialization leaves persisted completed records intact.
    store.init()
    assert store.claim() is None
    assert store.get('projects', 'stream')['title'] == 'stream'


def test_crash_recovery_requeues_only_running_work(store):
    store.put('projects', project('stream'))
    store.enqueue('stream')
    done = store.claim()
    store.finish(done['id'])
    store.enqueue('stream', 'render', 'clip')
    interrupted = store.claim()
    store.enqueue('stream', 'render', 'next')
    store.recover()
    assert store.claim()['id'] == interrupted['id']
    assert store.claim()['clip_id'] == 'next'
    assert store.claim() is None


def test_project_delete_cascades_jobs_and_clips_only_for_that_project(store):
    for ident in ('remove', 'keep'):
        store.put('projects', project(ident))
        store.put('clips', {'id': f'{ident}-clip', 'project_id': ident})
        store.enqueue(ident)
    store.delete('projects', 'remove')
    assert store.get('projects', 'remove') is None
    assert store.clips('remove') == []
    assert store.get('clips', 'keep-clip') is not None
    assert store.claim()['project_id'] == 'keep'
    assert store.claim() is None


def test_analysis_blocks_edits_for_all_clips_and_render_blocks_its_clip(store):
    store.put('projects', project('stream'))
    store.enqueue('stream', 'render', 'first')
    assert store.active('stream', 'first')
    assert not store.active('stream', 'second')
    render = store.claim()
    store.finish(render['id'], 'encoder failed')
    assert not store.active('stream', 'first')
    store.enqueue('stream')
    assert store.active('stream', 'first')
    assert store.active('stream', 'second')


def test_updates_preserve_fields_and_settings_survive_restart(store):
    store.put('projects', project('stream'))
    store.update('projects', 'stream', status='processing', progress=42)
    persisted = store.get('projects', 'stream')
    assert persisted['title'] == 'stream'
    assert persisted['progress'] == 42
    assert persisted['updated_at']
    assert store.update('projects', 'missing', status='failed') is None
    store.save_settings({'max_clips': 3, 'analysis_provider': 'heuristic'})
    store.init()
    assert store.settings()['max_clips'] == 3
    assert store.settings()['analysis_provider'] == 'heuristic'
    assert store.settings()['transcription_provider'] == config.DEFAULTS['transcription_provider']
