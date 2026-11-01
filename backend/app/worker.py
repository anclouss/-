"""Durable single-worker pipeline; no work is tied to an HTTP connection."""
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import shutil
import signal
import time
from . import db
from .config import DATA, MAX_BYTES, MAX_DURATION, CLIP_DEFAULTS, prepare
from .media import probe, extract_audio, render_clip
from .ai import AIError, transcribe, analyze
from .signals import extract_signals

log = logging.getLogger('clipforge.worker')


def atomic_json(path, value):
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
    os.replace(temporary,path)


def cleanup_sources():
    cutoff = datetime.now(timezone.utc)-timedelta(days=db.settings()['retention_days'])
    deleted = 0
    for p in db.all_projects():
        with db.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT payload FROM projects WHERE id=?',(p['id'],)).fetchone()
            if not row:
                continue
            current = json.loads(row[0])
            if current['status'] not in ('completed','failed') or datetime.fromisoformat(current['updated_at']) >= cutoff:
                continue
            if c.execute("SELECT 1 FROM jobs WHERE project_id=? AND status IN ('queued','running')",(p['id'],)).fetchone():
                continue
            if current.get('source_path') and Path(current['source_path']).is_file():
                Path(current['source_path']).unlink(missing_ok=True)
                current['source_path'] = None
                c.execute('UPDATE projects SET payload=? WHERE id=?',(json.dumps(current,ensure_ascii=False),p['id']))
                deleted += 1
            (DATA/'audio'/f"{p['id']}.wav").unlink(missing_ok=True)
    return deleted


def stage(ident, name, progress):
    db.update('projects',ident,status='processing',stage=name,progress=round(progress,1),error=None)


def import_recording(project):
    """Public recordings only. Never accept raw arbitrary download URLs."""
    import yt_dlp
    ident = project['id']
    prefix = DATA/'sources'/ident
    def match(info, *, incomplete=False):
        if info.get('is_live') or info.get('live_status') in ('is_live','is_upcoming'):
            return 'Поддерживаются завершённые записи, не прямые трансляции'
        duration = info.get('duration')
        if duration and duration > MAX_DURATION:
            return 'Запись длиннее 12 часов'
        if info.get('filesize',0) and info['filesize'] > MAX_BYTES:
            return 'Файл превышает ограничение размера'
    def progress(data):
        if data.get('downloaded_bytes',0) > MAX_BYTES or sum(f.stat().st_size for f in prefix.parent.glob(ident+'*') if f.is_file()) > MAX_BYTES:
            raise RuntimeError('Превышено ограничение размера видео')
        if shutil.disk_usage(DATA).free < 200*1024**2:
            raise RuntimeError('Недостаточно места на диске')
        total = data.get('total_bytes') or data.get('total_bytes_estimate')
        if total:
            stage(ident,'uploading',min(9,data.get('downloaded_bytes',0)/total*9))
    options = {
        'outtmpl':str(prefix)+'.%(ext)s','format':'bestvideo[height<=1080]+bestaudio/best[height<=1080]/best',
        'merge_output_format':'mp4','noplaylist':True,'max_downloads':1,'max_filesize':MAX_BYTES,
        'match_filter':match,'progress_hooks':[progress], 'socket_timeout':30,'retries':2,
        'quiet':True,'no_warnings':True,'restrictfilenames':True,'overwrites':True,
        'js_runtimes':{'node':{}},
        # Do not emit signed media URLs, cookies or provider diagnostics to logs.
        'logger':type('QuietLog',(),{'debug':lambda *a:None,'warning':lambda *a:None,'error':lambda *a:None})(),
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(project['source_url'],download=True)
        if not info:
            raise RuntimeError('Запись не скачана')
        files = [f for f in prefix.parent.glob(ident+'.*') if f.suffix not in ('.part','.ytdl','.json') and f.is_file()]
        if not files:
            raise RuntimeError('Файл записи не получен')
        path = max(files,key=lambda f:f.stat().st_size)
        if path.stat().st_size > MAX_BYTES:
            raise RuntimeError('Превышено ограничение размера видео')
        metadata = probe(path)
        if not metadata['has_audio'] or not 1 <= metadata['duration'] <= MAX_DURATION:
            raise RuntimeError('Запись без аудио или недопустимой длительности')
        db.update('projects',ident,source_path=str(path),duration=metadata['duration'],source_name=str(info.get('title') or project['source_name'])[:200],title=project['title'] if project['title']!=project['source_name'] else str(info.get('title') or project['title'])[:120])
        return path
    except Exception as exc:
        for file in prefix.parent.glob(ident+'.*'):
            if file.is_file():
                file.unlink(missing_ok=True)
        raise RuntimeError('Не удалось импортировать запись. Проверьте, что это публичное завершённое видео YouTube/Twitch, размер допустим, сеть доступна и yt-dlp обновлён. Можно загрузить файл вручную.') from exc


def render_one(clip):
    p = db.get('projects',clip['project_id'])
    if not p or not p.get('source_path') or not Path(p['source_path']).is_file():
        raise RuntimeError('Исходник удалён. Для повторного экспорта загрузите запись заново.')
    db.update('clips',clip['id'],status='rendering',error=None)
    output = DATA/'clips'/f"{clip['id']}.mp4"
    render_clip(Path(p['source_path']),output,clip['start'],clip['end'],clip['words'],clip['settings'])
    db.update('clips',clip['id'],status='ready',error=None,revision=clip.get('revision',0)+1)


def process_project(ident):
    p = db.get('projects',ident)
    config = db.settings()
    db.update('projects',ident,analysis_provider=config['analysis_provider'])
    stage(ident,'uploading',1)
    source = Path(p['source_path']) if p.get('source_path') else None
    if not source or not source.is_file():
        if not p.get('source_url'):
            raise RuntimeError('Исходный файл отсутствует. Загрузите видео заново.')
        source = import_recording(p)
    info = probe(source)
    if not info['has_audio'] or not 1 <= info['duration'] <= MAX_DURATION:
        raise RuntimeError('Видео должно содержать аудио и длиться не более 12 часов')
    db.update('projects',ident,duration=info['duration'])
    audio = DATA/'audio'/f'{ident}.wav'
    transcript = DATA/'transcripts'/f'{ident}.json'
    meta_path = DATA/'transcripts'/f'{ident}.meta.json'
    signature = {k:config[k] for k in ('transcription_provider','whisper_model','language')}
    signature.update(size=source.stat().st_size,mtime=source.stat().st_mtime_ns)
    try:
        stage(ident,'extracting_audio',10)
        extract_audio(source,audio)
        signals = extract_signals(audio)
        stage(ident,'transcribing',18)
        if transcript.is_file() and meta_path.is_file() and json.loads(meta_path.read_text(encoding='utf-8'))==signature:
            words = json.loads(transcript.read_text(encoding='utf-8'))
        else:
            words = transcribe(audio,config,lambda value:stage(ident,'transcribing',18+value*35))
            atomic_json(transcript,words)
            atomic_json(meta_path,signature)
        stage(ident,'analyzing',55)
        candidates = analyze(words,info['duration'],config,signals)
        if not candidates:
            raise RuntimeError('Не найдены завершённые моменты с речью. Попробуйте другую запись или модель анализа.')
        stage(ident,'finding_clips',65)
        # Replace earlier partial automatic results only after selection succeeded.
        for old in db.clips(ident):
            db.delete('clips',old['id'])
            (DATA/'clips'/f"{old['id']}.mp4").unlink(missing_ok=True)
        selected = []
        for item in candidates[:config['max_clips']]:
            clip = {**item,'id':db.new_id(),'project_id':ident,'status':'queued','error':None,
                    'revision':0,'settings':dict(CLIP_DEFAULTS),'created_at':db.now(),
                    'words':[w for w in words if w['end'] > item['start'] and w['start'] < item['end']]}
            db.put('clips',clip)
            selected.append(clip)
        for index,clip in enumerate(selected):
            stage(ident,'rendering',68+index/len(selected)*30)
            try:
                render_one(clip)
            except Exception:
                db.update('clips',clip['id'],status='failed',error='Ошибка экспорта FFmpeg. Проверьте место на диске и параметры кадрирования; затем повторите экспорт.')
                raise
        db.update('projects',ident,status='completed',stage='done',progress=100,error=None)
    finally:
        audio.unlink(missing_ok=True)


def safe_error(exc):
    # Never return raw HTTP/yt-dlp exception strings or signed URLs.
    if isinstance(exc,AIError):
        return str(exc)[:1200]
    if isinstance(exc,(ValueError,RuntimeError)) and not any(x in str(exc).lower() for x in ('http://','https://','bearer','token','api_key')):
        return str(exc)[:1200]
    return 'Ошибка обработки. Проверьте FFmpeg, доступность моделей, свободное место и повторите задачу.'


def run_job(job):
    try:
        if job['kind']=='analyze':
            process_project(job['project_id'])
        elif job['kind']=='render':
            clip = db.get('clips',job['clip_id'])
            if clip:
                render_one(clip)
        else:
            raise RuntimeError('Неизвестный тип задания')
        db.finish(job['id'])
        log.info('Job %s completed',job['id'])
    except Exception as exc:
        error = safe_error(exc)
        if job['kind']=='analyze':
            db.update('projects',job['project_id'],status='failed',error=error)
            # No remaining clip should look like it is still being processed.
            for clip in db.clips(job['project_id']):
                if clip['status'] in ('queued','rendering'):
                    db.update('clips',clip['id'],status='failed',error=error)
        elif job.get('clip_id'):
            db.update('clips',job['clip_id'],status='failed',error=error)
        db.finish(job['id'],error)
        log.error('Job %s failed: %s',job['id'],error)


class WorkerLock:
    def __enter__(self):
        prepare()
        self.file = (DATA/'worker.lock').open('a+b')
        self.file.seek(0)
        if self.file.read(1)==b'':
            self.file.write(b'0'); self.file.flush()
        self.file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise SystemExit('Другой worker уже запущен для DATA_DIR')
        return self
    def __exit__(self,*args):
        self.file.close()


def main():
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    db.init()
    with WorkerLock():
        db.recover()
        cleanup_at = 0
        log.info('Worker ready')
        try:
            while True:
                if time.monotonic() >= cleanup_at:
                    cleanup_sources()
                    cleanup_at = time.monotonic()+3600
                job = db.claim()
                if job:
                    run_job(job)
                else:
                    time.sleep(1)
        except KeyboardInterrupt:
            log.info('Worker stopped; interrupted job will resume on restart')

if __name__=='__main__':
    main()
