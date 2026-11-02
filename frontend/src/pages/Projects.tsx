import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  ArrowUpRight,
  Check,
  Download,
  Film,
  RefreshCw,
  Square,
  Trash2,
} from "lucide-react";
import { api, message, stageLabels, time } from "../api";
import type { Clip, Project } from "../api";
import { useResource } from "../hooks/useResource";
import { Empty, ErrorBanner, PageHeader, Skeleton } from "../ui";
function useOpenEditor() {
  const navigate = useNavigate();
  return async (id: string) => {
    const clip = await api<Clip>(`/projects/${id}/editor`, { method: "POST" });
    navigate(`/clips/${clip.id}/edit`);
  };
}
export function Processing() {
  const { id } = useParams(),
    {
      data: project,
      loading,
      error,
      refresh,
    } = useResource<Project>(`/projects/${id}`, 2500);
  const [actionError, setActionError] = useState(""),
    [busy, setBusy] = useState(false);
  const open = useOpenEditor();
  const active = project && ["queued", "processing"].includes(project.status);
  const run = async (action: "retry" | "cancel" | "editor") => {
    setBusy(true);
    setActionError("");
    try {
      if (action === "editor") await open(String(id));
      else {
        await api(`/projects/${id}/${action}`, { method: "POST" });
        await refresh();
      }
    } catch (failure) {
      setActionError(message(failure));
    } finally {
      setBusy(false);
    }
  };
  const stages = [
    "uploading",
    "extracting_audio",
    "transcribing",
    "analyzing",
    "finding_clips",
    "done",
  ];
  const index = stages.indexOf(project?.stage || "");
  return (
    <>
      <Link className="back-link" to="/">
        <ArrowLeft size={14} />
        Проекты
      </Link>
      <PageHeader
        title={project?.title || "Подготовка проекта"}
        description="Можно закрыть страницу и вернуться позже."
      />
      <ErrorBanner error={error || actionError} retry={() => void refresh()} />
      {loading ? (
        <Skeleton />
      ) : (
        project && (
          <div className="two-column">
            <section className="panel">
              <div className="panel-heading">
                <h2>
                  {project.status === "failed"
                    ? "Обработка остановлена"
                    : project.status === "cancelled"
                      ? "Задание отменено"
                      : project.status === "completed"
                        ? "Материал готов"
                        : stageLabels[project.stage] || "Подготовка"}
                </h2>
                <span className="badge">{Math.round(project.progress)}%</span>
              </div>
              <div className="panel-body">
                <div className="progress large">
                  <i style={{ width: `${project.progress}%` }} />
                </div>
                <p className="hint">
                  {active
                    ? "Отдельный воркер продолжает обработку. Скорость зависит от модели и компьютера."
                    : "Исходник и монтаж доступны из редактора."}
                </p>
                <ErrorBanner error={project.error || ""} />
                <div className="toolbar">
                  {project.source_available && (
                    <button
                      className="button primary"
                      disabled={busy}
                      onClick={() => void run("editor")}
                    >
                      Открыть редактор
                      <ArrowUpRight size={14} />
                    </button>
                  )}
                  {project.clip_count > 0 && (
                    <Link
                      className="button secondary"
                      to={`/projects/${id}/results`}
                    >
                      Найденные моменты ({project.clip_count})
                    </Link>
                  )}
                  {active && (
                    <button
                      className="button ghost"
                      disabled={busy}
                      onClick={() => void run("cancel")}
                    >
                      <Square size={12} />
                      Отменить
                    </button>
                  )}
                  {project.status === "failed" && (
                    <button
                      className="button secondary"
                      disabled={busy}
                      onClick={() => void run("retry")}
                    >
                      <RefreshCw size={14} />
                      Повторить
                    </button>
                  )}
                </div>
              </div>
            </section>
            <section className="panel">
              <div className="panel-heading">
                <h2>Этапы</h2>
              </div>
              <div className="processing-steps">
                {stages.map((stage, i) => (
                  <div
                    className={`step ${i < index || project.status === "completed" ? "complete" : ""} ${stage === project.stage && active ? "current" : ""}`}
                    key={stage}
                  >
                    <span>
                      {i < index || project.status === "completed" ? (
                        <Check size={13} />
                      ) : (
                        i + 1
                      )}
                    </span>
                    <strong>{stageLabels[stage]}</strong>
                  </div>
                ))}
              </div>
            </section>
          </div>
        )
      )}
    </>
  );
}
export function Results() {
  const { id } = useParams(),
    {
      data: project,
      error,
      loading,
      refresh,
    } = useResource<Project>(`/projects/${id}?compact=true`, 5000);
  const [selected, setSelected] = useState<string[]>([]),
    [actionError, setActionError] = useState(""),
    [busy, setBusy] = useState("");
  const open = useOpenEditor();
  const clips = project?.clips || [],
    ready = clips.filter((clip) => clip.status === "ready"),
    chosen = selected.filter((value) =>
      ready.some((clip) => clip.id === value),
    );
  const remove = async (clip: Clip) => {
    if (!window.confirm(`Удалить клип «${clip.title}»?`)) return;
    setBusy(clip.id);
    try {
      await api(`/clips/${clip.id}`, { method: "DELETE" });
      setSelected((value) => value.filter((item) => item !== clip.id));
      await refresh();
    } catch (failure) {
      setActionError(message(failure));
    } finally {
      setBusy("");
    }
  };
  return (
    <>
      <Link className="back-link" to="/">
        <ArrowLeft size={14} />
        Проекты
      </Link>
      <PageHeader
        title={project?.title || "Найденные моменты"}
        description="Откройте заготовку в редакторе, уточните монтаж и экспортируйте результат."
      >
        {project?.source_available && (
          <button
            className="button secondary"
            onClick={() =>
              void open(String(id)).catch((failure) =>
                setActionError(message(failure)),
              )
            }
          >
            Редактор исходника
            <ArrowUpRight size={14} />
          </button>
        )}
        {ready.length > 0 && (
          <a
            className="button primary"
            href={`/api/projects/${id}/download`}
            download
          >
            <Download size={15} />
            Все MP4 ({ready.length})
          </a>
        )}
      </PageHeader>
      <ErrorBanner
        error={error || actionError || project?.error || ""}
        retry={() => void refresh()}
      />
      {project?.analysis_provider === "heuristic" && (
        <div className="notice">
          Эвристический отбор: смысл моделью не оценивался. Проверьте найденные
          фрагменты.
        </div>
      )}
      {chosen.length > 0 && (
        <div className="selection-toolbar">
          <span>Выбрано {chosen.length}</span>
          <a
            className="button secondary"
            href={`/api/projects/${id}/download?ids=${chosen.join(",")}`}
            download
          >
            <Download size={14} />
            Скачать выбранные
          </a>
        </div>
      )}
      {loading ? (
        <Skeleton />
      ) : clips.length ? (
        <div className="clip-list">
          {clips.map((clip, index) => (
            <article className="clip-card" key={clip.id}>
              <div className="clip-player">
                {clip.status === "ready" ? (
                  <video
                    src={
                      clip.preview_url ||
                      `/media/clips/${clip.id}?v=${clip.revision}`
                    }
                    controls
                    playsInline
                    preload="none"
                  />
                ) : (
                  <div className="clip-draft">
                    <Film size={26} />
                    <span>Монтажная заготовка</span>
                  </div>
                )}
                {clip.status === "ready" && (
                  <label className="clip-checkbox">
                    <input
                      type="checkbox"
                      checked={selected.includes(clip.id)}
                      aria-label={`Выбрать ${clip.title}`}
                      onChange={(event) =>
                        setSelected((values) =>
                          event.target.checked
                            ? [...values, clip.id]
                            : values.filter((value) => value !== clip.id),
                        )
                      }
                    />
                  </label>
                )}
              </div>
              <div className="clip-info">
                <div className="row">
                  <span className="muted">Момент {index + 1}</span>
                  <span className="badge">{clip.score}/100</span>
                </div>
                <h3>{clip.title}</h3>
                <p className="timecode">
                  {time(clip.start)} — {time(clip.end)} ·{" "}
                  {Math.round(clip.duration || clip.end - clip.start)}с
                </p>
                <p className="muted">{clip.reason}</p>
                <ErrorBanner error={clip.error || ""} />
                <div className="clip-actions">
                  <Link
                    className="button primary"
                    to={`/clips/${clip.id}/edit`}
                  >
                    Редактор
                    <ArrowUpRight size={14} />
                  </Link>
                  {clip.status === "ready" && (
                    <a
                      className="icon-button"
                      href={
                        clip.download_url || `/api/clips/${clip.id}/download`
                      }
                      download
                      aria-label={`Скачать ${clip.title}`}
                    >
                      <Download size={15} />
                    </a>
                  )}
                  <button
                    className="icon-button"
                    disabled={busy === clip.id}
                    onClick={() => void remove(clip)}
                    aria-label={`Удалить ${clip.title}`}
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <Empty title="Пока нет монтажных заготовок">
          <p>Можно открыть исходник и собрать монтаж в редакторе.</p>
        </Empty>
      )}
    </>
  );
}
