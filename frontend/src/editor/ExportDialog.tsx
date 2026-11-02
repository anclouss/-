import { useEffect, useState } from "react";
import {
  Download,
  RefreshCw,
  SlidersHorizontal,
  Square,
  X,
} from "lucide-react";
import { api, message, stageLabels, time } from "../api";
import type { EditorJob, EditorSession, ExportOptions } from "../types/editor";
import { duration } from "../types/editor";
import { ErrorBanner } from "../ui";
export function ExportDialog({
  session,
  onClose,
  onExport,
  onJobs,
}: {
  session: EditorSession;
  onClose: () => void;
  onExport: (options: ExportOptions) => Promise<void>;
  onJobs: () => void;
}) {
  const [options, setOptions] = useState<ExportOptions>({
    platform: "tiktok",
    resolution: "1080p",
    fps: "30",
    preset: "balanced",
    codec: "h264",
    subtitles: session.state.settings.subtitles,
    watermark: "",
  });
  const [advanced, setAdvanced] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const exports = session.exports.filter((job) => job.kind === "export");
  const length = duration(session.state),
    can4k =
      Math.max(session.source.width, session.source.height) >= 3840 &&
      Math.min(session.source.width, session.source.height) >= 2160;
  const update = (values: Partial<ExportOptions>) =>
    setOptions((current) => ({ ...current, ...values }));
  const selectPlatform = (platform: ExportOptions["platform"]) =>
    update({
      platform,
      resolution: "1080p",
      fps: "30",
      codec: "h264",
      subtitles: true,
      preset: "balanced",
    });
  const run = async () => {
    setBusy(true);
    setError("");
    try {
      await onExport(options);
      onJobs();
    } catch (failure) {
      setError(message(failure));
    } finally {
      setBusy(false);
    }
  };
  const jobAction = async (job: EditorJob, action: "cancel" | "retry") => {
    setError("");
    try {
      await api(`/editor-jobs/${job.id}/${action}`, { method: "POST" });
      onJobs();
    } catch (failure) {
      setError(message(failure));
    }
  };
  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  }, [onClose]);
  const estimated = options.max_bitrate_mbps
    ? (length * (options.max_bitrate_mbps + 0.16)) / 8
    : null;
  return (
    <div
      className="export-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="export-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="export-title"
      >
        <header className="dialog-header">
          <div>
            <h2 id="export-title">Экспорт видео</h2>
            <p className="muted">
              {time(length)} · текущая версия {session.revision}
            </p>
          </div>
          <button
            className="icon-button"
            onClick={onClose}
            aria-label="Закрыть экспорт"
          >
            <X size={18} />
          </button>
        </header>
        <div className="dialog-body">
          <p className="hint">
            TikTok, Shorts и Reels экспортируют 9:16. Custom сохраняет формат
            текущего монтажа.
          </p>
          <div className="export-presets">
            {(["tiktok", "shorts", "reels", "custom"] as const).map((value) => (
              <button
                className={`export-option ${options.platform === value ? "selected" : ""}`}
                onClick={() => selectPlatform(value)}
                key={value}
              >
                {
                  {
                    tiktok: "TikTok",
                    shorts: "YouTube Shorts",
                    reels: "Instagram Reels",
                    custom: "Custom",
                  }[value]
                }
              </button>
            ))}
          </div>
          <fieldset disabled={busy} className="export-fieldset">
            <div className="form-grid">
              <label className="field">
                Разрешение
                <select
                  className="select"
                  value={options.resolution}
                  onChange={(event) =>
                    update({
                      resolution: event.target
                        .value as ExportOptions["resolution"],
                    })
                  }
                >
                  <option value="720p">720p</option>
                  <option value="1080p">1080p</option>
                  <option value="1440p">1440p</option>
                  <option value="4k" disabled={!can4k}>
                    4K{!can4k ? " · нужен 4K исходник" : ""}
                  </option>
                </select>
              </label>
              <label className="field">
                Частота кадров
                <select
                  className="select"
                  value={options.fps}
                  onChange={(event) =>
                    update({ fps: event.target.value as ExportOptions["fps"] })
                  }
                >
                  <option value="original">
                    Оригинал ({Math.round(session.source.fps || 30)} FPS)
                  </option>
                  <option value="30">30 FPS</option>
                  <option value="60">60 FPS</option>
                </select>
              </label>
            </div>
            <label className="field">
              Качество
              <select
                className="select"
                value={options.preset}
                onChange={(event) =>
                  update({
                    preset: event.target.value as ExportOptions["preset"],
                  })
                }
              >
                <option value="fast">Fast · быстрее</option>
                <option value="balanced">Balanced · баланс</option>
                <option value="high">High Quality</option>
                <option value="maximum">Maximum</option>
              </select>
            </label>
            <div className="form-grid">
              <label className="check">
                <input
                  type="checkbox"
                  checked={options.subtitles}
                  onChange={(event) =>
                    update({ subtitles: event.target.checked })
                  }
                />
                Вшить субтитры
              </label>
              <label className="check">
                <input
                  type="checkbox"
                  checked={Boolean(options.watermark)}
                  onChange={(event) =>
                    update({
                      watermark: event.target.checked ? "ClipForge" : "",
                    })
                  }
                />
                Водяной знак
              </label>
            </div>
            {options.watermark && (
              <label className="field">
                Текст водяного знака
                <input
                  className="input"
                  maxLength={80}
                  value={options.watermark}
                  onChange={(event) =>
                    update({ watermark: event.target.value })
                  }
                />
              </label>
            )}
            <button
              className="button ghost"
              onClick={() => setAdvanced(!advanced)}
            >
              <SlidersHorizontal size={14} />
              Advanced settings
            </button>
            {advanced && (
              <div className="advanced-settings">
                <div className="form-grid">
                  <label className="field">
                    Кодек
                    <select
                      className="select"
                      value={options.codec}
                      onChange={(event) =>
                        update({
                          codec: event.target.value as ExportOptions["codec"],
                        })
                      }
                    >
                      <option value="h264">H.264 · совместимый</option>
                      <option value="h265">H.265 · компактный</option>
                    </select>
                  </label>
                  <label className="field">
                    Макс. битрейт, Мбит/с
                    <input
                      className="input"
                      type="number"
                      min={0.5}
                      max={100}
                      step={0.5}
                      placeholder="Автоматически"
                      value={options.max_bitrate_mbps ?? ""}
                      onChange={(event) =>
                        update({
                          max_bitrate_mbps: event.target.value
                            ? Number(event.target.value)
                            : undefined,
                        })
                      }
                    />
                  </label>
                  <label className="field">
                    CRF
                    <input
                      className="input"
                      type="number"
                      min={14}
                      max={35}
                      placeholder="Из preset"
                      value={options.crf ?? ""}
                      onChange={(event) =>
                        update({
                          crf: event.target.value
                            ? Number(event.target.value)
                            : undefined,
                        })
                      }
                    />
                  </label>
                </div>
                <p className="hint">
                  Более низкий CRF обычно повышает качество и размер файла.
                </p>
              </div>
            )}
            <div className="export-summary">
              <span>
                {options.resolution} ·{" "}
                {options.fps === "original" ? "Original" : options.fps} FPS ·{" "}
                {options.codec.toUpperCase()}
              </span>
              <strong>
                {estimated
                  ? `До ≈ ${estimated.toFixed(1)} МБ`
                  : "Размер зависит от содержания"}
              </strong>
              <p className="hint">
                {estimated
                  ? "Оценка по верхнему битрейту и длительности, фактический файл может быть меньше."
                  : "При переменном качестве точный размер заранее неизвестен."}
              </p>
            </div>
          </fieldset>
          <ErrorBanner error={error} />
          {exports.length > 0 && (
            <section className="export-history">
              <h3>Экспорты проекта</h3>
              {exports.map((job) => (
                <div className="export-job" key={job.id}>
                  <div className="row">
                    <strong>
                      {job.status === "done"
                        ? "Готово"
                        : job.status === "failed"
                          ? "Ошибка"
                          : job.status === "cancelled"
                            ? "Отменён"
                            : stageLabels[job.stage] || "В очереди"}
                    </strong>
                    <span className="muted">Версия {job.revision ?? "—"}</span>
                    {job.status === "done" && (
                      <a
                        className="icon-button"
                        href={
                          job.download_url ||
                          `/api/editor-jobs/${job.id}/download`
                        }
                        download
                        aria-label="Скачать экспорт"
                      >
                        <Download size={15} />
                      </a>
                    )}
                    {["queued", "running"].includes(job.status) && (
                      <button
                        className="icon-button"
                        aria-label="Отменить экспорт"
                        onClick={() => void jobAction(job, "cancel")}
                      >
                        <Square size={12} />
                      </button>
                    )}
                    {["failed", "cancelled"].includes(job.status) && (
                      <button
                        className="icon-button"
                        aria-label="Повторить экспорт"
                        onClick={() => void jobAction(job, "retry")}
                      >
                        <RefreshCw size={14} />
                      </button>
                    )}
                  </div>
                  {["queued", "running"].includes(job.status) && (
                    <>
                      <div className="progress job-progress">
                        <i style={{ width: `${job.progress}%` }} />
                      </div>
                      <span className="job-stage">
                        {Math.round(job.progress)}%
                      </span>
                    </>
                  )}
                  {job.error && <p className="error-text">{job.error}</p>}
                </div>
              ))}
            </section>
          )}
        </div>
        <footer className="dialog-footer">
          <p>Рендер продолжится после закрытия страницы.</p>
          <button
            className="button primary"
            disabled={busy || !session.source.available}
            onClick={() => void run()}
          >
            {busy ? "Подготовка…" : "Начать экспорт"}
            <Download size={15} />
          </button>
        </footer>
      </section>
    </div>
  );
}
