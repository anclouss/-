"""New editor HTTP contracts; real persistence and media, deterministic LLM IO."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

from fastapi.testclient import TestClient
import pytest
from backend.app import config, db, main, editor_store, editor_api, editor_ai, editor_jobs, worker


@pytest.fixture(scope='module')
def real_editor_video(tmp_path_factory):
    directory=tmp_path_factory.mktemp('editor-source')
    path=directory/'source.mp4'
    subprocess.run(['ffmpeg','-v','error','-nostdin','-y','-f','lavfi','-i','testsrc2=size=320x180:rate=12:duration=3',
                    '-f','lavfi','-i','sine=frequency=440:sample_rate=16000:duration=3',
                    '-c:v','libx264','-threads','2','-preset','ultrafast','-pix_fmt','yuv420p','-c:a','aac',str(path)],check=True)
    return path


@pytest.fixture
def editor(tmp_path,monkeypatch,real_editor_video):
    for module in (config,db,main,editor_api,editor_jobs,worker):
        monkeypatch.setattr(module,'DATA',tmp_path)
    config.prepare();db.init();editor_store.init();editor_ai.init()
    db.save_settings({'analysis_provider':'ollama'})
    source=tmp_path/'sources'/'project.mp4';shutil.copyfile(real_editor_video,source)
    project=main.new_project('Исходный проект','source.mp4',source,duration=3)
    project.update(id='project',status='completed',stage='done',progress=100,
                   media={'duration':3,'width':320,'height':180,'fps':12,'has_audio':True})
    db.put('projects',project)
    clip={'id':'clip','project_id':'project','start':0.,'end':3.,'title':'Старый клип','reason':'Момент',
          'score':73,'status':'ready','revision':2,'settings':dict(config.CLIP_DEFAULTS),
          'words':[{'start':.1,'end':.4,'text':'Привет'},{'start':1.,'end':1.4,'text':'нож!'}],
          'created_at':db.now(),'error':None}
    db.put('clips',clip)
    (tmp_path/'clips'/'clip.mp4').write_bytes(b'legacy-export-0123456789')
    (tmp_path/'transcripts'/'project.json').write_text(json.dumps(clip['words'],ensure_ascii=False),encoding='utf-8')
    (tmp_path/'signals'/'project.json').write_text(json.dumps({'pauses':[{'start':1.5,'end':2.8}]}),encoding='utf-8')
    with TestClient(main.app) as client:
        yield client,tmp_path


def get_session(client):
    result=client.get('/api/editor/clip')
    assert result.status_code==200,result.text
    return result.json()


def job_count():
    with db.connection() as c:
        return c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]


def test_opening_legacy_clip_migrates_without_render_and_preserves_export(editor):
    client,path=editor
    session=get_session(client)
    assert session['revision']==0
    assert session['state']['segments'][0]['start']==0
    assert session['source']['available'] is True
    assert session['clip']['words']==[]  # transcript is loaded through bounded endpoint
    assert session['history']['can_undo'] is False
    assert session['messages']==[] and session['proposals']==[]
    assert job_count()==0
    assert client.get('/api/clips/clip/download').content==b'legacy-export-0123456789'
    assert get_session(client)['state']==session['state']


def test_autosave_stale_revision_and_history_survive_reconnect(editor):
    client,path=editor
    initial=get_session(client)
    changed=deepcopy(initial['state']);changed['settings']['subtitle_size']=84
    response=client.patch('/api/editor/clip',json={'revision':0,'state':changed,'label':'Крупнее'})
    assert response.status_code==200,response.text
    assert response.json()['revision']==1
    assert client.patch('/api/editor/clip',json={'revision':0,'state':initial['state']}).status_code==409
    assert get_session(client)['state']['settings']['subtitle_size']==84
    undone=client.post('/api/editor/clip/history',json={'revision':1,'action':'undo'})
    assert undone.status_code==200 and undone.json()['revision']==2
    assert undone.json()['state']==initial['state']
    redone=client.post('/api/editor/clip/history',json={'revision':2,'action':'redo'}).json()
    assert redone['revision']==3 and redone['state']==changed
    reset=client.post('/api/editor/clip/history',json={'revision':3,'action':'reset'}).json()
    assert reset['revision']==4 and reset['state']==initial['state']
    assert job_count()==0
    assert client.get('/api/clips/clip/download').status_code==200


def test_timeline_actions_validate_and_use_selection_without_rerender(editor):
    client,path=editor
    get_session(client)
    result=client.post('/api/editor/clip/actions',json={'revision':0,'actions':[{'type':'cut'}],
                      'context':{'selection':{'start':1,'end':2}}})
    assert result.status_code==200,result.text
    assert [(s['start'],s['end']) for s in result.json()['state']['segments']]==[(0,1),(2,3)]
    for action in ({'type':'shell','command':'touch /tmp/should-never-run'},
                   {'type':'cut','start':0,'end':99},
                   {'type':'zoom','scale':9}):
        response=client.post('/api/editor/clip/actions',json={'revision':1,'actions':[action]})
        assert response.status_code==422,response.text
    assert get_session(client)['revision']==1 and job_count()==0


def test_transcript_is_bounded_and_caption_overrides_are_persisted(editor):
    client,path=editor
    session=get_session(client)
    words=[{'start':i/1000,'end':i/1000+.0005,'text':f'word{i}'} for i in range(1000)]
    (path/'transcripts'/'project.json').write_text(json.dumps(words),encoding='utf-8')
    response=client.get('/api/editor/clip/transcript?offset=400&limit=200')
    assert response.status_code==200 and response.json()['total']==1000
    assert len(response.json()['words'])==200 and response.json()['offset']==400
    assert client.get('/api/editor/clip/transcript?limit=201').status_code==422
    state=deepcopy(session['state']);state['caption_overrides']['0.4']='Исправлено'
    assert client.patch('/api/editor/clip',json={'revision':0,'state':state}).status_code==200
    page=client.get('/api/editor/clip/transcript?offset=400&limit=1').json()
    assert page['words'][0]['text']=='Исправлено'


def test_chat_proposal_preview_apply_cancel_and_undo_are_distinct(editor,monkeypatch):
    client,path=editor
    get_session(client)
    calls=[]
    def model(system,prompt,config):
        calls.append(prompt)
        return {'summary':'Увеличить субтитры','actions':[{'type':'subtitle_style','size':84}]}
    monkeypatch.setattr(editor_ai,'_llm',model)
    response=client.post('/api/editor/clip/chat',json={'revision':0,'prompt':'Сделай субтитры крупнее','context':{'playhead':1}})
    assert response.status_code==200,response.text
    session=response.json(); proposal=session['proposals'][0]
    assert session['revision']==0 and session['state']['settings']['subtitle_size']==58
    assert proposal['state']['settings']['subtitle_size']==84
    assert proposal['status']=='pending' and len(session['messages'])==2
    assert job_count()==0
    # Repeating the same request uses the persisted proposal/cache.
    again=client.post('/api/editor/clip/chat',json={'revision':0,'prompt':'Сделай субтитры крупнее','context':{'playhead':1}})
    assert again.status_code==200 and len(calls)==1
    applied=client.post(f"/api/editor/clip/proposals/{proposal['id']}",json={'revision':0,'action':'apply'})
    assert applied.status_code==200,applied.text
    assert applied.json()['revision']==1 and applied.json()['state']['settings']['subtitle_size']==84
    assert applied.json()['proposals'][0]['status']=='applied' and job_count()==0
    assert client.post('/api/editor/clip/history',json={'revision':1,'action':'undo'}).json()['revision']==2
    fresh=client.post('/api/editor/clip/chat',json={'revision':2,'prompt':'Снова увеличь субтитры','context':{}}).json()
    proposed=fresh['proposals'][-1]
    cancelled=client.post(f"/api/editor/clip/proposals/{proposed['id']}",json={'revision':2,'action':'cancel'})
    assert cancelled.status_code==200 and cancelled.json()['revision']==2
    assert cancelled.json()['proposals'][-1]['status']=='cancelled'
    assert get_session(client)['messages'][-1]['status']=='cancelled'


def test_chat_rejects_shell_json_and_stale_proposal_without_mutation(editor,monkeypatch):
    client,path=editor
    get_session(client)
    monkeypatch.setattr(editor_ai,'_llm',lambda *a:{'summary':'Недопустимо','actions':[{'type':'shell','command':'echo nope'}]})
    rejected=client.post('/api/editor/clip/chat',json={'revision':0,'prompt':'Выполни команду','context':{}})
    assert rejected.status_code==422,rejected.text
    assert get_session(client)['revision']==0 and job_count()==0
    monkeypatch.setattr(editor_ai,'_llm',lambda *a:{'summary':'Крупный текст','actions':[{'type':'subtitle_style','size':84}]})
    proposal=client.post('/api/editor/clip/chat',json={'revision':0,'prompt':'Крупный текст','context':{}}).json()['proposals'][-1]
    assert client.post('/api/editor/clip/actions',json={'revision':0,'actions':[{'type':'subtitles','enabled':False}]}).status_code==200
    assert client.post(f"/api/editor/clip/proposals/{proposal['id']}",json={'revision':1,'action':'apply'}).status_code==409
    assert get_session(client)['state']['settings']['subtitles'] is False


def test_manual_editor_is_reused_and_stays_editable_without_transcript(editor):
    client,path=editor
    (path/'transcripts'/'project.json').unlink()
    first=client.post('/api/projects/project/editor')
    assert first.status_code==201,first.text
    second=client.post('/api/projects/project/editor')
    assert second.json()['id']==first.json()['id']
    ident=first.json()['id']
    details=client.get(f'/api/editor/{ident}')
    assert details.status_code==200 and details.json()['state']['segments'][0]['end']==3
    assert client.get(f'/api/editor/{ident}/transcript').json()['words']==[]


def test_cancel_inflight_chat_discards_proposal_and_does_not_change_revision(editor,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    client,path=editor
    get_session(client)
    entered=threading.Event();release=threading.Event()
    def model(*args):
        entered.set()
        assert release.wait(5)
        return {'summary':'Поздний ответ','actions':[{'type':'subtitles','enabled':False}]}
    monkeypatch.setattr(editor_ai,'_llm',model)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(client.post,'/api/editor/clip/chat',json={'revision':0,'prompt':'Убери субтитры','context':{},'request_id':'cancel-test'})
        assert entered.wait(5)
        try:
            cancelled=client.post('/api/editor/clip/chat/cancel',json={'request_id':'cancel-test'})
            assert cancelled.status_code==200 and cancelled.json()['cancelled'] is True
        finally:
            release.set()
        response=future.result(5)
    assert response.status_code==422,response.text
    session=get_session(client)
    assert session['revision']==0 and session['proposals']==[] and job_count()==0
    assert session['messages'][-1]['status']=='cancelled'


def test_preview_proposal_queues_its_snapshot_without_applying_document(editor,monkeypatch):
    client,path=editor
    get_session(client)
    monkeypatch.setattr(editor_ai,'_llm',lambda *a:{'summary':'Увеличить текст','actions':[{'type':'subtitle_style','size':84}]})
    response=client.post('/api/editor/clip/chat',json={'revision':0,'prompt':'Крупнее','context':{}})
    assert response.status_code==200,response.text
    proposal=response.json()['proposals'][0]
    preview=client.post('/api/editor/clip/preview',json={'revision':0,'quality':'480p','proposal_id':proposal['id']})
    assert preview.status_code==202,preview.text
    row=editor_jobs.job(preview.json()['id'])
    payload=json.loads(row['payload'])
    manifest=json.loads(Path(payload['manifest']).read_text(encoding='utf-8'))
    assert manifest['state']['settings']['subtitle_size']==84
    session=get_session(client)
    assert session['revision']==0 and session['state']['settings']['subtitle_size']==58
    assert session['proposals'][0]['status']=='pending'


def test_raw_editor_accepts_silent_video_and_auto_mode_requires_audio(editor,monkeypatch,real_editor_video):
    client,path=editor
    monkeypatch.setattr(main,'probe',lambda _: {'duration':3,'width':320,'height':180,'fps':12,'has_audio':False})
    data=real_editor_video.read_bytes()
    uploaded=client.post('/api/projects/upload',files={'file':('silent.mp4',data,'video/mp4')},data={'mode':'editor'})
    assert uploaded.status_code==201,uploaded.text
    created=uploaded.json()
    manual=client.post(f"/api/projects/{created['id']}/editor")
    assert manual.status_code==201
    assert client.get(f"/api/editor/{manual.json()['id']}").json()['source']['available']
    assert client.post('/api/projects/upload',files={'file':('silent.mp4',data,'video/mp4')},data={'mode':'auto'}).status_code==400


def test_upload_body_limit_rejects_before_persisting(editor,monkeypatch):
    from backend.app import upload_limit
    client,path=editor
    monkeypatch.setattr(upload_limit,'MAX_BYTES',16)
    response=client.post('/api/projects/upload',content=b'',headers={'content-length':str(3*1024**2),'content-type':'multipart/form-data; boundary=unused'})
    assert response.status_code==413
    assert len(db.all_projects())==1


def test_raw_editor_can_queue_highlights_without_uploading_or_rendering_again(editor):
    client,path=editor
    get_session(client)
    first=client.post('/api/projects/project/highlights')
    second=client.post('/api/projects/project/highlights')
    assert first.status_code==second.status_code==202
    assert first.json()['status']=='queued'
    assert first.json()['defer_render'] is True
    with db.connection() as con:
        queued=con.execute("SELECT kind FROM jobs WHERE status='queued'").fetchall()
    assert [row['kind'] for row in queued]==['analyze']
    assert get_session(client)['revision']==0


def test_chat_uses_real_ollama_http_adapter_with_bounded_safe_payload(editor,monkeypatch):
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    import threading
    client,path=editor
    get_session(client)
    calls=[]
    class ModelServer(BaseHTTPRequestHandler):
        def do_POST(self):
            payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(payload)
            plan={'summary':'Обрезать начало и увеличить субтитры','actions':[{'type':'trim','start':.5,'end':3},{'type':'subtitle_style','size':82}]}
            body=json.dumps({'message':{'content':json.dumps(plan,ensure_ascii=False)}}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def log_message(self,*args):
            pass
    server=ThreadingHTTPServer(('127.0.0.1',0),ModelServer)
    thread=threading.Thread(target=lambda:server.serve_forever(poll_interval=.02),daemon=True);thread.start()
    monkeypatch.setenv('OLLAMA_BASE_URL',f'http://127.0.0.1:{server.server_address[1]}')
    try:
        result=client.post('/api/editor/clip/chat',json={'revision':0,'prompt':'Обрежь первые полсекунды и увеличь субтитры','context':{'playhead':0}})
        assert result.status_code==200,result.text
        proposal=result.json()['proposals'][-1]
        assert proposal['state']['segments'][0]['start']==.5
        assert proposal['state']['settings']['subtitle_size']==82
        assert result.json()['revision']==0
        assert len(calls)==1 and calls[0]['format']=='json'
        wire=json.dumps(calls[0],ensure_ascii=False)
        assert str(path) not in wire and '_source_path' not in wire
        assert len(wire)<35000
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)
