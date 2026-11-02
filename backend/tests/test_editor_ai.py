"""Real editor state/SQLite integration; only external LLM responses are mocked."""
from copy import deepcopy
import json

import pytest

from backend.app import config, db, editing, editor_ai, editor_store
from backend.app.ai import AIError


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(config,'DATA',tmp_path)
    monkeypatch.setattr(db,'DATA',tmp_path)
    db.init(); editor_store.init(); editor_ai.init()
    project={'id':'project','title':'Запись','duration':5000.,'status':'completed','created_at':db.now(),'updated_at':db.now()}
    clip={'id':'clip','project_id':'project','start':100.,'end':160.,'settings':dict(config.CLIP_DEFAULTS),'words':[]}
    db.put('projects',project); db.put('clips',clip)
    return editor_store.ensure(clip,project)


def propose(monkeypatch, response, *, prompt='Обрежь первые5 секунд и добавь крупные субтитры', context=None, words=None, signals=None, revision=0):
    calls=[]
    def llm(system,user,config):
        calls.append((system,json.loads(user),config))
        return deepcopy(response)
    monkeypatch.setattr(editor_ai,'_llm',llm)
    result=editor_ai.propose('clip',revision,prompt,context or {'playhead':0},words or [],signals or {},{'analysis_provider':'ollama','ollama_model':'test'})
    return result,calls


def test_chat_proposes_trim_and_captions_without_mutation_then_apply_undo_redo(session,monkeypatch):
    proposal,calls=propose(monkeypatch,{'summary':'Обрезать первые5с и добавить крупные субтитры','actions':[{'type':'trim','start':5,'end':60},{'type':'subtitles','enabled':True},{'type':'subtitle_style','preset':'large'}]})
    assert len(calls)==1
    assert proposal['base_revision']==0 and proposal['status']=='pending'
    assert proposal['state']['segments'][0]['start']==105
    assert proposal['state']['settings']['subtitle_size']>58
    assert editor_store.get('clip')['state']==session['state']
    assert len(editor_ai.messages('clip'))==2
    current=editor_ai.decision('clip',proposal['id'],0,'apply')
    assert current['revision']==1
    assert current['state']['segments'][0]['start']==105
    assert editor_ai.proposals('clip')[0]['status']=='applied'
    assert editor_ai.messages('clip')[-1]['status']=='applied'
    repeated=editor_ai.decision('clip',proposal['id'],0,'apply')
    assert repeated['revision']==1
    undo=editor_store.history('clip',1,'undo')
    assert undo['state']==session['state']
    redo=editor_store.history('clip',2,'redo')
    assert redo['state']['segments'][0]['start']==105


def test_cancel_retains_conversation_but_cannot_apply_and_cache_avoids_new_provider_call(session,monkeypatch):
    response={'summary':'Включить субтитры','actions':[{'type':'subtitles','enabled':False}]}
    proposal,calls=propose(monkeypatch,response)
    assert editor_ai.decision('clip',proposal['id'],0,'cancel')['revision']==0
    assert editor_ai.proposals('clip')[0]['status']=='cancelled'
    with pytest.raises(ValueError):
        editor_ai.decision('clip',proposal['id'],0,'apply')
    second=editor_ai.propose('clip',0,'Обрежь первые5 секунд и добавь крупные субтитры',{'playhead':0},[],{}, {'analysis_provider':'ollama','ollama_model':'test'})
    assert len(calls)==1
    assert second['id']!=proposal['id'] and second['status']=='pending'
    assert editor_store.get('clip')['revision']==0


def test_identical_pending_prompt_returns_same_proposal_without_duplicate_chat(session,monkeypatch):
    proposal,calls=propose(monkeypatch,{'summary':'Текст крупнее','actions':[{'type':'subtitle_style','size':90}]})
    repeated=editor_ai.propose('clip',0,'Обрежь первые5 секунд и добавь крупные субтитры',{'playhead':0},[],{}, {'analysis_provider':'ollama','ollama_model':'test'})
    assert repeated['id']==proposal['id']
    assert len(calls)==1 and len(editor_ai.messages('clip'))==2


@pytest.mark.parametrize('response',[
    'not a JSON object',
    {'summary':'x','actions':'trim'},
    {'summary':'x','actions':[{'type':'shell','command':'rm -rf /'}]},
    {'summary':'x','actions':[{'type':'trim','start':0,'end':999999}]},
    {'summary':'x','actions':[{'type':'subtitles','enabled':True,'command':'curl secret'}]},
    {'summary':'x','actions':[],'shell':'touch /tmp/should-not-exist'},
])
def test_malformed_or_unsafe_plan_is_recorded_but_never_applied(session,monkeypatch,response):
    with pytest.raises(AIError):
        propose(monkeypatch,response)
    assert editor_store.get('clip')['state']==session['state']
    assert editor_ai.proposals('clip')==[]
    chat=editor_ai.messages('clip')
    assert chat[-1]['role']=='assistant' and chat[-1]['status']=='error'
    assert 'rm -rf' not in chat[-1]['content']


def test_stale_request_is_rejected_before_paid_call(session,monkeypatch):
    editor_store.save('clip',0,editing.apply_actions(session['state'],[{'type':'subtitles','enabled':False}],5000.),'Изменение')
    def forbidden(*args):
        raise AssertionError('provider must not be called')
    monkeypatch.setattr(editor_ai,'_llm',forbidden)
    with pytest.raises(editor_store.ConflictError):
        editor_ai.propose('clip',0,'Субтитры',{},[],{}, {'analysis_provider':'openai'})
    assert editor_ai.messages('clip')==[]


def test_state_change_while_llm_runs_discards_completion(session,monkeypatch):
    def llm(*args):
        changed=editing.apply_actions(session['state'],[{'type':'subtitles','enabled':False}],5000.)
        editor_store.save('clip',0,changed,'Ручная правка')
        return {'summary':'Зум','actions':[{'type':'zoom','start':0,'end':10,'scale':1.2}]}
    monkeypatch.setattr(editor_ai,'_llm',llm)
    with pytest.raises(editor_store.ConflictError):
        editor_ai.propose('clip',0,'Приблизь лицо',{'playhead':0},[],{}, {'analysis_provider':'ollama'})
    assert editor_ai.proposals('clip')==[]
    assert editor_store.get('clip')['revision']==1
    assert editor_ai.messages('clip')[-1]['status']=='stale'


def test_pending_proposal_cannot_overwrite_newer_manual_edit(session,monkeypatch):
    proposal,_=propose(monkeypatch,{'summary':'Субтитры','actions':[{'type':'subtitle_style','size':90}]})
    changed=editing.apply_actions(session['state'],[{'type':'subtitles','enabled':False}],5000.)
    editor_store.save('clip',0,changed,'Ручная правка')
    with pytest.raises(editor_store.ConflictError):
        editor_ai.decision('clip',proposal['id'],1,'apply')
    assert editor_store.get('clip')['state']['settings']['subtitles'] is False
    # A stale proposal may still be dismissed without editing the document.
    assert editor_ai.decision('clip',proposal['id'],0,'cancel')['revision']==1


def test_abort_after_provider_response_creates_no_proposal_and_saves_cancelled_message(session,monkeypatch):
    cancelled=[False]
    def llm(*args):
        cancelled[0]=True
        return {'summary':'Субтитры','actions':[{'type':'subtitles','enabled':False}]}
    monkeypatch.setattr(editor_ai,'_llm',llm)
    with pytest.raises(editor_ai.ChatCancelled):
        editor_ai.propose('clip',0,'Субтитры',{'playhead':0,'_cancel':lambda:cancelled[0]},[],{}, {'analysis_provider':'ollama'})
    assert editor_ai.proposals('clip')==[]
    assert editor_store.get('clip')['revision']==0
    assert editor_ai.messages('clip')[-1]['status']=='cancelled'


def test_heuristic_does_not_pretend_to_be_semantic_chat(session,monkeypatch):
    monkeypatch.setattr(editor_ai,'_llm',lambda *a:pytest.fail('not a semantic provider'))
    with pytest.raises(AIError,match='Ollama'):
        editor_ai.propose('clip',0,'Сделай хук',{},[],{}, {'analysis_provider':'heuristic'})
    assert editor_ai.messages('clip')[-1]['status']=='error'
    assert editor_ai.proposals('clip')==[]


def test_long_stream_retrieval_targets_query_far_from_playhead_and_caps_prompt(session,monkeypatch):
    words=[{'start':float(i),'end':i+.8,'text':('Он говорит про нож.' if i==4200 else 'Обычная речь.')} for i in range(4900)]
    proposal,calls=propose(monkeypatch,{'summary':'Нашёл упоминание ножа','actions':[{'type':'find_moments','query':'нож'}]},
                           prompt='Найди момент, где он говорит про нож',words=words)
    payload=calls[0][1]
    assert len(json.dumps(payload,ensure_ascii=False,separators=(',',':')))<=editor_ai.MAX_CONTEXT_CHARS
    retrieved=payload['transcript_context']
    assert retrieved['word_count']<=editor_ai.MAX_TRANSCRIPT_WORDS
    assert retrieved['source_word_count']==4900
    assert any(item['start']<=4200<=item['end'] for item in retrieved['matches'])
    assert 'нож' in retrieved['transcript']
    assert proposal['state']['segments'][0]['start']>4000
    assert editor_store.get('clip')['revision']==0


def test_clarifying_question_has_no_executable_proposal(session,monkeypatch):
    response,calls=propose(monkeypatch,{'summary':'После какой фразы удалить участок? Выделите его на timeline.','actions':[]},prompt='Удали после этой фразы')
    assert response['status']=='clarification' and response['id'] is None
    assert editor_ai.proposals('clip')==[]
    assert editor_ai.messages('clip')[-1]['status']=='clarification'
    assert editor_store.get('clip')['revision']==0


def test_chat_and_proposals_survive_reload_and_are_cascade_deleted_with_clip(session,monkeypatch):
    proposal,_=propose(monkeypatch,{'summary':'Субтитры','actions':[{'type':'subtitles','enabled':False}]})
    editor_ai.init();editor_store.init()
    assert editor_ai.proposals('clip')[0]['id']==proposal['id']
    assert len(editor_ai.messages('clip'))==2
    db.delete('clips','clip')
    assert editor_ai.proposals('clip')==[] and editor_ai.messages('clip')==[]
    with db.connection() as con:
        assert con.execute('SELECT count(*) FROM editor_ai_cache').fetchone()[0]==0


def test_cancel_releases_same_prompt_and_old_completion_cannot_replace_new_proposal(session,monkeypatch):
    calls=[]
    cancelled=[False]
    config={'analysis_provider':'ollama','ollama_model':'test'}
    prompt='Сделай крупнее субтитры'
    def llm(*args):
        calls.append(1)
        if len(calls)==1:
            assert editor_ai.cancel_requests('clip')==1
            cancelled[0]=True
            # A retry arrives before the original provider response finishes.
            retry=editor_ai.propose('clip',0,prompt,{'playhead':0},[],{},config)
            assert retry['summary']=='Новый ответ'
            return {'summary':'Устаревший ответ','actions':[{'type':'subtitle_style','size':70}]}
        return {'summary':'Новый ответ','actions':[{'type':'subtitle_style','size':90}]}
    monkeypatch.setattr(editor_ai,'_llm',llm)
    with pytest.raises(editor_ai.ChatCancelled):
        editor_ai.propose('clip',0,prompt,{'playhead':0,'_cancel':lambda:cancelled[0]},[],{},config)
    pending=editor_ai.proposals('clip')
    assert len(pending)==1 and pending[0]['summary']=='Новый ответ'
    assert len(calls)==2
    cached=editor_ai.propose('clip',0,prompt,{'playhead':0},[],{},config)
    assert cached['id']==pending[0]['id'] and len(calls)==2
    cancelled_messages=[m for m in editor_ai.messages('clip') if m['role']=='assistant' and m['status']=='cancelled']
    assert len(cancelled_messages)==1
    assert editor_store.get('clip')['revision']==0
