import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowLeft,
  Captions,
  Check,
  ChevronDown,
  Download,
  Eye,
  Focus,
  FolderOpen,
  LoaderCircle,
  RotateCcw,
  Scissors,
  Sparkles,
  Square,
  Undo2,
  Redo2,
  Wand2,
  X,
} from "lucide-react";
import { aborted, api, message } from "../api";
import type { Word } from "../api";
import { useEditor } from "../hooks/useEditor";
import { useJob } from "../hooks/useJob";
import type {
  EditingAction,
  EditorJob,
  EditorSession,
  ExportOptions,
  PreviewQuality,
  Proposal,
  Selection,
} from "../types/editor";
import { duration, segmentPositions } from "../types/editor";
import { ErrorBanner, Skeleton } from "../ui";
import { AssetsPanel } from "./AssetsPanel";
import { VideoPreview } from "./VideoPreview";
import { PropertiesPanel } from "./PropertiesPanel";
import { Timeline } from "./Timeline";
import { ChatPanel } from "./ChatPanel";
import { ExportDialog } from "./ExportDialog";

export function EditorPage() {
  const { id } = useParams(),
    editor = useEditor(id);
  const {
    setError: setEditorError,
    actions: applyActions,
    history: historyAction,
    flush,
    getCurrent,
    accept,
  } = editor;
  const [playhead, setPlayhead] = useState(0),
    [playing, setPlaying] = useState(false),
    [selection, setSelection] = useState<Selection | null>(null),
    [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState<"chat" | "properties">("chat"),
    [assetsOpen, setAssetsOpen] = useState(false),
    [exportOpen, setExportOpen] = useState(false),
    [quality, setQuality] = useState<PreviewQuality>("auto");
  const [previewProposal, setPreviewProposal] = useState<Proposal | null>(null),
    [thinking, setThinking] = useState(false),
    [chatError, setChatError] = useState(""),
    [notice, setNotice] = useState("");
  const [proxyJob, setProxyJob] = useJob(),
    [previewJob, setPreviewJob] = useJob(),
    [baked, setBaked] = useState(false),
    [bakeSignature, setBakeSignature] = useState(""),
    [captionWords, setCaptionWords] = useState<Word[]>([]);
  const [proxyUrls, setProxyUrls] = useState<
      Partial<Record<PreviewQuality, string>>
    >({}),
    [proxyRequestQuality, setProxyRequestQuality] =
      useState<PreviewQuality>("auto");
  const chatController = useRef<AbortController | null>(null),
    chatId = useRef<string | null>(null),
    head = useRef(playhead),
    selectionRef = useRef(selection),
    alive = useRef(true);
  head.current = playhead;
  selectionRef.current = selection;
  const session = editor.session,
    document = previewProposal?.state || editor.document;
  const total = document ? duration(document) : 0,
    signature = document ? JSON.stringify(document) : "";
  const available = Boolean(session?.source.available),
    disabled = editor.working || editor.conflict || !available;
  const stopChat = useCallback(() => {
    chatController.current?.abort();
    const requestId = chatId.current;
    chatId.current = null;
    if (alive.current) {
      setThinking(false);
      setChatError("Запрос отменён. Изменения не применены.");
    }
    if (requestId)
      void api(`/editor/${id}/chat/cancel`, {
        method: "POST",
        body: JSON.stringify({ request_id: requestId }),
      }).catch(() => undefined);
  }, [id]);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      chatController.current?.abort();
      const requestId = chatId.current;
      if (requestId)
        void api(`/editor/${id}/chat/cancel`, {
          method: "POST",
          body: JSON.stringify({ request_id: requestId }),
        }).catch(() => undefined);
    };
  }, [id]);
  useEffect(() => {
    setPreviewProposal(null);
    setSelectedId(null);
    setPlayhead(0);
    setPlaying(false);
    setSelection(null);
    setProxyUrls({});
    setBaked(false);
    setCaptionWords([]);
  }, [id]);
  useEffect(() => {
    if (playhead > total) setPlayhead(total);
  }, [playhead, total]);
  useEffect(() => {
    if (signature !== bakeSignature) setBaked(false);
  }, [signature, bakeSignature]);
  useEffect(() => {
    if (proxyJob?.status === "done" && proxyJob.url)
      setProxyUrls((current) => ({
        ...current,
        [proxyRequestQuality]: proxyJob.url || "",
      }));
  }, [proxyJob?.status, proxyJob?.url, proxyRequestQuality]);
  useEffect(() => {
    if (
      previewJob?.status === "done" &&
      previewJob.url &&
      signature === bakeSignature
    ) {
      setBaked(true);
      setNotice("Обработанный preview готов.");
    }
    if (previewJob?.status === "failed")
      setEditorError(previewJob.error || "Не удалось создать preview.");
  }, [
    previewJob?.status,
    previewJob?.url,
    previewJob?.error,
    signature,
    bakeSignature,
    setEditorError,
  ]);
  const seek = useCallback((value: number) => setPlayhead(value), []);
  const command = useCallback(
    (actions: EditingAction[], label: string) => {
      setPreviewProposal(null);
      setBaked(false);
      setPlaying(false);
      setNotice("");
      void applyActions(actions, label, {
        playhead: head.current,
        selection: selectionRef.current || undefined,
      })
        .then(() => setSelection(null))
        .catch(() => undefined);
    },
    [applyActions],
  );
  const history = useCallback(
    (action: "undo" | "redo" | "reset") => {
      setPreviewProposal(null);
      setPlaying(false);
      setBaked(false);
      void historyAction(action).catch(() => undefined);
    },
    [historyAction],
  );
  const prompt = useCallback(
    async (text: string) => {
      if (thinking) return false;
      setChatError("");
      setThinking(true);
      setTab("chat");
      const controller = new AbortController(),
        requestId = crypto.randomUUID();
      chatController.current = controller;
      chatId.current = requestId;
      try {
        await flush();
        const latest = getCurrent();
        if (!latest) return false;
        const result = await api<EditorSession>(`/editor/${id}/chat`, {
          method: "POST",
          body: JSON.stringify({
            revision: latest.revision,
            prompt: text,
            request_id: requestId,
            context: {
              playhead: head.current,
              selection: selectionRef.current || undefined,
            },
          }),
          signal: controller.signal,
        });
        if (
          !alive.current ||
          controller.signal.aborted ||
          chatId.current !== requestId
        )
          return false;
        accept(result, false);
        return true;
      } catch (failure) {
        if (alive.current && !aborted(failure)) setChatError(message(failure));
        return false;
      } finally {
        if (chatId.current === requestId) {
          chatId.current = null;
          chatController.current = null;
          if (alive.current) setThinking(false);
        }
      }
    },
    [thinking, flush, getCurrent, accept, id],
  );
  const decide = (proposal: Proposal, action: "apply" | "cancel") => {
    setPlaying(false);
    void editor
      .decision(proposal, action)
      .then(() => {
        setPreviewProposal(null);
        setBaked(false);
        setNotice(
          action === "apply"
            ? "AI-правки применены и сохранены."
            : "Предложение отменено.",
        );
      })
      .catch(() => undefined);
  };
  const preview = (proposal: Proposal | null) => {
    setPlaying(false);
    setBaked(false);
    setPreviewProposal(proposal);
    setNotice(
      proposal
        ? "Предпросмотр AI-правок. Примените предложение, чтобы сохранить этот монтаж."
        : "",
    );
  };
  const makeProxy = async (next: PreviewQuality) => {
    setQuality(next);
    setBaked(false);
    setEditorError("");
    if (proxyUrls[next]) return;
    try {
      if (proxyJob && ["queued", "running"].includes(proxyJob.status))
        await api(`/editor-jobs/${proxyJob.id}/cancel`, { method: "POST" });
      const job = await api<EditorJob>(`/editor/${id}/proxy`, {
        method: "POST",
        body: JSON.stringify({ quality: next }),
      });
      setProxyRequestQuality(next);
      setProxyJob(job);
      setNotice(
        "Proxy создаётся в фоне. До готовности воспроизводится исходник.",
      );
    } catch (failure) {
      setEditorError(message(failure));
    }
  };
  const makePreview = async () => {
    setPlaying(false);
    setEditorError("");
    try {
      await flush();
      const latest = getCurrent();
      if (!latest) return;
      const job = await api<EditorJob>(`/editor/${id}/preview`, {
        method: "POST",
        body: JSON.stringify({
          revision: latest.revision,
          proposal_id: previewProposal?.id,
          quality,
        }),
      });
      setBakeSignature(JSON.stringify(previewProposal?.state || latest.state));
      setPreviewJob(job);
      setNotice(
        "Обработанный preview поставлен в очередь. Можно продолжать работу.",
      );
    } catch (failure) {
      setEditorError(message(failure));
    }
  };
  const exportVideo = async (options: ExportOptions) => {
    await flush();
    const latest = getCurrent();
    if (!latest) throw new Error("Редактор ещё загружается.");
    await api<EditorJob>(`/editor/${id}/exports`, {
      method: "POST",
      body: JSON.stringify({ revision: latest.revision, ...options }),
    });
    await editor.refresh();
  };
  const findHighlights = async () => {
    try {
      await flush();
      await api(`/projects/${session?.project.id}/highlights`, {
        method: "POST",
      });
      await editor.refresh();
      setNotice(
        "Ищем интересные моменты в фоне. Клипы появятся во вкладке «Клипы».",
      );
    } catch (failure) {
      setEditorError(message(failure));
    }
  };
  const sourceSeek = (value: number) => {
    if (!document) return;
    const found = segmentPositions(document).find(
      ({ segment }) => value >= segment.start && value <= segment.end,
    );
    if (found) {
      setPlayhead(
        found.offset + (value - found.segment.start) / found.segment.speed,
      );
      setAssetsOpen(false);
    } else
      setNotice(
        "Эта фраза находится вне текущего монтажа. Попросите AI найти её и добавить в ролик.",
      );
  };
  const wordEdit = (word: Word, text: string) => {
    editor.change(
      (state) => ({
        ...state,
        caption_overrides: {
          ...state.caption_overrides,
          [String(word.start)]: text,
        },
      }),
      "Исправление субтитров",
    );
  };
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (
        event.target instanceof HTMLElement &&
        (event.target.matches("input,textarea,select,button,a") ||
          event.target.closest('[role="dialog"]') ||
          event.target.isContentEditable)
      )
        return;
      if (event.code === "Space") {
        event.preventDefault();
        setPlaying((value) => !value);
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
        event.preventDefault();
        history(event.shiftKey ? "redo" : "undo");
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "y") {
        event.preventDefault();
        history("redo");
      }
      if (event.key === "Delete" && selection) {
        event.preventDefault();
        command([{ type: "remove", ...selection }], "Удаление участка");
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [command, history, selection]);
  if (editor.loading)
    return (
      <div className="editor-loading">
        <Skeleton rows={5} />
      </div>
    );
  if (!session || !document)
    return (
      <div className="editor-loading">
        <ErrorBanner
          error={editor.error || "Редактор не найден."}
          retry={() => void editor.reload()}
        />
        <Link className="button secondary" to="/">
          К проектам
        </Link>
      </div>
    );
  const pending = session.exports.filter(
    (job) =>
      job.kind === "export" && ["queued", "running"].includes(job.status),
  );
  const selected =
    document.segments.find((segment) => segment.id === selectedId) ||
    document.segments[0];
  const proxyUrl =
    proxyUrls[quality] ||
    (session.proxy.url &&
    (session.proxy.quality === quality || quality === "auto")
      ? session.proxy.url
      : null);
  const videoUrl =
    baked && previewJob?.url
      ? previewJob.url
      : proxyUrl || `/media/source/${session.project.id}`;
  const latestApplied =
    session.proposals
      .slice()
      .reverse()
      .find((item) => item.status === "applied") || null;
  return (
    <div className="editor-page">
      <header className="editor-header">
        <div className="editor-heading">
          <Link
            className="icon-button"
            to={`/projects/${session.project.id}/results`}
            aria-label="Назад к проекту"
          >
            <ArrowLeft size={17} />
          </Link>
          <div>
            <strong>{session.clip.title || session.project.title}</strong>
            <span className={`save-state ${editor.conflict ? "error" : ""}`}>
              {editor.saving ? (
                <>
                  <LoaderCircle className="spinner" size={11} />
                  Сохранение…
                </>
              ) : editor.conflict ? (
                "Конфликт версий"
              ) : editor.hasDraft ? (
                "Черновик · ожидает сохранения"
              ) : (
                <>
                  <Check size={11} />
                  Сохранено · v{session.revision}
                </>
              )}
            </span>
          </div>
        </div>
        <div className="editor-toolbar">
          <button
            className="icon-button assets-toggle"
            aria-label="Открыть файлы и транскрипт"
            onClick={() => setAssetsOpen(!assetsOpen)}
          >
            <FolderOpen size={17} />
          </button>
          <button
            className="icon-button"
            aria-label="Undo"
            title="Undo · Ctrl+Z"
            disabled={
              (!session.history.can_undo && !editor.hasDraft) ||
              editor.working ||
              !available ||
              Boolean(previewProposal)
            }
            onClick={() => history("undo")}
          >
            <Undo2 size={17} />
          </button>
          <button
            className="icon-button"
            aria-label="Redo"
            title="Redo · Ctrl+Shift+Z"
            disabled={
              !session.history.can_redo || disabled || Boolean(previewProposal)
            }
            onClick={() => history("redo")}
          >
            <Redo2 size={17} />
          </button>
          <button
            className="icon-button"
            aria-label="Reset"
            title="Вернуть исходный монтаж"
            disabled={editor.working || !available}
            onClick={() => {
              if (
                window.confirm(
                  "Вернуть начальный монтаж? Это действие можно отменить через Undo.",
                )
              )
                history("reset");
            }}
          >
            <RotateCcw size={15} />
          </button>
          <span className="toolbar-separator" />
          <label className="quality-select">
            <span>Preview</span>
            <select
              aria-label="Качество preview"
              value={quality}
              onChange={(event) =>
                void makeProxy(event.target.value as PreviewQuality)
              }
            >
              {(["auto", "360p", "480p", "720p", "1080p"] as const).map(
                (value) => (
                  <option key={value} value={value}>
                    {value === "auto" ? "Auto" : value}
                  </option>
                ),
              )}
            </select>
            <ChevronDown size={12} />
          </label>
          <button
            className="button secondary"
            disabled={
              disabled ||
              ["queued", "running"].includes(previewJob?.status || "")
            }
            title="Точное кадрирование, переходы и звук в лёгком рендере"
            onClick={() => void makePreview()}
          >
            <Eye size={14} />
            Preview changes
          </button>
          <button
            className="button primary"
            disabled={
              editor.working ||
              editor.conflict ||
              Boolean(previewProposal) ||
              (!available && !session.exports.length)
            }
            onClick={() => {
              void flush()
                .then(() => setExportOpen(true))
                .catch((failure) => setEditorError(message(failure)));
            }}
          >
            <Download size={15} />
            Export{pending.length > 0 ? ` (${pending.length})` : ""}
          </button>
        </div>
      </header>
      {(editor.error ||
        session.project.error ||
        notice ||
        !available ||
        editor.conflict ||
        proxyJob?.status === "running" ||
        proxyJob?.status === "queued") && (
        <div className="editor-notifications">
          <ErrorBanner error={editor.error} />
          {session.project.error && (
            <details className="notice">
              <summary>
                Подготовка материала остановлена. Ручной монтаж доступен.
              </summary>
              <ErrorBanner
                error={session.project.error}
                retry={() => {
                  void api(`/projects/${session.project.id}/retry`, {
                    method: "POST",
                  })
                    .then(() => editor.refresh())
                    .catch((failure) => setEditorError(message(failure)));
                }}
              />
            </details>
          )}
          {editor.conflict && (
            <button
              className="button secondary"
              onClick={() => {
                if (
                  window.confirm(
                    "Загрузить сохранённую версию? Несохранённые локальные изменения будут заменены.",
                  )
                )
                  void editor.reload();
              }}
            >
              Загрузить сохранённую версию
            </button>
          )}
          {!available && (
            <div className="notice">
              Исходник удалён. Сохранённые экспорты доступны, повторный монтаж
              недоступен.
            </div>
          )}
          {notice && (
            <div className="notice">
              <span>{notice}</span>
              <button
                className="icon-button"
                aria-label="Скрыть сообщение"
                onClick={() => setNotice("")}
              >
                <X size={12} />
              </button>
            </div>
          )}
          {proxyJob && ["queued", "running"].includes(proxyJob.status) && (
            <div className="notice">
              Proxy: {Math.round(proxyJob.progress)}%
              <button
                className="button ghost"
                onClick={() =>
                  void api(`/editor-jobs/${proxyJob.id}/cancel`, {
                    method: "POST",
                  })
                    .then(() => api<EditorJob>(`/editor-jobs/${proxyJob.id}`))
                    .then(setProxyJob)
                    .catch((failure) => setEditorError(message(failure)))
                }
              >
                <Square size={10} />
                Отменить
              </button>
            </div>
          )}
        </div>
      )}
      <div className="editor-grid">
        <AssetsPanel
          session={session}
          document={document}
          disabled={disabled || Boolean(previewProposal)}
          open={assetsOpen}
          onClose={() => setAssetsOpen(false)}
          onSeek={sourceSeek}
          onWord={wordEdit}
        />
        {assetsOpen && (
          <button
            className="assets-overlay"
            onClick={() => setAssetsOpen(false)}
            aria-label="Закрыть панель файлов"
          />
        )}
        <div className="editor-center">
          <div className="quick-actions">
            <button
              disabled={disabled || thinking}
              onClick={() =>
                void prompt(
                  "Убери скучные фрагменты и собери более динамичный монтаж.",
                )
              }
            >
              <Scissors size={13} />
              Auto Cut
            </button>
            <button
              disabled={
                disabled ||
                thinking ||
                ["queued", "processing"].includes(session.project.status)
              }
              onClick={() => void findHighlights()}
            >
              <Sparkles size={13} />
              Find Highlights
            </button>
            <button
              disabled={disabled || Boolean(previewProposal)}
              onClick={() =>
                command(
                  [{ type: "remove_silences", min_duration: 1.5 }],
                  "Удаление длинных пауз",
                )
              }
            >
              <Scissors size={13} />
              Remove Silences
            </button>
            <button
              disabled={disabled || Boolean(previewProposal)}
              onClick={() =>
                command(
                  [{ type: "subtitles", enabled: true, highlight: true }],
                  "Динамические субтитры",
                )
              }
            >
              <Captions size={13} />
              Add Captions
            </button>
            <button
              disabled={disabled || Boolean(previewProposal)}
              onClick={() =>
                command(
                  [
                    { type: "reframe", layout: "crop" },
                    { type: "face_tracking", enabled: true },
                  ],
                  "Автокадрирование",
                )
              }
            >
              <Focus size={13} />
              Auto Reframe
            </button>
            <button
              disabled={disabled || thinking}
              onClick={() => {
                if (selection)
                  command(
                    [{ type: "create_hook", ...selection }],
                    "Создание хука",
                  );
                else
                  void prompt(
                    "Найди лучший момент и поставь его в начало как короткий hook.",
                  );
              }}
            >
              <Wand2 size={13} />
              Create Hook
            </button>
            <button
              disabled={disabled || thinking}
              onClick={() =>
                void prompt(
                  "Сделай готовую TikTok-версию: убери паузы, добавь динамические субтитры, сделай понятное начало и вертикальный кадр.",
                )
              }
            >
              <Wand2 size={13} />
              Make TikTok
            </button>
          </div>
          <VideoPreview
            document={document}
            session={session}
            url={videoUrl}
            baked={baked}
            playhead={playhead}
            onPlayhead={seek}
            onError={setEditorError}
            playing={playing}
            setPlaying={setPlaying}
            onWords={setCaptionWords}
          />
        </div>
        <aside className="panel assistant-panel">
          <div className="assistant-tabs">
            <button
              className={tab === "chat" ? "active" : ""}
              onClick={() => setTab("chat")}
            >
              <Wand2 size={14} />
              AI Chat
            </button>
            <button
              className={tab === "properties" ? "active" : ""}
              onClick={() => setTab("properties")}
            >
              Свойства
            </button>
          </div>
          {tab === "chat" ? (
            <ChatPanel
              session={session}
              thinking={thinking}
              error={chatError}
              onPrompt={prompt}
              onCancel={stopChat}
              onPreview={preview}
              onDecision={decide}
              previewId={previewProposal?.id || null}
              disabled={disabled}
            />
          ) : (
            <PropertiesPanel
              document={document}
              selected={selected}
              onChange={editor.change}
              disabled={disabled || Boolean(previewProposal)}
            />
          )}
        </aside>
        <Timeline
          document={document}
          sourceDuration={session.source.duration}
          peaks={session.waveform.peaks || []}
          words={captionWords}
          playhead={playhead}
          selection={selection}
          selectedId={selected?.id || null}
          proposal={previewProposal || latestApplied}
          disabled={disabled || Boolean(previewProposal)}
          onSeek={seek}
          onSelection={setSelection}
          onSelected={setSelectedId}
          onChange={editor.change}
          onActions={command}
        />
      </div>
      {exportOpen && (
        <ExportDialog
          session={session}
          onClose={() => setExportOpen(false)}
          onExport={exportVideo}
          onJobs={() => void editor.refresh()}
        />
      )}
    </div>
  );
}
