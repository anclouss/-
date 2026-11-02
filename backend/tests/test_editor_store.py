from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import pytest
from backend.app import config,db,editor_store
from backend.app.editing import apply_actions


@pytest.fixture
def store(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'DATA',tmp_path)
    monkeypatch.setattr(db,'DATA',tmp_path)
    db.init()
    project={'id':'p','duration':120,'updated_at':'2020-01-01T00:00:00+00:00'}
    clip={'id':'c','project_id':'p','start':10,'end':70,'settings':{},'words':[]}
    db.put('projects',project);db.put('clips',clip)
    editor_store.init()
    yield project,clip


def test_lazy_migration_save_and_restart_preserve_legacy_and_state(store):
    project,clip=store
    first=editor_store.ensure(clip,project)
    assert first['revision']==0 and len(first['history']['entries'])==1
    state=apply_actions(first['state'],[{'type':'trim','start':5}],120)
    saved=editor_store.save('c',0,state,'Trim')
    assert saved['revision']==1 and saved['state']['segments'][0]['start']==15
    assert db.get('clips','c')==clip
    assert editor_store.get('c')==saved
    assert editor_store.ensure(clip,project)==saved
    assert db.get('projects','p')['updated_at']!=project['updated_at']


def test_cas_allows_only_one_concurrent_autosave(store):
    first=editor_store.ensure(store[1],store[0])
    def save(size):
        state=deepcopy(first['state']);state['settings']['subtitle_size']=size
        try: return editor_store.save('c',0,state,'Style')['revision']
        except editor_store.ConflictError: return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(save,[70,80]))
    assert sorted(str(x) for x in results)==['1','conflict']


def test_undo_redo_reset_are_durable_and_revision_is_monotonic(store):
    current=editor_store.ensure(store[1],store[0]); initial=deepcopy(current['state'])
    altered=apply_actions(current['state'],[{'type':'trim','start':10}],120)
    editor_store.save('c',0,altered,'Trim')
    undone=editor_store.history('c',1,'undo')
    assert undone['revision']==2 and undone['state']==initial and undone['history']['can_redo']
    redone=editor_store.history('c',2,'redo')
    assert redone['revision']==3 and redone['state']==altered
    reset=editor_store.history('c',3,'reset')
    assert reset['revision']==4 and reset['state']==initial
    assert editor_store.history('c',4,'undo')['state']==altered


def test_new_edit_after_undo_discards_redo_and_noop_does_not_add_history(store):
    current=editor_store.ensure(store[1],store[0])
    assert editor_store.save('c',0,current['state'],'Noop')['revision']==0
    changed=deepcopy(current['state']);changed['settings']['subtitles']=False
    editor_store.save('c',0,changed,'Off')
    editor_store.history('c',1,'undo')
    changed['settings']['subtitle_size']=84
    saved=editor_store.save('c',2,changed,'New branch')
    assert saved['revision']==3 and not saved['history']['can_redo']
    assert len(saved['history']['entries'])==2


def test_hundred_snapshot_cap_keeps_reset_base_and_clip_delete_cascades(store):
    current=editor_store.ensure(store[1],store[0]); initial=deepcopy(current['state'])
    for index in range(105):
        state=deepcopy(current['state']);state['settings']['volume']=1+((index+1)%10)/10
        current=editor_store.save('c',current['revision'],state,f'Edit{index}')
    assert len(current['history']['entries'])==100
    reset=editor_store.history('c',current['revision'],'reset')
    assert reset['state']==initial
    db.delete('clips','c')
    assert editor_store.get('c') is None
    with db.connection() as c:
        assert c.execute('SELECT COUNT(*) FROM editor_history').fetchone()[0]==0


def test_external_transaction_save_is_atomic_with_other_updates(store):
    current=editor_store.ensure(store[1],store[0])
    state=deepcopy(current['state']);state['settings']['subtitles']=False
    with pytest.raises(RuntimeError):
        with db.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            result=editor_store.save('c',0,state,'AI apply',connection=c)
            assert result['revision']==1
            raise RuntimeError('simulate proposal update failure')
    assert editor_store.get('c')['revision']==0
    with db.connection() as c:
        with pytest.raises(ValueError): editor_store.save('c',0,state,'No transaction',connection=c)
