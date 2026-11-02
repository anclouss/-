export type Word = { start: number; end: number; text: string };
export type ClipSettings = {
  layout: "crop" | "fit" | "split";
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
  status: "draft" | "queued" | "rendering" | "ready" | "failed";
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
  status: "queued" | "processing" | "completed" | "failed" | "cancelled";
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
  mode?: "auto" | "editor";
};
export type Settings = {
  transcription_provider: "local" | "openai";
  whisper_model: string;
  analysis_provider: "ollama" | "openai" | "heuristic";
  openai_model: string;
  ollama_model: string;
  max_clips: number;
  retention_days: number;
  max_upload_gb: number;
  language: string;
  providers: { openai: boolean; ollama: boolean; ffmpeg: boolean };
};
export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
export const message = (error: unknown): string =>
  error instanceof Error ? error.message : "Не удалось выполнить действие.";
export const aborted = (error: unknown): boolean =>
  error instanceof DOMException && error.name === "AbortError";
function errorMessage(data: unknown, fallback: string): string {
  if (typeof data !== "object" || data === null || !("detail" in data))
    return fallback;
  const detail: unknown = data.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail
      .map((entry: unknown) =>
        typeof entry === "object" &&
        entry !== null &&
        "msg" in entry &&
        typeof entry.msg === "string"
          ? entry.msg
          : "Проверьте параметры запроса.",
      )
      .join("; ");
  return fallback;
}
export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body) headers.set("Content-Type", "application/json");
  let response: Response;
  try {
    response = await fetch(`/api${path}`, { ...options, headers });
  } catch (error) {
    if (aborted(error)) throw error;
    throw new ApiError(
      "Сервер недоступен. Проверьте окно запуска ClipForge.",
      0,
    );
  }
  if (!response.ok)
    throw new ApiError(
      errorMessage(
        await response.json().catch(() => null),
        `Сервер вернул ошибку ${response.status}.`,
      ),
      response.status,
    );
  return response.status === 204
    ? (undefined as T)
    : (response.json() as Promise<T>);
}
export function uploadFile(
  file: File,
  title: string,
  mode: "editor" | "auto",
  onProgress: (value: number) => void,
  signal: AbortSignal,
): Promise<Project> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Загрузка отменена.", "AbortError"));
      return;
    }
    const xhr = new XMLHttpRequest();
    const cancel = () => xhr.abort();
    const cleanup = () => {
      signal.removeEventListener("abort", cancel);
      xhr.onload = null;
      xhr.onerror = null;
      xhr.onabort = null;
      xhr.upload.onprogress = null;
    };
    xhr.open("POST", "/api/projects/upload");
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable)
        onProgress(Math.round((event.loaded / event.total) * 100));
    };
    xhr.onerror = () => {
      cleanup();
      reject(
        new ApiError(
          "Загрузка прервана соединением. Проверьте сервер и повторите.",
          0,
        ),
      );
    };
    xhr.onabort = () => {
      cleanup();
      reject(new DOMException("Загрузка отменена.", "AbortError"));
    };
    xhr.onload = () => {
      const status = xhr.status;
      let body: unknown;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        cleanup();
        reject(new ApiError("Сервер вернул некорректный ответ.", status));
        return;
      }
      cleanup();
      if (status >= 200 && status < 300) resolve(body as Project);
      else
        reject(
          new ApiError(
            errorMessage(body, `Не удалось загрузить файл (${status}).`),
            status,
          ),
        );
    };
    signal.addEventListener("abort", cancel, { once: true });
    const data = new FormData();
    data.append("file", file);
    data.append("mode", mode);
    data.append("defer_render", "true");
    if (title.trim()) data.append("title", title.trim());
    xhr.send(data);
  });
}
export function time(value: number, decimals = false): string {
  if (!Number.isFinite(value)) return "00:00";
  const seconds = Math.max(0, value),
    whole = Math.floor(seconds),
    hours = Math.floor(whole / 3600);
  return `${hours ? `${String(hours).padStart(2, "0")}:` : ""}${String(Math.floor((whole % 3600) / 60)).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}${decimals ? `.${Math.floor((seconds % 1) * 10)}` : ""}`;
}
export const stageLabels: Record<string, string> = {
  queued: "В очереди",
  uploading: "Загрузка",
  downloading: "Загрузка записи",
  extracting_audio: "Подготовка аудио",
  transcribing: "Распознавание речи",
  analyzing: "Анализ контекста",
  finding_clips: "Выбор моментов",
  rendering: "Рендеринг",
  done: "Готово",
  preparing: "Подготовка",
  adding_subtitles: "Субтитры",
  encoding: "Кодирование",
  finalizing: "Завершение",
};
