"""Immutable render snapshots and actual preview/export lifecycle."""
from copy import deepcopy
import json
from pathlib import Path
import pytest

from test_editor_api import editor, real_editor_video, get_session, job_count
from backend.app import db,editor_api,editor_jobs,editor_store,worker
from backend.app.editing import document_duration
from backend.app.media import probe


def test_export_snapshot_cache_quality_and_old_export_availability(editor):
    client,path=editor
    current=get_session(client)
    body={'revision':0,'resolution':'720p','preset':'fast','fps':'30','codec':'h264','platform':'tiktok','subtitles':True,'watermark':''}
    response=client.post('/api/editor/clip/exports',json=body)
    assert response.status_code==202,response.text
    first=response.json()
    again=client.post('/api/editor/clip/exports',json=body).json()
    assert again['id']==first['id'] and job_count()==1
    assert first['estimated_bytes']>0
    assert client.get('/api/clips/clip/download').content==b'legacy-export-0123456789'
    assert client.delete('/api/projects/project').status_code==409
    assert client.delete('/api/clips/clip').status_code==409
    changed=deepcopy(current['state']);changed['settings']['subtitle_size']=84
    assert client.patch('/api/editor/clip',json={'revision':0,'state':changed}).status_code==200
    row=editor_jobs.job(first['id']);payload=json.loads(row['payload'])
    manifest=json.loads(Path(payload['manifest']).read_text(encoding='utf-8'))
    assert manifest['state']['settings']['subtitle_size']==58
    assert payload['revision']==0
    assert client.post('/api/editor/clip/exports',json=body).status_code==409
    different=client.post('/api/editor/clip/exports',json={**body,'revision':1,'resolution':'1080p'}).json()
    assert different['id']!=first['id']
    assert client.post('/api/editor/clip/exports',json={**body,'revision':1,'resolution':'4k'}).status_code==422


def test_proxy_quality_is_cached_per_source_and_survives_editor_changes(editor):
    client,path=editor
    current=get_session(client)
    low=client.post('/api/editor/clip/proxy',json={'quality':'360p'})
    assert low.status_code==202,low.text
    ident=low.json()['id']
    assert client.post('/api/editor/clip/proxy',json={'quality':'360p'}).json()['id']==ident
    high=client.post('/api/editor/clip/proxy',json={'quality':'720p'}).json()
    assert high['id']!=ident
    changed=deepcopy(current['state']);changed['settings']['subtitle_size']=84
    client.patch('/api/editor/clip',json={'revision':0,'state':changed})
    assert client.post('/api/editor/clip/proxy',json={'quality':'360p'}).json()['id']==ident


def test_queued_cancel_retry_and_crash_recovery_preserve_job_snapshot(editor):
    client,path=editor
    get_session(client)
    queued=client.post('/api/editor/clip/exports',json={'revision':0,'resolution':'720p'}).json()
    ident=queued['id'];before=editor_jobs.job(ident)['payload']
    cancel=client.post(f'/api/editor-jobs/{ident}/cancel')
    assert cancel.status_code==200 and cancel.json()['status']=='cancelled'
    assert db.claim() is None
    retried=client.post(f'/api/editor-jobs/{ident}/retry')
    assert retried.status_code==200 and retried.json()['status']=='queued'
    claimed=db.claim();assert claimed['id']==ident
    db.recover()
    assert editor_jobs.job(ident)['status']=='queued'
    assert editor_jobs.job(ident)['payload']==before
    claimed=db.claim()
    assert client.post(f'/api/editor-jobs/{ident}/cancel').json()['status']=='running'
    db.recover()
    assert editor_jobs.job(ident)['status']=='cancelled'
    assert client.get(f'/media/editor/{ident}').status_code==409


def test_real_edl_preview_export_range_and_reexport_reuse(editor):
    client,path=editor
    session=get_session(client)
    result=client.post('/api/editor/clip/actions',json={'revision':0,'actions':[{'type':'cut','start':1,'end':1.5},{'type':'zoom','start':.2,'end':.8,'scale':1.15}]})
    assert result.status_code==200,result.text
    assert document_duration(result.json()['state'])==2.5
    preview=client.post('/api/editor/clip/preview',json={'revision':1,'quality':'360p'})
    assert preview.status_code==202,preview.text
    row=db.claim();assert row['id']==preview.json()['id']
    editor_jobs.run(row)
    ready=client.get(f"/api/editor-jobs/{row['id']}").json()
    assert ready['status']=='done' and ready['url']
    info=probe(Path(json.loads(editor_jobs.job(row['id'])['payload'])['output']))
    assert (info['width'],info['height'])==(360,640)
    assert abs(info['duration']-2.5)<.2
    ranged=client.get(ready['url'],headers={'Range':'bytes=0-31'})
    assert ranged.status_code==206 and len(ranged.content)==32
    assert client.post('/api/editor/clip/preview',json={'revision':1,'quality':'360p'}).json()['id']==row['id']
    exported=client.post('/api/editor/clip/exports',json={'revision':1,'resolution':'720p','preset':'fast','fps':'30'}).json()
    row=db.claim();assert row['id']==exported['id']
    editor_jobs.run(row)
    info=probe(Path(json.loads(editor_jobs.job(row['id'])['payload'])['output']))
    assert (info['width'],info['height'])==(720,1280)
    completed=client.get(f"/api/editor-jobs/{row['id']}").json()
    download=client.get(completed['download_url'])
    assert download.status_code==200 and len(download.content)>1000
    assert get_session(client)['revision']==1
    assert (path/'clips'/'clip.mp4').read_bytes()==b'legacy-export-0123456789'
    assert client.get('/api/clips/clip/download').content==download.content


def test_legacy_media_subprocess_is_actually_cancelled():
    import sys
    import time
    from backend.app import job_control
    from backend.app.media import _run
    started=time.monotonic()
    def cancel():
        if time.monotonic()-started>.12:
            raise editor_jobs.JobCancelled('cancelled')
    token=job_control.install(cancel)
    try:
        with pytest.raises(editor_jobs.JobCancelled):
            _run([sys.executable,'-c','import time; time.sleep(10)'],timeout=20)
    finally:
        job_control.reset(token)
    assert time.monotonic()-started<3


def test_export_snapshot_only_retains_words_inside_source_segments():
    document={'segments':[{'start':10,'end':12},{'start':0,'end':1},{'start':10.5,'end':11}]}
    words=[{'start':0,'end':.5,'text':'yes'}, {'start':5,'end':6,'text':'no'}, {'start':10.7,'end':11,'text':'once'}, {'start':20,'end':21,'text':'no'}]
    assert [w['text'] for w in editor_jobs.relevant_words(document,words)]==['yes','once']
