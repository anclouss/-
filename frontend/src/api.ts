export type Word = { start: number; end: number; text: string };
export type ClipSettings = {
  layout: 'crop' | 'fit' | 'split';
  crop_x: number;
  crop_y: number;
  face_box: number[];
  subtitle_font: string;
  subtitle_size: number;
  subtitle_position: number;
  subtitle_color: string;
  subtitles: boolean;
  auto_zoom: boolean;
  remove_silence: boolean;
  face_tracking: boolean;
};
export type Clip = {
  id: string;
  project_id: string;
  title: string;
  reason: string;
  score: number;
  start: number;
  end: number;
  duration: number;
  status: 'queued' | 'rendering' | 'ready' | 'failed';
  error?: string | null;
  revision: number;
  settings: Partial<ClipSettings>;
  words: Word[];
  preview_url?: string | null;
  download_url?: string | null;
};
export type Project = {
  id: string;
  title: string;
  status: 'queued' | 'processing' | 'completed' | 'failed';
  stage: string;
  progress: number;
  error?: string | null;
  source_name: string;
  duration?: number;
  created_at: string;
  clip_count: number;
  analysis_provider?: string;
  source_available?: boolean;
  clips?: Clip[];
};
export type Settings = {
  transcription_provider: 'local' | 'openai';
  whisper_model: string;
  analysis_provider: 'ollama' | 'openai' | 'heuristic';
  openai_model: string;
  ollama_model: string;
  max_clips: number;
  retention_days: number;
  max_upload_gb: number;
  language: string;
  providers: { openai: boolean; ollama: boolean; ffmpeg: boolean };
};

export const defaultClipSettings: ClipSettings = {
  layout: 'crop', crop_x: 0.5, crop_y: 0.5, face_box: [0, 0, 0.3, 0.4],
  subtitle_font: 'Arial', subtitle_size: 58, subtitle_position: 0.76,
  subtitle_color: '#B8FF60', subtitles: true, auto_zoom: false,
  remove_silence: false, face_tracking: false,
};

function errorMessage(data: unknown, fallback: string): string {
  if (typeof data === 'object' && data !== null && 'detail' in data) {
    const detail = (data as { detail: unknown }).detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) return detail.map(d => d.msg || JSON.stringify(d)).join('; ');
  }
  return fallback;
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      ...options,
      headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers },
    });
  } catch {
    throw new Error('Нет связи с сервером. Проверьте, что backend запущен на порту 8000.');
  }
  if (!res.ok) {
    const data: unknown = await res.json().catch(() => null);
    throw new Error(errorMessage(data, `Ошибка сервера (${res.status})`));
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export function uploadFile(file: File, title: string, onProgress: (value: number) => void, signal?: AbortSignal): Promise<Project> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/projects/upload');
    xhr.upload.onprogress = event => {
      if (event.lengthComputable) onProgress(Math.round(event.loaded / event.total * 100));
    };
    xhr.onerror = () => reject(new Error('Не удалось загрузить файл. Проверьте соединение с backend.'));
    xhr.onabort = () => reject(new Error('Загрузка отменена.'));
    xhr.onload = () => {
      let body: unknown;
      try { body = JSON.parse(xhr.responseText); } catch { reject(new Error('Некорректный ответ сервера.')); return; }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as Project);
      else reject(new Error(errorMessage(body, `Ошибка загрузки (${xhr.status})`)));
    };
    if (signal) signal.addEventListener('abort', () => xhr.abort(), { once: true });
    const data = new FormData();
    data.append('file', file);
    if (title.trim()) data.append('title', title.trim());
    xhr.send(data);
  });
}

export function time(value: number): string {
  if (!Number.isFinite(value)) return '00:00';
  const seconds = Math.max(0, Math.floor(value));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor(seconds % 3600 / 60);
  const rest = seconds % 60;
  return `${hours ? `${String(hours).padStart(2, '0')}:` : ''}${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`;
}

export function previewUrl(clip: Clip): string {
  const url = clip.preview_url || `/media/clips/${clip.id}`;
  return `${url}${url.includes('?') ? '&' : '?'}revision=${clip.revision || 0}`;
}

export const stageLabels: Record<string, string> = {
  uploading: 'Загрузка видео', queued: 'В очереди', downloading: 'Загрузка по ссылке',
  extracting_audio: 'Извлечение аудио', transcribing: 'Распознавание речи',
  analyzing: 'Анализ контекста', finding_clips: 'Поиск лучших моментов',
  rendering: 'Экспорт клипов', done: 'Готово', failed: 'Ошибка обработки',
};
