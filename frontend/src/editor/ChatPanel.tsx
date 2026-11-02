import { useEffect, useRef, useState } from "react";
import {
  ArrowUp,
  Check,
  Eye,
  LoaderCircle,
  Square,
  Wand2,
  X,
} from "lucide-react";
import type { EditorSession, Proposal } from "../types/editor";
import { ErrorBanner } from "../ui";
const actionNames: Record<string, string> = {
  trim: "Границы",
  cut: "Вырезать",
  remove: "Удалить участок",
  split: "Разделить",
  move: "Переместить",
  speed: "Скорость",
  zoom: "Приближение",
  crop: "Кадрирование",
  resize: "Формат",
  reframe: "Перекадрирование",
  layout: "Раскладка",
  face_tracking: "Кадрирование по лицу",
  subtitles: "Субтитры",
  subtitle_style: "Оформление субтитров",
  text_overlay: "Текст поверх видео",
  normalize_audio: "Нормализация звука",
  volume: "Громкость",
  transition: "Переход",
  remove_silences: "Удаление пауз",
  create_hook: "Создание хука",
  replace_segments: "Новый монтаж",
  find_moments: "Поиск по речи",
  auto_highlights: "Лучшие моменты",
  tiktok: "TikTok-версия",
};
export function ChatPanel({
  session,
  thinking,
  error,
  onPrompt,
  onCancel,
  onPreview,
  onDecision,
  previewId,
  disabled,
}: {
  session: EditorSession;
  thinking: boolean;
  error: string;
  onPrompt: (prompt: string) => Promise<boolean>;
  onCancel: () => void;
  onPreview: (proposal: Proposal | null) => void;
  onDecision: (proposal: Proposal, action: "apply" | "cancel") => void;
  previewId: string | null;
  disabled: boolean;
}) {
  const [prompt, setPrompt] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [session.messages.length, session.proposals.length, thinking]);
  const send = async (value: string) => {
    if (!value.trim() || thinking || disabled) return;
    const success = await onPrompt(value.trim());
    if (success) setPrompt("");
  };
  const pending = session.proposals.filter(
    (proposal) => proposal.status === "pending",
  );
  return (
    <>
      <div className="chat-thread">
        <div className="chat-intro">
          <Wand2 size={21} />
          <h3>Монтаж словами</h3>
          <p>
            Опишите результат. AI предложит конкретные правки — вы посмотрите их
            и примените.
          </p>
        </div>
        {session.messages.map((entry, index) => (
          <article
            className={`chat-message chat-${entry.role}`}
            key={entry.id ?? `${entry.created_at}-${index}`}
          >
            <div className="chat-meta">
              {entry.role === "user"
                ? "Вы"
                : entry.role === "system"
                  ? "Редактор"
                  : "ClipForge AI"}
              {entry.status === "cancelled" && <span> · Отменено</span>}
            </div>
            <div className="chat-bubble">
              {entry.content || entry.text || ""}
            </div>
          </article>
        ))}
        {pending.map((proposal) => (
          <article
            className={`proposal-card ${previewId === proposal.id ? "previewing" : ""}`}
            key={proposal.id}
          >
            <div className="row">
              <Wand2 size={14} />
              <strong>Предложение AI</strong>
              <span>{proposal.actions.length} действий</span>
            </div>
            <p>{proposal.summary}</p>
            <details>
              <summary>Состав изменений</summary>
              <ul className="action-list">
                {proposal.actions.map((action, index) => (
                  <li key={index}>{actionNames[action.type] || action.type}</li>
                ))}
              </ul>
            </details>
            {proposal.base_revision !== session.revision && (
              <p className="hint">
                Монтаж изменился после этого предложения. Создайте новый запрос
                или отмените предложение.
              </p>
            )}
            <div className="proposal-actions">
              <button
                className={`button secondary ${previewId === proposal.id ? "active" : ""}`}
                onClick={() =>
                  onPreview(previewId === proposal.id ? null : proposal)
                }
              >
                <Eye size={13} />
                {previewId === proposal.id ? "Вернуть монтаж" : "Preview"}
              </button>
              <button
                className="button primary"
                disabled={
                  disabled || proposal.base_revision !== session.revision
                }
                onClick={() => onDecision(proposal, "apply")}
              >
                <Check size={13} />
                Применить
              </button>
              <button
                className="icon-button"
                disabled={disabled}
                aria-label="Отменить предложение"
                onClick={() => onDecision(proposal, "cancel")}
              >
                <X size={14} />
              </button>
            </div>
          </article>
        ))}
        {thinking && (
          <div className="chat-message chat-assistant">
            <div className="chat-bubble">
              <LoaderCircle className="spinner" size={15} />
              Разбираю команду и контекст…
            </div>
            <button className="button ghost" onClick={onCancel}>
              <Square size={11} />
              Отменить запрос
            </button>
          </div>
        )}
        <ErrorBanner error={error} />
        <div ref={bottom} />
      </div>
      <div className="chat-composer">
        <div className="chat-quick-actions">
          {[
            "Убери длинные паузы",
            "Добавь крупные субтитры",
            "Сделай максимум 45 секунд",
          ].map((value) => (
            <button
              key={value}
              disabled={thinking || disabled}
              onClick={() => void send(value)}
            >
              {value}
            </button>
          ))}
        </div>
        <form
          className="chat-input"
          onSubmit={(event) => {
            event.preventDefault();
            void send(prompt);
          }}
        >
          <textarea
            value={prompt}
            maxLength={4000}
            onChange={(event) => setPrompt(event.target.value)}
            placeholder="Ask AI to edit this video…"
            aria-label="Команда AI для монтажа"
            rows={3}
            disabled={disabled}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void send(prompt);
              }
            }}
          />
          <button
            className="icon-button"
            type="submit"
            aria-label="Отправить AI-команду"
            disabled={!prompt.trim() || thinking || disabled}
          >
            <ArrowUp size={17} />
          </button>
        </form>
        <p className="hint">Enter — отправить · Shift+Enter — новая строка</p>
      </div>
    </>
  );
}
