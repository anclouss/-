import {
  Clapperboard,
  Film,
  FolderOpen,
  Plus,
  Trash2,
  ArrowUpRight,
  Square,
} from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { useState } from "react";
import { api, message, stageLabels, time } from "../api";
import type { Clip, Project } from "../api";
import { useResource } from "../hooks/useResource";
import { Empty, ErrorBanner, PageHeader, Skeleton } from "../ui";
export function Dashboard() {
  const { data, loading, error, refresh } = useResource<Project[]>(
    "/projects",
    8000,
  );
  const [actionError, setActionError] = useState(""),
    [busy, setBusy] = useState("");
  const navigate = useNavigate();
  const projects = data || [],
    processing = projects.filter((item) =>
      ["queued", "processing"].includes(item.status),
    );
  const open = async (project: Project) => {
    setBusy(project.id);
    setActionError("");
    try {
      const clip = await api<Clip>(`/projects/${project.id}/editor`, {
        method: "POST",
      });
      navigate(`/clips/${clip.id}/edit`);
    } catch (failure) {
      setActionError(message(failure));
    } finally {
      setBusy("");
    }
  };
  const remove = async (project: Project) => {
    if (!window.confirm(`Удалить «${project.title}» и файлы проекта?`)) return;
    setBusy(project.id);
    setActionError("");
    try {
      await api(`/projects/${project.id}`, { method: "DELETE" });
      await refresh();
    } catch (failure) {
      setActionError(message(failure));
    } finally {
      setBusy("");
    }
  };
  const cancel = async (project: Project) => {
    setBusy(project.id);
    setActionError("");
    try {
      await api(`/projects/${project.id}/cancel`, { method: "POST" });
      await refresh();
    } catch (failure) {
      setActionError(message(failure));
    } finally {
      setBusy("");
    }
  };
  return (
    <>
      <PageHeader
        title="Рабочее пространство"
        description="Исходники, автоматические нарезки и ваш монтаж."
      >
        <Link className="button primary" to="/upload">
          <Plus size={16} />
          Новый проект
        </Link>
      </PageHeader>
      <div className="stats-strip">
        <span>
          <FolderOpen size={16} />
          <strong>{projects.length}</strong> проектов
        </span>
        <span>
          <Clapperboard size={16} />
          <strong>
            {projects.reduce((sum, item) => sum + item.clip_count, 0)}
          </strong>{" "}
          клипов
        </span>
        <span>
          <span className="status-dot" />
          <strong>{processing.length}</strong> в обработке
        </span>
      </div>
      <ErrorBanner error={actionError || error} retry={() => void refresh()} />
      <div className="section-heading">
        <h2>Проекты</h2>
        <span className="muted">Локальное пространство</span>
      </div>
      {loading ? (
        <Skeleton rows={5} />
      ) : projects.length ? (
        <div className="table-list">
          {projects.map((project) => {
            const active = ["queued", "processing"].includes(project.status);
            return (
              <article className="project-row" key={project.id}>
                <div className="project-thumb">
                  <Film size={22} />
                </div>
                <div className="project-name">
                  <Link
                    to={`/projects/${project.id}/${active ? "processing" : "results"}`}
                  >
                    {project.title}
                  </Link>
                  <span>
                    {time(project.duration || 0)} ·{" "}
                    {new Date(project.created_at).toLocaleDateString("ru-RU")} ·{" "}
                    {project.clip_count} клипов
                  </span>
                </div>
                <div className="project-status">
                  <span className={`badge ${project.status}`}>
                    {active
                      ? stageLabels[project.stage] || "В очереди"
                      : project.status === "completed"
                        ? "Готово"
                        : project.status === "cancelled"
                          ? "Отменён"
                          : "Ошибка"}
                  </span>
                  {active && (
                    <div className="progress">
                      <i style={{ width: `${project.progress}%` }} />
                    </div>
                  )}
                </div>
                <div className="row">
                  {project.source_available && (
                    <button
                      className="button secondary"
                      disabled={busy === project.id}
                      onClick={() => void open(project)}
                    >
                      Редактор
                      <ArrowUpRight size={14} />
                    </button>
                  )}
                  {active ? (
                    <button
                      className="icon-button"
                      aria-label={`Отменить ${project.title}`}
                      disabled={busy === project.id}
                      onClick={() => void cancel(project)}
                    >
                      <Square size={14} />
                    </button>
                  ) : (
                    <button
                      className="icon-button"
                      aria-label={`Удалить ${project.title}`}
                      disabled={busy === project.id}
                      onClick={() => void remove(project)}
                    >
                      <Trash2 size={15} />
                    </button>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <Empty title="Начните с исходного видео">
          <p>Соберите монтаж вручную или попросите AI найти лучшие моменты.</p>
          <Link className="button primary" to="/upload">
            <Plus size={16} />
            Загрузить видео
          </Link>
        </Empty>
      )}
    </>
  );
}
