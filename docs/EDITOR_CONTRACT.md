# Editor implementation contract (schema v1)

Existing checkout, API and old clip exports stay compatible. New editor is the only frontend editor.

## Non-destructive document

```json
{"version":1,"segments":[{"id":"s1","start":12,"end":60,"speed":1,"effects":{}}],"settings":{"layout":"crop","crop_x":0.5,"crop_y":0.5,"face_box":[0.65,0,0.35,0.4],"face_tracking":false,"subtitle_font":"Arial","subtitle_size":58,"subtitle_position":0.76,"subtitle_color":"#B8FF60","subtitles":true,"subtitle_highlight":true,"auto_zoom":false,"remove_silence":false,"volume":1,"normalize_audio":true,"aspect_ratio":"9:16"},"transition":{"type":"none","duration":0.2},"caption_overrides":{}}
```

Segment start/end are absolute SOURCE seconds. Ordered segments determine output. Speed0.25..4. Segment effects optional restricted fields zoom1..3,crop_x/y0..1,volume0..3,text(max200),text_size24..120,text_color#RRGGBB. Targeted effects split segments at selection boundaries, so scope stays attached when segments move. Global settings inherit old defaults. caption_overrides maps source word start formatted as string to edited text. Duration=sum((end-start)/speed), minus transition overlap if fade. Source must stay untouched. Max256 segments and nonempty output; bound total24h and actions32/request.

Actions use OUTPUT TIMELINE seconds unless explicit source_* or segments ranges:
trim(start,end), cut/remove(start,end), split(at), move(index,to), speed(start,end,value), zoom(start,end,scale), crop(x,y), resize(aspect_ratio), reframe(layout,crop_x?,crop_y?), layout(value), face_tracking(enabled), subtitles(enabled,highlight?), subtitle_style(size?,font?,position?,color?,preset?), text_overlay(start,end,text,size?,color?), normalize_audio(enabled), volume(value,start?,end?), transition(value:none|fade,duration?), remove_silences(min_duration?), create_hook(start,end) [move timeline range to start], replace_segments(segments:[{start,end}]) [SOURCE ranges], find_moments(query) [resolve via retrieval], auto_highlights [resolve via transcript/highlight context], tiktok [safe bundled preset]. Backend validates all and rejects unknown fields/tools. No shell strings allowed.

## HTTP

- GET /api/editor/{clip_id}: {clip,project,revision,state,history:{can_undo,can_redo,cursor,entries:[{label,created_at}]},messages:[],proposals:[],source:{duration,width,height,fps,available},proxy:{status,quality,url,error},waveform:{peaks:[],duration},exports:[]}. clip/project use existing public shapes.
- PATCH /api/editor/{id}: {revision,state,label?} -> full session. CAS409 on stale. No render.
- POST /api/editor/{id}/actions: {revision,actions,label?,context?:{playhead,selection?}} -> full session. Quick/manual actions; autosaved, no render.
- POST /api/editor/{id}/history: {revision,action:undo|redo|reset} -> full session.
- GET /api/editor/{id}/transcript?q=&offset=0&limit=100 -> {words:[{start,end,text}],total,offset}; limit<=200.
- POST /api/editor/{id}/chat: {revision,prompt,context:{playhead,selection?:{start,end}}} -> full session including proposal; no apply/render. Frontend AbortController can abandon request; server revision check rejects stale completion. Proposal {id,base_revision,summary,actions,state,status:pending|applied|cancelled,created_at}.
- POST /api/editor/{id}/proposals/{proposal_id}: {revision,action:apply|cancel} -> full session. CAS on apply; cancel retained as chat status. Never execute LLM shell code.
- POST /api/editor/{id}/proxy: {quality:auto|360p|480p|720p|1080p} -> job. GET/media/proxy/{project_id}/{quality} video with Range. Source video available immediately during proxy build.
- POST /api/editor/{id}/preview: {revision,proposal_id?:str,quality:auto|360p|480p|720p|1080p} -> job. Explicit baked preview cached by state+source+quality; quick browser playback uses source/proxy+EDL without encode.
- POST /api/editor/{id}/exports: {revision,platform:tiktok|shorts|reels|custom,resolution:720p|1080p|1440p|4k,fps:original|30|60,preset:fast|balanced|high|maximum,codec:h264|h265,subtitles:bool,watermark:str,max_bitrate_mbps?:float,crf?:int} -> export job. Snapshot fixed at request.4k only when source has4k dimensions. Existing exports remain downloadable while new job runs.
- GET /api/editor/{id}/events SSE revision/job changes (optional client uses non-overlap polling fallback).
- GET /api/editor-jobs/{job_id}; POST /api/editor-jobs/{job_id}/cancel; POST /api/editor-jobs/{job_id}/retry. Job {id,kind,status:queued|running|done|failed|cancelled,stage,progress,error,url?,download_url?,estimated_bytes?,revision?}. /media/editor/{jobid} and /api/editor-jobs/{jobid}/download.
- POST /api/projects/{project_id}/editor creates manual clip over source [0,duration] even without transcription; user can edit raw upload. Existing auto-clip workflow retained.

## Module contracts

editing.py: default_document(clip,source_duration), validate_document(document,source_duration)->dict, apply_actions(document,actions,source_duration,context=None)->dict, document_duration(document)->float, action_schema()->dict. context holds words/pauses/highlights and UIplayhead/selection.
editor_store.py: init(), ensure(clip,project)->session, get(clip_id)->session|None, save(clip_id,expected_revision,state,label)->session, history(clip_id,expected_revision,action)->session. ConflictError,ValidationError orValueError; parent maps409/422. Session has revision,state,history; stored source_duration. Deletes cascade withclip. Preserveinitialstate; max100history.
editor_ai.py: init(), messages(clip_id), proposals(clip_id), propose(clip_id,revision,prompt,context,words,signals,config)->proposal; decision(clip_id,proposalid,revision,action)->session. Parent ensures editor_store.ensure first. Uses editing.apply_actions + bounded retrieval + schema, existing ai._llm; stores messages/proposals SQLite; caches prompts with state/context/model keys. No direct shell. Proposed state validated, apply checks currentrevision.
editor_media.py: render_document(source,output,document,words,options,progress=None,cancel=None)->dict metadata; create_proxy(source,output,quality,progress=None,cancel=None)->dict; waveform(audio_path,max_points=2048)->list[float]; mapped_words(document,words)->list[dict]. cancellation callback returnsbool; progress callback(stage:str,percent:float). render options keys from exportbody, preview bool. Cache/job/DB owned byparent. MediaError safe, MediaCancelled exception. Captions/times remap acrosssegment speed/order/cuts/transitions. outputs atomic. Old media.render_clip remains compatible.

Migration only adds tables/columns and creates documents lazily for existingclips. No source/dependency reset. Keep PyAV18 pin. Each phase is validated before following integration.
