import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronLeft, ChevronRight, Film, Search, X } from "lucide-react";
import { time } from "../api";
import type { Project, Word } from "../api";
import { useResource } from "../hooks/useResource";
import type {
  DocumentState,
  EditorSession,
  TranscriptPage,
} from "../types/editor";
import { ErrorBanner, Skeleton } from "../ui";
export function AssetsPanel({
  session,
  document,
  disabled,
  open,
  onClose,
  onSeek,
  onWord,
}: {
  session: EditorSession;
  document: DocumentState;
  disabled: boolean;
  open: boolean;
  onClose: () => void;
  onSeek: (source: number) => void;
  onWord: (word: Word, text: string) => void;
}) {
  const [tab, setTab] = useState<"assets" | "clips" | "transcript">("assets"),
    [query, setQuery] = useState(""),
    [search, setSearch] = useState(""),
    [offset, setOffset] = useState(0);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearch(query);
      setOffset(0);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  const transcript = useResource<TranscriptPage>(
    `/editor/${session.clip.id}/transcript?q=${encodeURIComponent(search)}&offset=${offset}&limit=100`,
  );
  const project = useResource<Project>(
    `/projects/${session.project.id}?compact=true`,
  );
  const { refresh: refreshProject } = project,
    { refresh: refreshTranscript } = transcript;
  const projectStatus = useRef(
    `${session.project.status}:${session.project.clip_count}`,
  );
  useEffect(() => {
    const next = `${session.project.status}:${session.project.clip_count}`;
    if (next !== projectStatus.current) {
      projectStatus.current = next;
      void refreshProject();
      void refreshTranscript();
    }
  }, [
    session.project.status,
    session.project.clip_count,
    refreshProject,
    refreshTranscript,
  ]);
  const words = transcript.data?.words || [];
  return (
    <aside className={`panel asset-panel ${open ? "open" : ""}`}>
      <div className="panel-tabs">
        <button
          className={tab === "assets" ? "active" : ""}
          onClick={() => setTab("assets")}
        >
          Файлы
        </button>
        <button
          className={tab === "clips" ? "active" : ""}
          onClick={() => setTab("clips")}
        >
          Клипы
        </button>
        <button
          className={tab === "transcript" ? "active" : ""}
          onClick={() => setTab("transcript")}
        >
          Текст
        </button>
        <button
          className="icon-button assets-close"
          onClick={onClose}
          aria-label="Закрыть файлы"
        >
          <X size={14} />
        </button>
      </div>
      <div className="panel-scroll">
        {tab === "assets" && (
          <>
            <div className="asset-item asset-active">
              <div className="asset-thumb">
                <Film size={24} />
              </div>
              <div className="asset-meta">
                <strong>{session.project.source_name}</strong>
                <span>
                  {session.source.width} × {session.source.height}
                </span>
                <span>
                  {time(session.source.duration)} ·{" "}
                  {Math.round(session.source.fps || 30)} FPS
                </span>
              </div>
            </div>
            <p className="hint">
              Оригинал сохраняется отдельно. Действия редактора изменяют только
              инструкции монтажа.
            </p>
            <div className="asset-history">
              <h3>История</h3>
              {session.history.entries
                .slice(-15)
                .reverse()
                .map((entry, index) => (
                  <div key={`${entry.created_at}-${index}`}>
                    <span>{entry.label}</span>
                    <small>
                      {new Date(entry.created_at).toLocaleTimeString("ru-RU", {
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </small>
                  </div>
                ))}
            </div>
          </>
        )}
        {tab === "clips" && (
          <>
            {project.loading ? (
              <Skeleton rows={3} />
            ) : (
              project.data?.clips?.map((clip) => (
                <Link
                  className={`asset-item ${clip.id === session.clip.id ? "asset-active" : ""}`}
                  key={clip.id}
                  to={`/clips/${clip.id}/edit`}
                >
                  <div className="asset-thumb">
                    <Film size={19} />
                  </div>
                  <div className="asset-meta">
                    <strong>{clip.title}</strong>
                    <span>
                      {time(clip.start)} — {time(clip.end)}
                    </span>
                    <span>
                      {clip.score}/100 ·{" "}
                      {clip.status === "ready" ? "MP4 готов" : "Заготовка"}
                    </span>
                  </div>
                </Link>
              ))
            )}
            <ErrorBanner error={project.error} />
          </>
        )}
        {tab === "transcript" && (
          <>
            <label className="transcript-search">
              <Search size={14} />
              <input
                className="input"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Найти фразу…"
                aria-label="Поиск по транскрипту"
              />
            </label>
            <ErrorBanner error={transcript.error} />
            {transcript.loading ? (
              <Skeleton />
            ) : (
              <div className="transcript-scroll">
                {words.length ? (
                  words.map((word, index) => (
                    <div
                      className="transcript-row"
                      key={`${search}-${offset}-${word.start}-${index}`}
                    >
                      <button
                        className="transcript-time"
                        onClick={() => onSeek(word.start)}
                        title="Перейти к слову"
                      >
                        {time(word.start)}
                      </button>
                      <input
                        className="transcript-text"
                        aria-label={`Текст на ${time(word.start)}`}
                        value={
                          document.caption_overrides[String(word.start)] ??
                          word.text
                        }
                        disabled={disabled}
                        maxLength={100}
                        onChange={(event) => onWord(word, event.target.value)}
                      />
                    </div>
                  ))
                ) : (
                  <p className="hint">
                    {search
                      ? "Совпадений нет."
                      : "Транскрипции пока нет. Автоматический анализ добавит слова с таймкодами."}
                  </p>
                )}
              </div>
            )}
            <div className="pagination">
              <button
                className="icon-button"
                aria-label="Предыдущие слова"
                disabled={!offset}
                onClick={() => setOffset(Math.max(0, offset - 100))}
              >
                <ChevronLeft size={16} />
              </button>
              <span>
                {transcript.data?.total
                  ? `${offset + 1}–${Math.min(offset + 100, transcript.data.total)} / ${transcript.data.total}`
                  : "0 слов"}
              </span>
              <button
                className="icon-button"
                aria-label="Следующие слова"
                disabled={
                  !transcript.data || offset + 100 >= transcript.data.total
                }
                onClick={() => setOffset(offset + 100)}
              >
                <ChevronRight size={16} />
              </button>
            </div>
          </>
        )}
      </div>
    </aside>
  );
}
