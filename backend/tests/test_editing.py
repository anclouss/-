from copy import deepcopy
import math
import pytest
from backend.app.editing import action_schema, apply_actions, default_document, document_duration, validate_document


def state(start=10,end=70):
    return default_document({'id':'clip','start':start,'end':end,'settings':{},'words':[]},120)


def test_trim_cut_speed_and_hook_keep_absolute_source_coordinates():
    original=state()
    trimmed=apply_actions(original,[{'type':'trim','start':5,'end':55}],120)
    assert [(s['start'],s['end']) for s in trimmed['segments']]==[(15,65)]
    cut=apply_actions(trimmed,[{'type':'cut','start':10,'end':20}],120)
    assert [(s['start'],s['end']) for s in cut['segments']]==[(15,25),(35,65)]
    fast=apply_actions(cut,[{'type':'speed','start':0,'end':10,'value':2}],120)
    assert document_duration(fast)==35
    hooked=apply_actions(fast,[{'type':'create_hook','start':5,'end':15}],120)
    assert [(s['start'],s['end']) for s in hooked['segments']]==[(35,45),(15,25),(45,65)]
    assert document_duration(hooked)==35
    assert original['segments'][0]['start']==10


def test_targeted_effects_split_scope_and_follow_moved_segments():
    result=apply_actions(state(),[{'type':'zoom','start':5,'end':10,'scale':1.6},
                                  {'type':'move','index':1,'to':0}],120)
    assert len(result['segments'])==3
    assert result['segments'][0]['effects']['zoom']==1.6
    assert [(s['start'],s['end']) for s in result['segments']]==[(15,20),(10,15),(20,70)]
    assert not result['segments'][1]['effects']


def test_selection_and_playhead_resolve_without_invented_times():
    result=apply_actions(state(),[{'type':'cut'}],120,{'selection':{'start':5,'end':10}})
    assert [(s['start'],s['end']) for s in result['segments']]==[(10,15),(20,70)]
    result=apply_actions(state(),[{'type':'trim'}],120,{'playhead':5})
    assert result['segments'][0]['start']==15
    with pytest.raises(ValueError):
        apply_actions(state(),[{'type':'cut'}],120)


def test_split_preserves_length_and_source_order():
    result=apply_actions(state(),[{'type':'split','at':15}],120)
    assert [(s['start'],s['end']) for s in result['segments']]==[(10,25),(25,70)]
    assert document_duration(result)==60


def test_silences_are_resolved_to_cuts_without_removing_recognized_words():
    context={'pauses':[{'start':20,'end':25},{'start':40,'end':45}],
             'words':[{'start':41,'end':42,'text':'речь'}]}
    result=apply_actions(state(),[{'type':'remove_silences'}],120,context)
    assert [(s['start'],s['end']) for s in result['segments']]==[(10,20.12),(24.88,70)]
    assert result['settings']['remove_silence'] is False
    assert document_duration(result)<60


def test_find_matches_and_highlights_use_actual_source_ranges():
    found=apply_actions(state(),[{'type':'find_moments','query':'нож'}],120,{'matches':[{'start':70,'end':100}]})
    assert [(s['start'],s['end']) for s in found['segments']]==[(70,100)]
    found=apply_actions(state(),[{'type':'auto_highlights'}],120,{'highlights':[{'start':80,'end':100},{'start':10,'end':25}]})
    assert [s['start'] for s in found['segments']]==[80,10]
    with pytest.raises(ValueError):
        apply_actions(state(),[{'type':'auto_highlights'}],120)


def test_styles_audio_and_tiktok_are_real_document_changes():
    result=apply_actions(state(),[{'type':'subtitles','enabled':False},
       {'type':'subtitle_style','preset':'large','color':'#FF0000'},
       {'type':'volume','value':1.5}, {'type':'normalize_audio','enabled':False},
       {'type':'text_overlay','start':5,'end':10,'text':'HOOK','size':80},
       {'type':'resize','aspect_ratio':'1:1'}, {'type':'tiktok'}],120)
    assert result['settings']['aspect_ratio']=='9:16'
    assert result['settings']['subtitles'] is True
    assert result['settings']['subtitle_size']==76
    assert result['settings']['normalize_audio'] is True
    assert result['settings']['volume']==1.5
    assert result['segments'][1]['effects']['text']=='HOOK'


def test_fade_duration_matches_renderer_and_rejects_excessive_overlap():
    result=apply_actions(state(),[{'type':'split','at':20},{'type':'transition','value':'fade','duration':.5}],120)
    assert document_duration(result)==59.5
    with pytest.raises(ValueError):
        apply_actions(result,[{'type':'speed','start':0,'end':.1,'value':4}],120)


@pytest.mark.parametrize('action',[
 {'type':'shell','command':'rm -rf /'},
 {'type':'crop','x':.5,'y':.5,'ffmpeg':'unsafe'},
 {'type':'trim','start':float('nan'),'end':20},
 {'type':'trim','start':True,'end':20},
 {'type':'cut','start':0,'end':60},
 {'type':'replace_segments','segments':[{'start':2,'end':130}]},
 {'type':'zoom','scale':9},
 {'type':'speed','value':0},
 {'type':'text_overlay','text':'x'*201},
])
def test_invalid_actions_never_mutate_original_or_execute_tools(action):
    original=state(); before=deepcopy(original)
    with pytest.raises(ValueError):
        apply_actions(original,[action],120)
    assert original==before


def test_schema_exposes_only_bounded_explicit_action_union():
    schema=action_schema()
    assert schema['additionalProperties'] is False
    assert schema['properties']['actions']['maxItems']==32
    assert 'command' not in str(schema)
    with pytest.raises(ValueError):
        apply_actions(state(),[{'type':'tiktok'}]*33,120)


def test_legacy_migration_preserves_style_words_and_original_export_metadata():
    clip={'id':'legacy','start':5,'end':35,'status':'ready','revision':7,
          'settings':{'subtitle_size':80,'remove_silence':True},
          'words':[{'start':6.2,'end':7,'text':'исправлено'}]}
    result=default_document(clip,120)
    assert result['settings']['subtitle_size']==80
    assert result['caption_overrides']=={'6.2':'исправлено'}
    assert result['settings']['remove_silence'] is False
    assert clip['revision']==7 and clip['status']=='ready'


def test_document_validation_rejects_unknown_fields_duplicate_ids_and_caption_times():
    original=state()
    for altered in [dict(original,shell='bad'),dict(original,caption_overrides={'NaN':'bad'}),
                    dict(original,segments=original['segments']*2)]:
        with pytest.raises(ValueError): validate_document(altered,120)


def test_canonical_caption_key_wins_over_legacy_float_alias():
    from backend.app.editing import default_document,validate_document
    from backend.app.editor_media import mapped_words
    state=default_document({'start':0,'end':3,'settings':{},'words':[{'start':1.0,'end':1.5,'text':'старый текст'}]},3)
    assert state['caption_overrides']=={'1':'старый текст'}
    state['caption_overrides']={'1':'новый текст','1.0':'старый текст'}
    state=validate_document(state,3)
    assert state['caption_overrides']=={'1':'новый текст'}
    assert mapped_words(state,[{'start':1.0,'end':1.5,'text':'исходник'}])[0]['text']=='новый текст'
