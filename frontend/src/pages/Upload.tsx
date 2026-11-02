import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowRight,
  FileVideo,
  Link2,
  Square,
  UploadCloud,
  Wand2,
} from "lucide-react";
import { aborted, ApiError, api, message, uploadFile } from "../api";
import type { Clip, Project, Settings } from "../api";
import { useResource } from "../hooks/useResource";
import { ErrorBanner, PageHeader } from "../ui";
export function Upload() {
  const [tab, setTab] = useState<"file" | "url">("file"),
    [mode, setMode] = useState<"editor" | "auto">("editor");
  const [file, setFile] = useState<File | null>(null),
    [url, setUrl] = useState(""),
    [title, setTitle] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [progress, setProgress] = useState(0),
    [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null),
    controller = useRef<AbortController | null>(null),
    alive = useRef(true);
  const navigate = useNavigate();
  const { data: settings } = useResource<Settings>("/settings");
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      controller.current?.abort();
    };
  }, []);
  const choose = (next?: File) => {
    if (!next) return;
    if (!/\.(mp4|mov|mkv|webm|avi|m4v|ts|flv)$/i.test(next.name)) {
      setError("Поддерживаются MP4, MOV, MKV, WebM, AVI, TS и FLV.");
      return;
    }
    if (next.size > (settings?.max_upload_gb || 4) * 1024 ** 3) {
      setError(`Файл превышает лимит ${settings?.max_upload_gb || 4} ГБ.`);
      return;
    }
    setError("");
    setFile(next);
    if (!title) setTitle(next.name.replace(/\.[^.]+$/, "").slice(0, 120));
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    setProgress(0);
    const request = new AbortController();
    controller.current = request;
    try {
      let project: Project;
      if (tab === "file") {
        if (!file) throw new Error("Выберите видео.");
        project = await uploadFile(
          file,
          title,
          mode,
          setProgress,
          request.signal,
        );
      } else {
        project = await api<Project>("/projects/url", {
          method: "POST",
          body: JSON.stringify({ url, title, mode, defer_render: true }),
          signal: request.signal,
        });
      }
      if (!alive.current || request.signal.aborted) return;
      if (mode === "editor" && project.source_available) {
        try {
          const clip = await api<Clip>(`/projects/${project.id}/editor`, {
            method: "POST",
            signal: request.signal,
          });
          if (alive.current) navigate(`/clips/${clip.id}/edit`);
        } catch (failure) {
          if (failure instanceof ApiError && failure.status === 409)
            navigate(`/projects/${project.id}/processing`);
          else throw failure;
        }
      } else navigate(`/projects/${project.id}/processing`);
    } catch (failure) {
      if (alive.current)
        setError(aborted(failure) ? "Загрузка отменена." : message(failure));
    } finally {
      if (alive.current) {
        setBusy(false);
        controller.current = null;
      }
    }
  };
  return (
    <>
      <PageHeader
        title="Новый проект"
        description="Загрузите исходник. Монтаж и экспорт сохраняются отдельно от оригинала."
      />
      <form className="upload-layout" onSubmit={submit}>
        <div className="panel">
          <div className="panel-heading">
            <h2>Источник</h2>
            <div className="tabs">
              <button
                type="button"
                className={tab === "file" ? "active" : ""}
                disabled={busy}
                onClick={() => setTab("file")}
              >
                <UploadCloud size={15} />
                Файл
              </button>
              <button
                type="button"
                className={tab === "url" ? "active" : ""}
                disabled={busy}
                onClick={() => setTab("url")}
              >
                <Link2 size={15} />
                Ссылка
              </button>
            </div>
          </div>
          <div className="panel-body">
            {tab === "file" ? (
              <>
                <input
                  ref={input}
                  type="file"
                  hidden
                  accept="video/*,.mkv,.ts,.avi"
                  disabled={busy}
                  onChange={(event) => choose(event.target.files?.[0])}
                />
                <div
                  className={`upload-zone ${dragging ? "dragging" : ""}`}
                  role="button"
                  tabIndex={busy ? -1 : 0}
                  aria-label="Выбрать видео"
                  onClick={() => !busy && input.current?.click()}
                  onKeyDown={(event) => {
                    if (!busy && ["Enter", " "].includes(event.key)) {
                      event.preventDefault();
                      input.current?.click();
                    }
                  }}
                  onDragOver={(event) => {
                    event.preventDefault();
                    if (!busy) setDragging(true);
                  }}
                  onDragLeave={() => setDragging(false)}
                  onDrop={(event) => {
                    event.preventDefault();
                    setDragging(false);
                    if (!busy) choose(event.dataTransfer.files[0]);
                  }}
                >
                  <FileVideo size={32} />
                  <strong>
                    {file ? file.name : "Перетащите видео или выберите файл"}
                  </strong>
                  <span>
                    {file
                      ? `${(file.size / 1024 ** 2).toFixed(1)} МБ`
                      : `До ${settings?.max_upload_gb || 4} ГБ · MP4 / MOV / MKV / WebM`}
                  </span>
                </div>
              </>
            ) : (
              <label className="field">
                Ссылка на запись
                <input
                  type="url"
                  className="input"
                  placeholder="https://youtube.com/watch?v=…"
                  value={url}
                  disabled={busy}
                  required
                  onChange={(event) => setUrl(event.target.value)}
                />
                <span className="hint">
                  Публичные завершённые записи YouTube и Twitch.
                </span>
              </label>
            )}
            <label className="field">
              Название
              <input
                className="input"
                value={title}
                maxLength={120}
                disabled={busy}
                placeholder="Название проекта"
                onChange={(event) => setTitle(event.target.value)}
              />
            </label>
            <ErrorBanner error={error} />
            {busy && (
              <div className="upload-progress">
                <div className="row">
                  <span>
                    {tab === "url"
                      ? "Добавляем запись в очередь…"
                      : progress < 100
                        ? "Передаём видео…"
                        : "Проверяем видео…"}
                  </span>
                  <strong>{tab === "file" ? `${progress}%` : ""}</strong>
                </div>
                <div className="progress">
                  <i style={{ width: `${tab === "file" ? progress : 20}%` }} />
                </div>
                <button
                  className="button ghost"
                  type="button"
                  onClick={() => controller.current?.abort()}
                >
                  <Square size={12} />
                  Отменить
                </button>
              </div>
            )}
          </div>
        </div>
        <aside className="panel">
          <div className="panel-heading">
            <h2>С чего начать</h2>
          </div>
          <div className="panel-body">
            <button
              type="button"
              className={`mode-option ${mode === "editor" ? "selected" : ""}`}
              disabled={busy}
              onClick={() => setMode("editor")}
            >
              <FileVideo size={20} />
              <strong>Открыть в редакторе</strong>
              <span>
                Сразу работайте с исходником, таймлайном и AI-командами.
              </span>
            </button>
            <button
              type="button"
              className={`mode-option ${mode === "auto" ? "selected" : ""}`}
              disabled={busy}
              onClick={() => setMode("auto")}
            >
              <Wand2 size={20} />
              <strong>Найти лучшие моменты</strong>
              <span>
                Распознаем речь и создадим монтажные заготовки. Экспорт — когда
                вы готовы.
              </span>
            </button>
            <button
              className="button primary full-width"
              disabled={busy || (tab === "file" ? !file : !url)}
              type="submit"
            >
              {busy ? "Подготовка…" : "Продолжить"}
              <ArrowRight size={15} />
            </button>
            <p className="hint">
              После передачи файла обработка продолжится в фоне. Исходник
              остаётся неизменным.
            </p>
          </div>
        </aside>
      </form>
    </>
  );
}
