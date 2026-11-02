"""Actual EDL/FFmpeg checks; no model or API provider is required."""
from pathlib import Path
import math
import shutil
import struct
import subprocess
import time
import wave

import pytest

from backend.app.editor_media import MediaCancelled, create_proxy, mapped_words, render_document, waveform
from backend.app.media import MediaError, _write_ass, probe


@pytest.fixture(scope='module')
def source(tmp_path_factory):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg is required')
    root = tmp_path_factory.mktemp('editor-media')
    video = root / 'source.mp4'
    subprocess.run(['ffmpeg','-v','error','-nostdin','-y','-f','lavfi','-i','testsrc2=size=960x540:rate=15:duration=5',
                    '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=5',
                    '-c:v','libx264','-threads','2','-pix_fmt','yuv420p','-c:a','aac',str(video)],check=True)
    return video


def document(segments=None, **settings):
    return {'version':1,'segments':segments or [{'id':'a','start':0,'end':2,'speed':1,'effects':{}}],
            'settings':{'layout':'crop','aspect_ratio':'9:16','subtitles':True,'subtitle_highlight':True,
                        'normalize_audio':True,'volume':1,**settings},
            'transition':{'type':'none','duration':.2},'caption_overrides':{}}


def run_render(*args,**kwargs):
    try:
        return render_document(*args,**kwargs)
    except MediaError as error:
        pytest.fail(f'{error}\n{getattr(error,"diagnostic","")}')


def test_words_follow_reordered_speed_segments_and_fade():
    doc=document([{'id':'fast','start':2,'end':4,'speed':2,'effects':{}},
                  {'id':'slow','start':0,'end':1,'speed':.5,'effects':{}}])
    doc['transition']={'type':'fade','duration':.2}
    doc['caption_overrides']={'0.2':'Edited'}
    result=mapped_words(doc,[{'start':.2,'end':.4,'text':'First'}, {'start':2.5,'end':3,'text':'Second'},
                             {'start':4.5,'end':4.9,'text':'Removed'}])
    assert [w['text'] for w in result]==['Second','Edited']
    assert result[0]['start']==pytest.approx(.25)
    assert result[0]['end']==pytest.approx(.5)
    assert result[1]['start']==pytest.approx(1.2)
    assert result[1]['end']==pytest.approx(1.6)
    assert [w['segment_id'] for w in result]==['fast','slow']


def test_real_preview_reorder_speed_zoom_overlay_and_captions(source,tmp_path):
    doc=document([{'id':'fast','start':2,'end':4,'speed':2,'effects':{'zoom':1.2,'volume':.6,'text':'Hook!'}},
                  {'id':'normal','start':0,'end':1,'speed':1,'effects':{}}])
    output=tmp_path/'preview.mp4'
    calls=[]
    before=source.stat().st_size,source.stat().st_mtime_ns
    result=run_render(source,output,doc,[{'start':2.3,'end':2.7,'text':'Привет!'}],
                      {'preview':True,'quality':'360p','fps':'30','subtitles':True,'watermark':'ClipForge'},
                      progress=lambda stage,value:calls.append((stage,value)))
    assert (result['width'],result['height'])==(360,640)
    assert result['duration']==pytest.approx(2,abs=.12)
    assert result['fps']==pytest.approx(30,abs=.05)
    assert result['has_audio']
    assert result['size_bytes']==output.stat().st_size
    assert calls[0]==('preparing',0)
    assert calls[-1]==('done',100)
    assert 'adding_subtitles' in {stage for stage,value in calls}
    assert before==(source.stat().st_size,source.stat().st_mtime_ns)


def test_actual_crossfade_and_split_layout(source,tmp_path):
    doc=document([{'id':'one','start':0,'end':1.2,'speed':1,'effects':{}},
                  {'id':'two','start':2,'end':3.2,'speed':1,'effects':{}}],layout='split',face_box=[.5,0,.5,.5])
    doc['transition']={'type':'fade','duration':.2}
    result=run_render(source,tmp_path/'fade.mp4',doc,[],{'preview':True,'quality':'360p','subtitles':False})
    assert result['duration']==pytest.approx(2.2,abs=.15)
    assert (result['width'],result['height'])==(360,640)


def test_real_720p_export_original_fps_and_fit(source,tmp_path):
    doc=document([{'id':'one','start':.5,'end':1.5,'speed':1,'effects':{}}],layout='fit',aspect_ratio='16:9')
    result=run_render(source,tmp_path/'final.mp4',doc,[],{'resolution':'720p','fps':'original','preset':'fast','codec':'h264','subtitles':False,'max_bitrate_mbps':1.5})
    assert (result['width'],result['height'])==(1280,720)
    assert result['fps']==pytest.approx(15,abs=.05)
    assert result['video_codec']=='h264'
    assert result['estimated_bytes']>0


def test_proxy_preserves_aspect_and_has_audio(source,tmp_path):
    result=create_proxy(source,tmp_path/'proxy.mp4','360p')
    assert (result['width'],result['height'])==(640,360)
    assert result['duration']==pytest.approx(probe(source)['duration'],abs=.2)
    assert result['has_audio']


def test_cancel_is_atomic_and_removes_temporary_work(source,tmp_path):
    output=tmp_path/'untouched.mp4'
    output.write_bytes(b'original export')
    started=time.monotonic()
    with pytest.raises(MediaCancelled):
        render_document(source,output,document(),[],{'resolution':'1080p','preset':'maximum'},cancel=lambda:time.monotonic()-started>.15)
    assert output.read_bytes()==b'original export'
    assert not list(tmp_path.glob('editor-render-*'))


def test_quality_and_invalid_source_ranges_fail_before_render(source,tmp_path):
    with pytest.raises(MediaError,match='4K'):
        render_document(source,tmp_path/'4k.mp4',document(),[],{'resolution':'4k'})
    invalid=document([{'id':'invalid','start':4,'end':6,'speed':1,'effects':{}}])
    with pytest.raises(MediaError,match='пределы'):
        render_document(source,tmp_path/'invalid.mp4',invalid,[],{'preview':True})
    doc=document()
    doc['transition']={'type':'shell','duration':.2}
    with pytest.raises(MediaError,match='переход'):
        mapped_words(doc,[])


def test_waveform_is_real_bounded_streaming_pcm(tmp_path):
    path=tmp_path/'wave.wav'
    with wave.open(str(path),'wb') as audio:
        audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000)
        tone=b''.join(struct.pack('<h',round(10000*math.sin(i*2*math.pi*440/16000))) for i in range(16000))
        audio.writeframes(tone+b'\0\0'*16000)
    peaks=waveform(path,32)
    assert len(peaks)==32
    assert min(peaks[:16])>.25
    assert max(peaks[16:])==0


def test_caption_phrases_do_not_overlap_and_highlight_can_be_disabled(tmp_path):
    output=tmp_path/'captions.ass'
    _write_ass(output,[{'start':0,'end':.5,'text':'Первое.'},{'start':.51,'end':1,'text':'Второе'}],{'subtitle_highlight':False},1.2,width=720,height=1280)
    lines=[line for line in output.read_text(encoding='utf-8-sig').splitlines() if line.startswith('Dialogue:')]
    assert len(lines)==2
    assert lines[0].split(',')[2]=='0:00:00.51'
    assert '\\c&H' not in '\n'.join(lines)
    assert 'PlayResX: 720' in output.read_text(encoding='utf-8-sig')


def test_volume_changes_survive_normalization(source,tmp_path):
    import numpy as np
    levels=[]
    for gain in (.5,2.):
        doc=document([{'id':'one','start':0,'end':1,'speed':1,'effects':{'volume':gain}}],normalize_audio=True)
        output=tmp_path/f'gain-{gain}.mp4'
        run_render(source,output,doc,[],{'preview':True,'quality':'360p','subtitles':False})
        raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(output),'-vn','-ac','1','-ar','16000','-f','f32le','-'])
        values=np.frombuffer(raw,dtype='<f4')
        levels.append(float(np.sqrt(np.mean(values**2))))
    assert levels[1]>levels[0]*2.5


def test_silent_video_can_be_edited_without_transcription(source,tmp_path):
    silent=tmp_path/'silent.mp4'
    subprocess.run(['ffmpeg','-v','error','-nostdin','-y','-i',str(source),'-t','1','-an','-c:v','copy',str(silent)],check=True)
    assert not probe(silent)['has_audio']
    result=run_render(silent,tmp_path/'with-silence.mp4',document([{'id':'a','start':0,'end':.7,'speed':1,'effects':{}}]),[],{'preview':True,'quality':'360p','subtitles':False})
    assert result['has_audio']
    assert result['duration']==pytest.approx(.7,abs=.12)


def test_real_hevc_60fps_watermark_and_custom_aspect(source,tmp_path):
    encoders=subprocess.check_output(['ffmpeg','-hide_banner','-encoders'],stderr=subprocess.DEVNULL,text=True)
    if 'libx265' not in encoders:
        pytest.skip('FFmpeg libx265 unavailable')
    doc=document([{'id':'hevc','start':0,'end':.6,'speed':1,'effects':{}}],aspect_ratio='4:5')
    output=tmp_path/'hevc.mp4'
    result=run_render(source,output,doc,[],{'resolution':'720p','fps':'60','preset':'fast','codec':'h265',
                                          'subtitles':False,'watermark':'Export test','crf':None})
    assert (result['width'],result['height'])==(720,900)
    assert result['video_codec']=='hevc'
    assert result['fps']==pytest.approx(60,abs=.05)
    assert result['duration']==pytest.approx(.6,abs=.12)


def test_three_segment_crossfade_with_different_gains(source,tmp_path):
    doc=document([{'id':'a','start':0,'end':.8,'speed':1,'effects':{'volume':.7}},
                  {'id':'b','start':1,'end':1.8,'speed':1,'effects':{'volume':1.3}},
                  {'id':'c','start':2,'end':2.8,'speed':1,'effects':{'volume':1}}])
    doc['transition']={'type':'fade','duration':.2}
    result=run_render(source,tmp_path/'three-fades.mp4',doc,[],{'preview':True,'quality':'360p','subtitles':False})
    assert result['duration']==pytest.approx(2.,abs=.15)


def test_explicit_remove_zoom_overrides_legacy_auto_zoom_on_target_segment(source,tmp_path):
    checksums=[]
    for auto in (False,True):
        doc=document([{'id':'one','start':0,'end':.5,'speed':1,'effects':{'zoom':1}}],auto_zoom=auto)
        output=tmp_path/f'removed-zoom-{auto}.mp4'
        run_render(source,output,doc,[],{'preview':True,'quality':'360p','subtitles':False})
        checksums.append(subprocess.check_output(['ffmpeg','-v','error','-i',str(output),'-map','0:v:0','-f','hash','-hash','sha256','-']))
    assert checksums[0]==checksums[1]
