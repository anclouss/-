import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { Link, NavLink, Route, Routes, useNavigate, useParams } from 'react-router-dom';
import {
  ArrowDownToLine, ArrowLeft, ArrowRight, AudioLines, Check, CheckCircle2,
  ChevronRight, CircleAlert, Clock3, Clapperboard, Download, ExternalLink,
  Film, FolderOpen, Layers3, LayoutDashboard, Link2, LoaderCircle, Menu,
  Plus, RefreshCw, Scissors, Settings2, ShieldCheck, SlidersHorizontal,
  Sparkles, Trash2, UploadCloud, WandSparkles, X,
} from 'lucide-react';
import { api, defaultClipSettings, stageLabels, time, uploadFile, previewUrl } from './api';
import type { Clip, ClipSettings, Project, Settings, Word } from './api';

function useData<T>(path: string, poll = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const sequence = useRef(0);
  const refresh = useCallback(async () => {
    const current = ++sequence.current;
    try {
      const next = await api<T>(path);
      if (current !== sequence.current) return;
      setData(next); setError('');
    } catch (e) {
      if (current === sequence.current) setError(message(e));
    } finally {
      if (current === sequence.current) setLoading(false);
    }
  }, [path]);
  useEffect(() => {
    setData(null); setLoading(true); void refresh();
    const timer = poll ? window.setInterval(refresh, poll) : undefined;
    return () => { window.clearInterval(timer); sequence.current++; };
  }, [refresh, poll]);
  return { data, setData, error, loading, refresh };
}

const message = (error: unknown) => error instanceof Error ? error.message : 'Не удалось выполнить действие.';
const date = (value: string) => new Date(value).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });

function ErrorBanner({ error, retry }: { error: string; retry?: () => void }) {
  if (!error) return null;
  return <div className="error-banner" role="alert"><CircleAlert size={19} /><span>{error}</span>{retry && <button className="button ghost" onClick={retry}>Повторить</button>}</div>;
}
function Loading() {
  return <div className="loading-state" role="status"><LoaderCircle className="loading-spinner" size={28} /><span>Загружаем данные…</span></div>;
}
function Status({ project }: { project: Project }) {
  const label = project.status === 'completed' ? 'Готово' : project.status === 'failed' ? 'Ошибка' : project.status === 'queued' ? 'В очереди' : 'В обработке';
  return <span className={`badge ${project.status === 'completed' ? 'success' : project.status === 'failed' ? 'error' : 'warning'}`}><span className="status-dot" />{label}</span>;
}
function Header({ eyebrow, title, subtitle, children }: { eyebrow?: string; title: string; subtitle: string; children?: ReactNode }) {
  return <header className="page-header"><div>{eyebrow && <div className="page-eyebrow">{eyebrow}</div>}<h1 className="page-title">{title}</h1><p className="page-subtitle">{subtitle}</p></div>{children && <div className="page-actions">{children}</div>}</header>;
}
function Stat({ label, value, icon }: { label: string; value: ReactNode; icon: ReactNode }) {
  return <div className="stat-card"><div><div className="stat-label">{label}</div><div className="stat-value">{value}</div></div><span className="stat-icon">{icon}</span></div>;
}

function Dashboard() {
  const { data: projects, error, loading, refresh } = useData<Project[]>('/projects', 6000);
  const [actionError, setActionError] = useState('');
  const [deleting, setDeleting] = useState('');
  const remove = async (project: Project) => {
    if (!window.confirm(`Удалить проект «${project.title}», исходник и все клипы? Это действие нельзя отменить.`)) return;
    setDeleting(project.id); setActionError('');
    try { await api(`/projects/${project.id}`, { method: 'DELETE' }); await refresh(); }
    catch (e) { setActionError(message(e)); } finally { setDeleting(''); }
  };
  const list = projects || [];
  return <>
    <Header eyebrow="ВАШЕ ТВОРЧЕСКОЕ ПРОСТРАНСТВО" title="Меньше монтажа. Больше контента." subtitle="Превращайте длинные стримы в моменты, которые хочется пересмотреть.">
      <Link className="button primary" to="/upload"><Plus size={18} />Новый проект</Link>
    </Header>
    <section className="dashboard-hero">
      <div className="hero-copy"><div className="hero-label"><Sparkles size={15} /> ДЛИННОЕ ВИДЕО → КОРОТКАЯ ИСТОРИЯ</div><h2>Хороший момент<br />уже есть в вашем стриме.</h2><p>AI найдёт контекст и кульминацию. Вы получите вертикальные клипы с субтитрами, готовые к публикации.</p><Link className="button primary" to="/upload">Загрузить первое видео <ArrowRight size={17} /></Link><div className="privacy-note"><ShieldCheck size={14} /> Файлы хранятся на вашем сервере</div></div>
      <div className="hero-art" aria-hidden="true"><div className="hero-video-wide"><div className="wave-bars">{Array.from({ length: 29 }, (_, i) => <i key={i} style={{ height: `${18 + ((i * 37) % 64)}%` }} />)}</div><span><Film size={15} /> ВАШ СТРИМ</span></div><div className="hero-video-phone"><div className="hero-phone-icon"><Scissors size={34} strokeWidth={1.4} /></div><div className="hero-phone-subtitle">Вот он,<br /><em>тот самый</em> момент.</div><div className="hero-phone-bottom">9:16 <span>1080p</span></div></div><span className="hero-art-badge"><WandSparkles size={15} /> Меньше рутины</span></div>
    </section>
    <div className="stats-grid"><Stat label="Всего проектов" value={list.length} icon={<FolderOpen size={21} />} /><Stat label="Найдено клипов" value={list.reduce((n, p) => n + (p.clip_count || 0), 0)} icon={<Clapperboard size={21} />} /><Stat label="Сейчас в обработке" value={list.filter(p => ['processing', 'queued'].includes(p.status)).length} icon={<AudioLines size={21} />} /></div>
    <div className="section-header"><div><h2>Мои проекты <span className="count-pill">{list.length}</span></h2><p>Все записи и результаты в одном месте</p></div><button className="button ghost" onClick={() => void refresh()} aria-label="Обновить проекты"><RefreshCw size={16} /> Обновить</button></div>
    <ErrorBanner error={actionError || error} retry={() => void refresh()} />
    {loading ? <Loading /> : list.length ? <div className="project-grid">{list.map(p => <article className="project-card" key={p.id}>
      <div className="project-card-top"><div className="project-icon"><Film size={26} strokeWidth={1.5} /></div><Status project={p} /></div>
      <Link className="project-title" to={`/projects/${p.id}/${p.status === 'completed' ? 'results' : 'processing'}`}>{p.title || p.source_name}</Link>
      <div className="project-meta"><span>{date(p.created_at)}</span><span>•</span><span>{p.duration ? time(p.duration) : 'Длительность уточняется'}</span></div>
      {['processing', 'queued'].includes(p.status) ? <div className="project-progress"><div className="status-line"><span>{stageLabels[p.stage] || p.stage}</span><span>{Math.round(p.progress || 0)}%</span></div><div className="progress-track"><div className="progress-fill" style={{ width: `${p.progress || 0}%` }} /></div></div> : <div className="project-clip-count"><Scissors size={15} /> {p.clip_count || 0} клипов {p.status === 'failed' && <span className="error-text">Нужна проверка</span>}</div>}
      <div className="project-actions"><Link className="button secondary" to={`/projects/${p.id}/${p.status === 'completed' ? 'results' : 'processing'}`}>{p.status === 'completed' ? 'Открыть клипы' : 'Открыть проект'}<ChevronRight size={15} /></Link><button className="icon-button" disabled={deleting === p.id} onClick={() => void remove(p)} aria-label={`Удалить проект ${p.title}`}><Trash2 size={16} /></button></div>
    </article>)}</div> : !error && <div className="empty-state"><div className="empty-icon"><FolderOpen size={29} /></div><h3>Здесь начнётся ваша библиотека</h3><p>Загрузите запись стрима — мы найдём лучшие моменты<br />и соберём из них готовые клипы.</p><Link className="button secondary" to="/upload"><Plus size={16} />Создать проект</Link></div>}
    <p className="footer-note">Сначала история, потом монтаж. ClipForge ищет моменты по смыслу, а не только по громкости.</p>
  </>;
}

function Upload() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<'file' | 'url'>('file');
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState('');
  const [title, setTitle] = useState('');
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const controller = useRef<AbortController | null>(null);
  const { data: settings } = useData<Settings>('/settings');
  const maxGb = settings?.max_upload_gb ?? 4;
  const choose = (next?: File) => {
    if (!next) return;
    setError('');
    if (!/\.(mp4|mkv|mov|webm|avi|m4v|ts|flv)$/i.test(next.name)) { setError('Выберите видео: MP4, MKV, MOV, WebM, AVI или TS.'); return; }
    if (next.size > maxGb * 1024 ** 3) { setError(`Файл превышает лимит ${maxGb} ГБ.`); return; }
    setFile(next);
    if (!title) setTitle(next.name.replace(/\.[^.]+$/, '').slice(0, 120));
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setError(''); setBusy(true); setProgress(0);
    try {
      let project: Project;
      if (tab === 'file') {
        if (!file) throw new Error('Выберите видео для загрузки.');
        controller.current = new AbortController();
        project = await uploadFile(file, title, setProgress, controller.current.signal);
      } else {
        const parsed = new URL(url);
        if (parsed.protocol !== 'https:') throw new Error('Используйте HTTPS-ссылку на YouTube или Twitch.');
        project = await api<Project>('/projects/url', { method: 'POST', body: JSON.stringify({ url, title }) });
      }
      navigate(`/projects/${project.id}/processing`);
    } catch (e) { setError(message(e)); } finally { setBusy(false); controller.current = null; }
  };
  return <>
    <Header eyebrow="НОВЫЙ ПРОЕКТ" title="Давайте найдём ваши лучшие моменты" subtitle="Загрузите запись или вставьте ссылку. Остальное возьмёт на себя ClipForge." />
    <div className="upload-grid"><form className="card upload-source-card" onSubmit={submit}>
      <div className="card-header"><h2>Исходное видео</h2><span className="badge neutral">01 / 01</span></div>
      <div className="card-body"><div className="tabs" role="tablist" aria-label="Источник видео"><button type="button" role="tab" aria-selected={tab === 'file'} className={`tab ${tab === 'file' ? 'tab-active' : ''}`} disabled={busy} onClick={() => setTab('file')}><UploadCloud size={17} /> Файл</button><button type="button" role="tab" aria-selected={tab === 'url'} className={`tab ${tab === 'url' ? 'tab-active' : ''}`} disabled={busy} onClick={() => setTab('url')}><Link2 size={17} /> Ссылка</button></div>
      {tab === 'file' ? <><input ref={input} type="file" accept="video/*,.mkv,.ts,.avi" hidden onChange={e => choose(e.target.files?.[0])} disabled={busy} /><div className={`upload-dropzone ${dragging ? 'dragging' : ''}`} role="button" tabIndex={busy ? -1 : 0} aria-label="Выбрать видеофайл" aria-disabled={busy} onClick={() => !busy && input.current?.click()} onKeyDown={e => { if (!busy && ['Enter', ' '].includes(e.key)) { e.preventDefault(); input.current?.click(); } }} onDragOver={e => { e.preventDefault(); if (!busy) setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={e => { e.preventDefault(); setDragging(false); if (!busy) choose(e.dataTransfer.files[0]); }}>
        <div className="upload-icon"><UploadCloud size={31} strokeWidth={1.4} /></div><h3>{file ? file.name : 'Перетащите видео сюда'}</h3><p>{file ? `${(file.size / 1024 ** 2).toFixed(1)} МБ · Нажмите, чтобы выбрать другой файл` : 'или нажмите, чтобы выбрать файл'}</p><span className="hint">MP4, MKV, MOV, WebM · до {maxGb} ГБ</span>
      </div></> : <div className="field"><label className="field-label" htmlFor="source-url">Ссылка на запись</label><div className="input-icon"><Link2 size={17} /><input id="source-url" className="input" type="url" placeholder="https://www.youtube.com/watch?v=…" value={url} disabled={busy} onChange={e => setUrl(e.target.value)} required /></div><p className="hint">Публичные записи YouTube и Twitch. Живые эфиры, DRM и видео с авторизацией не поддерживаются. Загрузка зависит от доступности платформы.</p></div>}
      <div className="field"><label className="field-label" htmlFor="project-title">Название проекта <span className="hint">необязательно</span></label><input id="project-title" className="input" maxLength={120} placeholder="Например, вечерний стрим — лучшие моменты" value={title} disabled={busy} onChange={e => setTitle(e.target.value)} /></div>
      <ErrorBanner error={error} />
      {busy && <div className="upload-progress" aria-live="polite"><div className="status-line"><span>{tab === 'file' ? progress < 100 ? 'Загружаем видео на сервер…' : 'Файл загружен. Создаём проект…' : 'Добавляем ссылку в очередь…'}</span>{tab === 'file' && <strong>{progress}%</strong>}</div><div className="progress-track"><div className="progress-fill" style={{ width: `${tab === 'file' ? progress : 30}%` }} /></div>{tab === 'file' && progress < 100 && <button type="button" className="button ghost" onClick={() => controller.current?.abort()}>Отменить загрузку</button>}<p className="hint">Не закрывайте страницу до завершения загрузки файла.</p></div>}
      <button type="submit" className="button primary full-width" disabled={busy || (tab === 'file' ? !file : !url)}>{busy ? <LoaderCircle size={18} className="loading-spinner" /> : <Sparkles size={18} />}{busy ? 'Подготавливаем проект…' : 'Найти лучшие моменты'}{!busy && <ArrowRight size={17} />}</button>
      </div></form>
      <aside><div className="card"><div className="card-body"><div className="section-icon"><WandSparkles size={23} /></div><h3>От записи до готового клипа</h3><div className="upload-feature"><span>01</span><div><h4>Понимаем контекст</h4><p>Распознаём речь и ищем историю, реакцию или неожиданную развязку.</p></div></div><div className="upload-feature"><span>02</span><div><h4>Собираем момент</h4><p>Учитываем предысторию, уточняем начало и логичное завершение.</p></div></div><div className="upload-feature"><span>03</span><div><h4>Готовим к публикации</h4><p>Вертикальный MP4, подсветка слов и нормализованный звук.</p></div></div></div></div><div className="info-banner"><Clock3 size={19} /><p>После загрузки можно закрыть страницу. Обработка продолжится в фоне, пока работает сервер и воркер.</p></div><Link className="back-link" to="/settings"><Settings2 size={16} /> Проверить настройки AI <ChevronRight size={15} /></Link></aside>
    </div>
  </>;
}

const stages = ['extracting_audio', 'transcribing', 'analyzing', 'finding_clips', 'rendering', 'done'];
const stageDescriptions = ['Готовим звуковую дорожку для распознавания', 'Получаем слова и точные таймкоды', 'Ищем историю, эмоцию и кульминацию', 'Уточняем границы и ранжируем кандидатов', 'Кадрируем, добавляем субтитры и сохраняем MP4', 'Ваши клипы готовы к просмотру'];

function Processing() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { data: project, error, loading, refresh } = useData<Project>(`/projects/${id}`, 3000);
  const [retrying, setRetrying] = useState(false);
  const [actionError, setActionError] = useState('');
  useEffect(() => { if (project?.status === 'completed') navigate(`/projects/${id}/results`, { replace: true }); }, [project?.status, id, navigate]);
  const retry = async () => {
    setRetrying(true); setActionError('');
    try { await api(`/projects/${id}/retry`, { method: 'POST' }); await refresh(); } catch (e) { setActionError(message(e)); } finally { setRetrying(false); }
  };
  if (loading) return <Loading />;
  const active = project ? stages.indexOf(project.stage) : -1;
  return <>
    <Link className="back-link" to="/"><ArrowLeft size={16} /> Все проекты</Link>
    <Header eyebrow="ОБРАБОТКА ВИДЕО" title={project?.title || 'Подготавливаем проект'} subtitle="Длинная запись становится короткой историей." />
    <ErrorBanner error={error || actionError} retry={() => void refresh()} />
    {project && <div className="processing-grid"><div className="card processing-card"><div className="card-body"><div className={`process-visual ${project.status === 'failed' ? 'process-failed' : ''}`}><div className="process-orbit" /><div className="process-center">{project.status === 'failed' ? <CircleAlert size={38} /> : <AudioLines size={38} strokeWidth={1.3} />}</div></div><span className={`badge ${project.status === 'failed' ? 'error' : 'warning'}`}>{project.status === 'failed' ? 'Нужна ваша помощь' : project.status === 'queued' ? 'В очереди' : 'AI за работой'}</span><h2>{project.status === 'failed' ? 'Обработка остановлена' : stageLabels[project.stage] || 'Готовимся к обработке'}</h2><p>{project.status === 'failed' ? 'Проверьте причину ниже. После исправления можно повторить обработку.' : project.status === 'queued' ? 'Воркер начнёт обработку, когда освободится. Убедитесь, что он запущен.' : 'Время зависит от длины записи, модели и мощности сервера.'}</p><div className="processing-percent">{Math.round(project.progress || 0)}<span>%</span></div><div className="progress-track"><div className="progress-fill" style={{ width: `${project.progress || 0}%` }} /></div>{project.status === 'failed' && <><ErrorBanner error={project.error || 'Неизвестная ошибка. Посмотрите журнал воркера.'} /><button className="button primary" disabled={retrying} onClick={() => void retry()}><RefreshCw size={16} className={retrying ? 'loading-spinner' : ''} /> Повторить обработку</button><Link className="button ghost" to="/settings">Открыть настройки AI</Link></>}</div></div><div><div className="card"><div className="card-header"><h2>Этапы обработки</h2><span className="badge neutral">ФОНОВОЕ ЗАДАНИЕ</span></div><div className="card-body steps">{stages.map((stage, index) => {
      const done = active > index || project.stage === 'done';
      const current = active === index && project.status !== 'failed';
      return <div key={stage} className={`step ${done ? 'step-done' : current ? 'step-active' : ''}`}><div className="step-dot">{done ? <Check size={15} /> : current ? <LoaderCircle className="loading-spinner" size={16} /> : index + 1}</div><div className="step-copy"><h4>{stageLabels[stage]}</h4><p>{stageDescriptions[index]}</p></div></div>;
    })}</div></div><div className="info-banner"><ShieldCheck size={19} /><p>Можно закрыть вкладку и вернуться позже. Проект и его прогресс сохраняются на сервере.</p></div>{Boolean(project.clips?.length) && <Link className="button secondary full-width" to={`/projects/${id}/results`}><Clapperboard size={17} /> Смотреть найденные моменты ({project.clips?.length})</Link>}</div></div>}
  </>;
}

function Results() {
  const { id } = useParams();
  const { data: project, error, loading, refresh } = useData<Project>(`/projects/${id}`, 4000);
  const [selected, setSelected] = useState<string[]>([]);
  const [actionError, setActionError] = useState('');
  const [deleting, setDeleting] = useState('');
  const clips = project?.clips || [];
  const ready = clips.filter(c => c.status === 'ready');
  const selectedReady = selected.filter(value => ready.some(c => c.id === value));
  const remove = async (clip: Clip) => {
    if (!window.confirm(`Удалить клип «${clip.title}»? Исходное видео останется в проекте.`)) return;
    setDeleting(clip.id); setActionError('');
    try { await api(`/clips/${clip.id}`, { method: 'DELETE' }); setSelected(s => s.filter(i => i !== clip.id)); await refresh(); } catch (e) { setActionError(message(e)); } finally { setDeleting(''); }
  };
  if (loading) return <Loading />;
  return <>
    <Link className="back-link" to="/"><ArrowLeft size={16} /> Все проекты</Link>
    <Header eyebrow="РЕЗУЛЬТАТЫ АНАЛИЗА" title={project?.title || 'Найденные моменты'} subtitle="Выберите лучшие клипы, внесите последние штрихи и скачайте MP4.">
      {Boolean(ready.length) && <a className="button primary" href={`/api/projects/${id}/download`} download><Download size={17} />Скачать все ({ready.length})</a>}
    </Header>
    <ErrorBanner error={error || actionError} retry={() => void refresh()} />
    {project?.status === 'failed' && <ErrorBanner error={project.error || 'Обработка проекта не завершена.'} />}
    {project && ['processing', 'queued'].includes(project.status) && <div className="info-banner"><LoaderCircle className="loading-spinner" size={19} /><p>Обработка продолжается: {stageLabels[project.stage] || project.stage}. Готовые клипы появятся здесь автоматически.</p><Link className="button ghost" to={`/projects/${id}/processing`}>Прогресс <ChevronRight size={15} /></Link></div>}
    {project && <div className="stats-grid"><Stat label="Найдено моментов" value={clips.length} icon={<Scissors size={21} />} /><Stat label="Готово к скачиванию" value={ready.length} icon={<CheckCircle2 size={21} />} /><Stat label="Лучший Viral Score" value={clips.length ? `${Math.max(...clips.map(c => c.score))}/100` : '—'} icon={<Sparkles size={21} />} /></div>}
    <div className="results-toolbar"><div><h2>Подборка клипов</h2><p className="hint">Viral Score — оценка потенциала и структуры истории, а не прогноз просмотров.</p></div>{Boolean(ready.length) && <label className="checkbox-label"><input type="checkbox" checked={selectedReady.length === ready.length} onChange={e => setSelected(e.target.checked ? ready.map(c => c.id) : [])} /> Выбрать все</label>}</div>
    {project?.analysis_provider === 'heuristic' && <div className="info-banner"><CircleAlert size={18} /><p>Использован эвристический режим: оценки приблизительны. Для анализа смысла подключите Ollama или OpenAI в настройках.</p></div>}
    {Boolean(selectedReady.length) && <div className="selection-bar"><span><CheckCircle2 size={17} />Выбрано клипов: {selectedReady.length}</span><a className="button primary" href={`/api/projects/${id}/download?ids=${encodeURIComponent(selectedReady.join(','))}`} download><Download size={16} />Скачать выбранные</a><button className="icon-button" aria-label="Снять выбор" onClick={() => setSelected([])}><X size={18} /></button></div>}
    {clips.length ? <div className="clip-grid">{clips.map((clip, index) => <article className="clip-card" key={clip.id}>
      <div className="clip-preview">{clip.status === 'ready' ? <video controls preload="metadata" playsInline src={previewUrl(clip)} aria-label={`Предпросмотр: ${clip.title}`} /> : <div className="clip-placeholder">{clip.status === 'failed' ? <CircleAlert size={30} /> : <LoaderCircle className="loading-spinner" size={30} />}<span>{clip.status === 'failed' ? 'Ошибка экспорта' : 'Готовим клип…'}</span></div>}<span className="score-pill"><Sparkles size={13} />{clip.score}<span>/100</span></span><label className="clip-select" title="Выбрать клип"><input type="checkbox" aria-label={`Выбрать ${clip.title}`} disabled={clip.status !== 'ready'} checked={selected.includes(clip.id)} onChange={e => setSelected(s => e.target.checked ? [...s, clip.id] : s.filter(value => value !== clip.id))} /></label></div>
      <div className="clip-details"><span className="clip-index">МОМЕНТ {String(index + 1).padStart(2, '0')}</span><h3>{clip.title}</h3><div className="clip-times"><span><Clock3 size={13} />{time(clip.start)} — {time(clip.end)}</span><span>{Math.round(clip.end - clip.start)} сек.</span></div><p className="clip-reason">{clip.reason}</p>{clip.error && <p className="error-text">{clip.error}</p>}<div className="clip-actions"><Link className="button secondary" to={`/clips/${clip.id}/edit`}><SlidersHorizontal size={15} /> Редактор</Link>{clip.status === 'ready' && <a className="icon-button" href={clip.download_url || `/api/clips/${clip.id}/download`} download aria-label={`Скачать ${clip.title}`}><ArrowDownToLine size={17} /></a>}<button className="icon-button" disabled={deleting === clip.id} onClick={() => void remove(clip)} aria-label={`Удалить ${clip.title}`}><Trash2 size={16} /></button></div></div>
    </article>)}</div> : project && !error && <div className="empty-state"><div className="empty-icon"><Scissors size={28} /></div><h3>{project.status === 'completed' ? 'Клипы пока не найдены' : 'Подборка появится после анализа'}</h3><p>{project.status === 'completed' ? 'Попробуйте другую запись или измените настройки анализа.' : 'Откройте прогресс, чтобы узнать текущий этап и проверить ошибки.'}</p><Link className="button secondary" to={`/projects/${id}/processing`}>Открыть прогресс <ArrowRight size={16} /></Link></div>}
  </>;
}

function Slider({ label, value, min = 0, max = 1, step = 0.01, suffix = '', onChange }: { label: string; value: number; min?: number; max?: number; step?: number; suffix?: string; onChange: (v: number) => void }) {
  return <label className="slider-field"><span className="slider-row"><span>{label}</span><strong>{step < 1 ? Math.round(value * 100) : Math.round(value)}{suffix}</strong></span><input type="range" min={min} max={max} step={step} value={value} onChange={e => onChange(Number(e.target.value))} /></label>;
}
function Toggle({ label, description, checked, onChange }: { label: string; description?: string; checked: boolean; onChange: (value: boolean) => void }) {
  return <label className="switch-row"><span><strong>{label}</strong>{description && <small>{description}</small>}</span><input type="checkbox" role="switch" className="switch" checked={checked} onChange={e => onChange(e.target.checked)} /></label>;
}

function Editor() {
  const { id } = useParams();
  const [clip, setClip] = useState<Clip | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [title, setTitle] = useState('');
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState(0);
  const [settings, setSettings] = useState<ClipSettings>(defaultClipSettings);
  const [words, setWords] = useState<Word[]>([]);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [preview, setPreview] = useState<'source' | 'render'>('source');
  const [section, setSection] = useState<'video' | 'subtitles'>('video');
  const source = useRef<HTMLVideoElement>(null);
  const busy = saving || clip?.status === 'queued' || clip?.status === 'rendering';
  const sourceUnavailable = project?.source_available === false;

  useEffect(() => {
    let alive = true;
    setLoading(true); setError(''); setDirty(false);
    api<Clip>(`/clips/${id}`).then(async next => {
      if (!alive) return;
      setClip(next); setTitle(next.title); setStart(next.start); setEnd(next.end);
      setSettings({ ...defaultClipSettings, ...next.settings }); setWords(next.words || []);
      setPreview(next.status === 'ready' ? 'render' : 'source');
      const parent = await api<Project>(`/projects/${next.project_id}`);
      if (alive) setProject(parent);
    }).catch(e => { if (alive) setError(message(e)); }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [id]);
  useEffect(() => {
    if (!clip || !['queued', 'rendering'].includes(clip.status)) return;
    let alive = true;
    const timer = window.setInterval(() => {
      api<Clip>(`/clips/${id}`).then(next => {
        if (!alive) return;
        setClip(next);
        if (next.status === 'ready') { setNotice('Экспорт завершён. Ниже — новая версия MP4.'); setPreview('render'); }
        if (next.status === 'failed') setError(next.error || 'Не удалось экспортировать клип.');
      }).catch(e => { if (alive) setError(message(e)); });
    }, 2500);
    return () => { alive = false; window.clearInterval(timer); };
  }, [id, clip?.status]);
  useEffect(() => {
    const handler = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);
  const changeSettings = (values: Partial<ClipSettings>) => { setSettings(current => ({ ...current, ...values })); setDirty(true); setNotice(''); };
  const save = async () => {
    if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start || (project?.duration && end > project.duration + 0.05)) { setError('Проверьте границы: конец должен быть после начала и в пределах исходного видео.'); return; }
    if (end - start > 180 || end - start < 1) { setError('Длительность клипа должна быть от 1 до 180 секунд.'); return; }
    setSaving(true); setError(''); setNotice(''); source.current?.pause();
    try {
      const next = await api<Clip>(`/clips/${id}/render`, { method: 'POST', body: JSON.stringify({ start, end, title, settings, words }) });
      setClip(next); setWords(next.words || []); setDirty(false); setNotice('Изменения сохранены. Клип в очереди на экспорт.');
    } catch (e) { setError(message(e)); } finally { setSaving(false); }
  };
  if (loading) return <Loading />;
  if (!clip) return <><Header title="Редактор клипа" subtitle="Точная настройка вашего момента" /><ErrorBanner error={error || 'Клип не найден.'} /><Link className="button secondary" to="/">Все проекты</Link></>;
  const visibleWords = words.map((word, index) => ({ ...word, index })).filter(word => word.end >= start && word.start <= end);
  return <>
    <Link className="back-link" to={`/projects/${clip.project_id}/results`} onClick={event => { if (dirty && !window.confirm('Изменения ещё не экспортированы. Покинуть редактор?')) event.preventDefault(); }}><ArrowLeft size={16} /> Назад к клипам</Link>
    <Header eyebrow="CLIP EDITOR" title="Последние штрихи" subtitle="Уточните границы, настройте кадр и сделайте субтитры своими.">
      {clip.status === 'ready' && !dirty && <a className="button secondary" href={clip.download_url || `/api/clips/${clip.id}/download`} download><Download size={17} /> Скачать MP4</a>}
      <button className="button primary" disabled={busy || sourceUnavailable} onClick={() => void save()}>{busy ? <LoaderCircle size={17} className="loading-spinner" /> : <Clapperboard size={17} />}{busy ? 'Экспортируем…' : 'Сохранить и экспортировать'}</button>
    </Header>
    <ErrorBanner error={error} />
    {notice && <div className="info-banner" role="status"><CheckCircle2 size={18} /><p>{notice}</p></div>}
    {sourceUnavailable && <div className="info-banner"><CircleAlert size={18} /><p>Исходник удалён при очистке хранения. Готовый MP4 можно скачать, повторный монтаж недоступен.</p></div>}
    {clip.status === 'failed' && !error && <ErrorBanner error={clip.error || 'Предыдущий экспорт завершился ошибкой. Можно попробовать снова.'} />}
    <div className="editor-grid"><section className="card preview-panel"><div className="card-header"><h2>Предпросмотр</h2><span className="badge neutral">{preview === 'render' ? 'MP4 · 1080 × 1920' : 'ИСХОДНОЕ ВИДЕО'}</span></div><div className="card-body"><div className="tabs"><button className={`tab ${preview === 'source' ? 'tab-active' : ''}`} disabled={sourceUnavailable} onClick={() => setPreview('source')}>Исходник</button><button className={`tab ${preview === 'render' ? 'tab-active' : ''}`} disabled={clip.status !== 'ready'} onClick={() => setPreview('render')}>Готовый экспорт</button></div>
    {preview === 'render' && clip.status === 'ready' ? <video className="editor-video" key={clip.revision} controls playsInline src={previewUrl(clip)} /> : sourceUnavailable ? <div className="empty-state"><Film size={28} /><p>Исходное видео удалено.</p></div> : <video ref={source} className="source-video" controls playsInline preload="metadata" src={`/media/source/${clip.project_id}`} onLoadedMetadata={e => { e.currentTarget.currentTime = start; }} onTimeUpdate={e => { if (e.currentTarget.currentTime > end && !e.currentTarget.paused) e.currentTarget.pause(); }} onError={() => setError('Исходное видео недоступно. Возможно, оно удалено при очистке хранения. Готовые экспорты доступны отдельно.')} />}
      <div className="preview-caption"><span><Clock3 size={14} />{time(start)} — {time(end)}</span><span>{(end - start).toFixed(1)} сек.</span></div><p className="hint">{dirty ? 'Есть изменения. Примените экспорт, чтобы увидеть новое кадрирование, субтитры и эффекты.' : preview === 'source' ? 'Исходник показывает выбранные границы. Эффекты применяются при экспорте.' : 'Это сохранённый MP4 с применёнными настройками.'}</p>{preview === 'source' && !sourceUnavailable && <button className="button secondary full-width" onClick={() => { if (source.current) { source.current.currentTime = start; void source.current.play().catch(() => setError('Браузер не смог воспроизвести исходник. Проверьте его кодек.')); } }}><RefreshCw size={16} /> Воспроизвести выбранный отрывок</button>}
      </div></section><section className="card controls-panel"><div className="card-header"><h2>Настройки клипа</h2>{dirty && <span className="badge warning">Не экспортировано</span>}</div><div className="card-body"><div className="field"><label className="field-label" htmlFor="clip-title">Название</label><input className="input" id="clip-title" value={title} maxLength={120} disabled={busy || sourceUnavailable} onChange={e => { setTitle(e.target.value); setDirty(true); }} /></div><div className="tabs"><button className={`tab ${section === 'video' ? 'tab-active' : ''}`} onClick={() => setSection('video')}><Film size={16} /> Видео</button><button className={`tab ${section === 'subtitles' ? 'tab-active' : ''}`} onClick={() => setSection('subtitles')}><Layers3 size={16} /> Субтитры</button></div>
      <fieldset disabled={busy || sourceUnavailable} className="editor-fieldset">{section === 'video' ? <>
        <h3 className="section-title">Границы отрывка</h3><div className="form-grid"><div className="field"><label className="field-label" htmlFor="clip-start">Начало, сек.</label><input id="clip-start" className="input" type="number" min={0} step={0.1} value={start} onChange={e => { setStart(Number(e.target.value)); setDirty(true); }} /></div><div className="field"><label className="field-label" htmlFor="clip-end">Конец, сек.</label><input id="clip-end" className="input" type="number" min={0} max={project?.duration || undefined} step={0.1} value={end} onChange={e => { setEnd(Number(e.target.value)); setDirty(true); }} /></div></div><p className="hint">Рекомендуем 30–90 секунд, максимум 180. Границы указаны относительно исходной записи.</p>
        <div className="divider" /><h3 className="section-title">Композиция</h3><div className="field"><label className="field-label" htmlFor="layout">Формат кадра</label><select id="layout" className="select" value={settings.layout} onChange={e => changeSettings({ layout: e.target.value as ClipSettings['layout'] })}><option value="crop">Вертикальный кроп · 9:16</option><option value="fit">Весь кадр · с полями</option><option value="split">Камера сверху + игра снизу</option></select></div>
        {settings.layout === 'crop' && <><Toggle label="Автокадрирование по лицу" description="Ищем лицо в нескольких кадрах и выбираем стабильный центр" checked={settings.face_tracking} onChange={face_tracking => changeSettings({ face_tracking })} />{settings.face_tracking && <p className="hint">Если лицо найдено, его положение заменит ручной центр кадра. Это не покадровое слежение.</p>}<Slider label="Центр кадра по горизонтали" suffix="%" value={settings.crop_x} onChange={crop_x => changeSettings({ crop_x })} /><Slider label="Центр кадра по вертикали" suffix="%" value={settings.crop_y} onChange={crop_y => changeSettings({ crop_y })} /></>}
        {settings.layout === 'split' && <><p className="hint">Укажите область камеры в исходнике. Координаты и размер — доли ширины и высоты кадра.</p><div className="form-grid">{['Слева (X)', 'Сверху (Y)', 'Ширина', 'Высота'].map((label, index) => <div className="field" key={label}><label className="field-label" htmlFor={`face-${index}`}>{label}</label><input id={`face-${index}`} className="input" type="number" min={index > 1 ? 0.05 : 0} max={1} step={0.01} value={settings.face_box[index]} onChange={e => { const face_box = [...settings.face_box]; face_box[index] = Number(e.target.value); changeSettings({ face_box }); }} /></div>)}</div></>}
        <div className="divider" /><Toggle label="Автоматический zoom" description="Лёгкое плавное приближение в клипе" checked={settings.auto_zoom} onChange={auto_zoom => changeSettings({ auto_zoom })} /><Toggle label="Сократить длинные паузы" description="Убирает затяжное молчание между словами" checked={settings.remove_silence} onChange={remove_silence => changeSettings({ remove_silence })} />
      </> : <>
        <Toggle label="Динамические субтитры" description="Короткие фразы с подсветкой произносимого слова" checked={settings.subtitles} onChange={subtitles => changeSettings({ subtitles })} />
        <div className="form-grid"><div className="field"><label className="field-label" htmlFor="font">Шрифт</label><select id="font" className="select" value={settings.subtitle_font} onChange={e => changeSettings({ subtitle_font: e.target.value })}><option>Arial</option><option>DejaVu Sans</option><option>Verdana</option></select></div><div className="field"><label className="field-label" htmlFor="color">Цвет подсветки</label><input className="input color-input" id="color" type="color" value={settings.subtitle_color} onChange={e => changeSettings({ subtitle_color: e.target.value })} /></div></div>
        <Slider label="Размер текста" min={28} max={100} step={1} suffix=" px" value={settings.subtitle_size} onChange={subtitle_size => changeSettings({ subtitle_size })} /><Slider label="Положение сверху" min={0.15} max={0.9} suffix="%" value={settings.subtitle_position} onChange={subtitle_position => changeSettings({ subtitle_position })} /><div className="subtitle-preview" style={{ fontFamily: settings.subtitle_font }}>Тот самый <span style={{ color: settings.subtitle_color }}>момент</span></div><p className="hint">Образец стиля. Точное положение видно после экспорта.</p><div className="divider" /><h3 className="section-title">Текст субтитров <span className="count-pill">{visibleWords.length}</span></h3><p className="hint">Исправляйте слова, сохраняя исходные таймкоды. Пробелы внутри поля допустимы.</p><div className="transcript-list">{visibleWords.length ? visibleWords.map(word => <label className="transcript-row" key={word.index}><span className="transcript-time">{time(word.start)}</span><input className="transcript-word" aria-label={`Слово на ${time(word.start)}`} value={word.text} maxLength={100} onChange={e => { setWords(current => current.map((w, index) => index === word.index ? { ...w, text: e.target.value } : w)); setDirty(true); }} /></label>) : <p className="hint">В выбранных границах нет распознанных слов.</p>}</div>
      </>}</fieldset></div></section></div>
  </>;
}

function SettingsPage() {
  const { data, setData, error, loading, refresh } = useData<Settings>('/settings');
  const [form, setForm] = useState<Settings | null>(null);
  const [saving, setSaving] = useState(false);
  const [cleaning, setCleaning] = useState(false);
  const [actionError, setActionError] = useState('');
  const [notice, setNotice] = useState('');
  useEffect(() => { if (data) setForm(data); }, [data]);
  const update = (values: Partial<Settings>) => { setForm(current => current ? { ...current, ...values } : current); setNotice(''); };
  const save = async (event: FormEvent) => {
    event.preventDefault(); if (!form) return;
    setSaving(true); setActionError(''); setNotice('');
    try {
      const { providers: _providers, max_upload_gb: _limit, ...values } = form;
      const result = await api<Settings>('/settings', { method: 'PATCH', body: JSON.stringify(values) });
      setData({ ...form, ...result, providers: result.providers || form.providers, max_upload_gb: result.max_upload_gb ?? form.max_upload_gb }); setNotice('Настройки сохранены. Они будут применены к новым заданиям.');
    } catch (e) { setActionError(message(e)); } finally { setSaving(false); }
  };
  const cleanup = async () => {
    if (!window.confirm('Удалить исходники завершённых проектов старше срока хранения? Готовые клипы останутся, но повторный монтаж этих исходников станет недоступен.')) return;
    setCleaning(true); setActionError(''); setNotice('');
    try { const result = await api<{ deleted: number }>('/storage/sources', { method: 'DELETE' }); setNotice(`Очистка завершена. Удалено исходников: ${result.deleted}.`); } catch (e) { setActionError(message(e)); } finally { setCleaning(false); }
  };
  return <>
    <Header eyebrow="ПОД ВАШ ПРОЦЕСС" title="Настройки" subtitle="Выберите модели, контролируйте стоимость и управляйте хранением." />
    <ErrorBanner error={error || actionError} retry={() => void refresh()} />
    {notice && <div className="info-banner" role="status"><CheckCircle2 size={19} /><p>{notice}</p></div>}
    {loading ? <Loading /> : form && <form onSubmit={save}><div className="settings-grid"><div className="card settings-card"><div className="card-header"><h2><AudioLines size={18} />Распознавание речи</h2></div><div className="card-body"><div className="field"><label className="field-label" htmlFor="transcription-provider">Провайдер</label><select id="transcription-provider" className="select" value={form.transcription_provider} onChange={e => update({ transcription_provider: e.target.value as Settings['transcription_provider'] })}><option value="local">Локально · faster-whisper</option><option value="openai">OpenAI · платный API</option></select></div>{form.transcription_provider === 'local' ? <div className="field"><label className="field-label" htmlFor="whisper-model">Модель Whisper</label><select id="whisper-model" className="select" value={form.whisper_model} onChange={e => update({ whisper_model: e.target.value })}>{['tiny', 'base', 'small', 'medium', 'large-v3', 'turbo'].map(model => <option key={model}>{model}</option>)}</select><p className="hint">Small — разумный старт для CPU. Более крупные модели требуют больше памяти и времени. При первом запуске веса скачиваются автоматически.</p></div> : <div className={`provider-status ${form.providers.openai ? 'success' : 'warning'}`}>{form.providers.openai ? <CheckCircle2 size={17} /> : <CircleAlert size={17} />}{form.providers.openai ? 'Ключ OpenAI задан на сервере' : 'Добавьте OPENAI_API_KEY в .env сервера'}</div>}<div className="field"><label className="field-label" htmlFor="language">Язык записи</label><select id="language" className="select" value={form.language} onChange={e => update({ language: e.target.value })}><option value="auto">Определять автоматически</option><option value="ru">Русский</option><option value="en">Английский</option><option value="uk">Украинский</option></select></div></div></div>
      <div className="card settings-card"><div className="card-header"><h2><Sparkles size={18} />Анализ моментов</h2></div><div className="card-body"><div className="field"><label className="field-label" htmlFor="analysis-provider">Понимание контекста</label><select id="analysis-provider" className="select" value={form.analysis_provider} onChange={e => update({ analysis_provider: e.target.value as Settings['analysis_provider'] })}><option value="ollama">Ollama · локальная LLM</option><option value="openai">OpenAI · платный API</option><option value="heuristic">Эвристики · без LLM</option></select></div>{form.analysis_provider === 'ollama' ? <><div className="field"><label className="field-label" htmlFor="ollama-model">Модель Ollama</label><input id="ollama-model" className="input" value={form.ollama_model} onChange={e => update({ ollama_model: e.target.value })} placeholder="qwen2.5:7b" required /></div><p className="hint">Установите Ollama, выполните <code>ollama pull {form.ollama_model || 'qwen2.5:7b'}</code> и запустите сервис. Адрес задаётся через OLLAMA_BASE_URL в .env.</p><div className={`provider-status ${form.providers.ollama ? 'success' : 'warning'}`}>{form.providers.ollama ? <CheckCircle2 size={17} /> : <CircleAlert size={17} />}{form.providers.ollama ? 'Ollama доступна серверу' : 'Ollama пока недоступна серверу'}</div></> : form.analysis_provider === 'openai' ? <><div className="field"><label className="field-label" htmlFor="openai-model">Модель анализа</label><input id="openai-model" className="input" value={form.openai_model} onChange={e => update({ openai_model: e.target.value })} required /></div><div className={`provider-status ${form.providers.openai ? 'success' : 'warning'}`}><CircleAlert size={17} />{form.providers.openai ? 'Ключ задан. Запросы оплачиваются по тарифам API.' : 'Добавьте OPENAI_API_KEY в .env и перезапустите сервер.'}</div></> : <div className="info-banner"><CircleAlert size={18} /><p>Эвристики выделяют кандидатов по тексту и сигналам аудио. Они не дают полноценного понимания контекста. Для качественного отбора используйте LLM.</p></div>}<div className="field"><label className="field-label" htmlFor="max-clips">Клипов на проект</label><input id="max-clips" className="input" type="number" min={1} max={20} value={form.max_clips} onChange={e => update({ max_clips: Number(e.target.value) })} required /></div></div></div>
      <div className="card settings-card"><div className="card-header"><h2><FolderOpen size={18} />Хранение</h2></div><div className="card-body"><div className="field"><label className="field-label" htmlFor="retention-days">Хранить исходники, дней</label><input id="retention-days" className="input" type="number" min={1} max={365} value={form.retention_days} onChange={e => update({ retention_days: Number(e.target.value) })} required /><p className="hint">Очистка затрагивает только исходники завершённых и неудачных проектов старше этого срока. Готовые MP4 остаются.</p></div><div className="provider-status"><ShieldCheck size={17} />Лимит загрузки: {form.max_upload_gb} ГБ на файл</div><button type="button" className="button secondary" disabled={cleaning} onClick={() => void cleanup()}>{cleaning ? <LoaderCircle size={16} className="loading-spinner" /> : <Trash2 size={16} />}Очистить старые исходники</button></div></div>
      <div className="card settings-card"><div className="card-header"><h2><ShieldCheck size={18} />Окружение</h2></div><div className="card-body"><div className="status-line"><span>FFmpeg</span><span className={`badge ${form.providers.ffmpeg ? 'success' : 'error'}`}>{form.providers.ffmpeg ? 'Найден' : 'Не найден'}</span></div><div className="divider" /><h3>Ключи остаются на сервере</h3><p className="hint">Секреты задаются в корневом .env и не передаются браузеру. Локальные модели не требуют оплаты API, но используют ресурсы компьютера.</p><p className="hint">Для фоновой обработки должны работать API и отдельный воркер. Инструкции установки и запуска находятся в README проекта.</p><button className="button ghost" type="button" onClick={() => void refresh()}><RefreshCw size={16} /> Проверить доступность</button></div></div>
    </div><div className="settings-save"><p className="hint">Текущие задания используют настройки, сохранённые при их создании.</p><button className="button primary" type="submit" disabled={saving}>{saving ? <LoaderCircle size={17} className="loading-spinner" /> : <Check size={17} />}Сохранить настройки</button></div></form>}
  </>;
}

function NotFound() {
  return <div className="empty-state"><div className="empty-icon"><Film size={28} /></div><h1>Страница не найдена</h1><p>Вернитесь в библиотеку, чтобы открыть проект.</p><Link className="button primary" to="/">Все проекты <ArrowRight size={16} /></Link></div>;
}

export default function App() {
  const [menu, setMenu] = useState(false);
  return <div className="app-shell"><button className="mobile-menu icon-button" aria-label={menu ? 'Закрыть меню' : 'Открыть меню'} aria-expanded={menu} onClick={() => setMenu(!menu)}>{menu ? <X size={22} /> : <Menu size={22} />}</button>{menu && <button className="sidebar-backdrop" aria-label="Закрыть меню" onClick={() => setMenu(false)} />}
    <aside className={`sidebar ${menu ? 'sidebar-open' : ''}`}><Link className="brand" to="/" onClick={() => setMenu(false)}><span className="brand-mark"><Scissors size={21} strokeWidth={2.3} /></span><span>clipforge<span className="brand-period">.</span></span></Link><div className="workspace-label">ВАШЕ ПРОСТРАНСТВО</div><nav aria-label="Основная навигация"><NavLink to="/" end className={({ isActive }) => `nav-link ${isActive ? 'nav-active' : ''}`} onClick={() => setMenu(false)}><LayoutDashboard size={18} />Главная</NavLink><NavLink to="/upload" className={({ isActive }) => `nav-link ${isActive ? 'nav-active' : ''}`} onClick={() => setMenu(false)}><Plus size={19} />Новый проект</NavLink><NavLink to="/settings" className={({ isActive }) => `nav-link ${isActive ? 'nav-active' : ''}`} onClick={() => setMenu(false)}><Settings2 size={18} />Настройки</NavLink></nav><div className="sidebar-footer"><div className="sidebar-tip"><span className="section-icon"><Sparkles size={17} /></span><h4>История важнее шума</h4><p>Лучший клип начинается чуть раньше самого яркого момента.</p></div><div className="sidebar-version"><span className="status-dot" /> Локальное пространство <span>MVP</span></div></div></aside>
    <main className="main-content"><Routes><Route path="/" element={<Dashboard />} /><Route path="/upload" element={<Upload />} /><Route path="/projects/:id/processing" element={<Processing />} /><Route path="/projects/:id/results" element={<Results />} /><Route path="/clips/:id/edit" element={<Editor />} /><Route path="/settings" element={<SettingsPage />} /><Route path="*" element={<NotFound />} /></Routes></main>
  </div>;
}
