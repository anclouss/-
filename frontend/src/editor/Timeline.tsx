import { useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Hand,
  MousePointer2,
  Scissors,
  Trash2,
  ZoomIn,
} from "lucide-react";
import type { Word } from "../api";
import { time } from "../api";
import type {
  DocumentState,
  EditingAction,
  Proposal,
  Selection,
} from "../types/editor";
import {
  duration,
  mappedWords,
  segmentPositions,
  sourceTime,
} from "../types/editor";
export function Timeline({
  document,
  sourceDuration,
  peaks,
  words,
  playhead,
  selection,
  selectedId,
  proposal,
  disabled,
  onSeek,
  onSelection,
  onSelected,
  onChange,
  onActions,
}: {
  document: DocumentState;
  sourceDuration: number;
  peaks: number[];
  words: Word[];
  playhead: number;
  selection: Selection | null;
  selectedId: string | null;
  proposal: Proposal | null;
  disabled: boolean;
  onSeek: (value: number) => void;
  onSelection: (value: Selection | null) => void;
  onSelected: (id: string) => void;
  onChange: (value: DocumentState, label?: string) => void;
  onActions: (actions: EditingAction[], label: string) => void;
}) {
  const [zoom, setZoom] = useState(1),
    [rangeMode, setRangeMode] = useState(false),
    [dragDocument, setDragDocument] = useState<DocumentState | null>(null),
    [width, setWidth] = useState(800);
  const scroll = useRef<HTMLDivElement>(null),
    inner = useRef<HTMLDivElement>(null),
    canvas = useRef<HTMLCanvasElement>(null);
  const rangeStart = useRef<number | null>(null),
    drag = useRef<{
      id: string;
      edge: "start" | "end";
      x: number;
      document: DocumentState;
    } | null>(null),
    localDrag = useRef<DocumentState | null>(null);
  const effective = dragDocument || document,
    total = duration(effective),
    positions = useMemo(() => segmentPositions(effective), [effective]);
  const captions = useMemo(() => {
    const mapped = mappedWords(effective, words);
    return mapped
      .filter((_, index) => index % 5 === 0)
      .map((word, index) => ({
        start: word.start,
        end:
          mapped[Math.min(index * 5 + 4, mapped.length - 1)]?.end || word.end,
        text: mapped
          .slice(index * 5, index * 5 + 5)
          .map((item) => item.text)
          .join(" "),
      }));
  }, [effective, words]);
  useEffect(() => {
    const element = scroll.current;
    if (!element) return;
    const observer = new ResizeObserver(() => setWidth(element.clientWidth));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const size = Math.min(2400, Math.max(200, Math.round(width * zoom)));
    element.width = size;
    element.height = 44;
    const context = element.getContext("2d");
    if (!context) return;
    context.clearRect(0, 0, size, 44);
    context.fillStyle = "#777db5";
    if (!peaks.length) return;
    for (let x = 0; x < size; x += 3) {
      const output = (x / size) * total,
        source = sourceTime(effective, output),
        index = Math.min(
          peaks.length - 1,
          Math.max(
            0,
            Math.floor((source / Math.max(1, sourceDuration)) * peaks.length),
          ),
        ),
        amplitude = Math.max(1, Math.min(20, (peaks[index] || 0) * 20));
      context.fillRect(x, 22 - amplitude, 2, amplitude * 2);
    }
  }, [effective, peaks, sourceDuration, total, width, zoom]);
  const point = (event: PointerEvent<HTMLElement>) => {
    const rect = inner.current?.getBoundingClientRect();
    return rect
      ? Math.max(
          0,
          Math.min(total, ((event.clientX - rect.left) / rect.width) * total),
        )
      : 0;
  };
  const down = (event: PointerEvent<HTMLElement>) => {
    if (event.button !== 0 || disabled) return;
    const value = point(event);
    event.currentTarget.setPointerCapture(event.pointerId);
    if (rangeMode || event.shiftKey) {
      rangeStart.current = value;
      onSelection({ start: value, end: value });
    } else {
      onSelection(null);
      onSeek(value);
    }
  };
  const move = (event: PointerEvent<HTMLElement>) => {
    if (drag.current) {
      const data = drag.current,
        original = data.document.segments.find(
          (segment) => segment.id === data.id,
        );
      if (!original || !inner.current) return;
      const delta =
        ((event.clientX - data.x) /
          inner.current.getBoundingClientRect().width) *
        duration(data.document) *
        original.speed;
      const next = {
        ...data.document,
        segments: data.document.segments.map((segment) =>
          segment.id !== data.id
            ? segment
            : {
                ...segment,
                [data.edge]:
                  data.edge === "start"
                    ? Math.max(
                        0,
                        Math.min(segment.end - 0.2, original.start + delta),
                      )
                    : Math.min(
                        sourceDuration,
                        Math.max(segment.start + 0.2, original.end + delta),
                      ),
              },
        ),
      };
      localDrag.current = next;
      setDragDocument(next);
      return;
    }
    if (rangeStart.current !== null) {
      const value = point(event);
      onSelection({
        start: Math.min(rangeStart.current, value),
        end: Math.max(rangeStart.current, value),
      });
    }
  };
  const up = () => {
    if (drag.current && localDrag.current)
      onChange(localDrag.current, "Границы фрагмента");
    drag.current = null;
    localDrag.current = null;
    setDragDocument(null);
    rangeStart.current = null;
  };
  const trim = (
    event: PointerEvent<HTMLSpanElement>,
    id: string,
    edge: "start" | "end",
  ) => {
    event.stopPropagation();
    if (disabled) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { id, edge, x: event.clientX, document };
    localDrag.current = document;
  };
  const index = document.segments.findIndex(
      (segment) => segment.id === selectedId,
    ),
    selected = positions.find((position) => position.segment.id === selectedId);
  const remove = () => {
    const target =
      selection ||
      (selected
        ? { start: selected.offset, end: selected.offset + selected.length }
        : null);
    if (target && target.end - target.start > 0.1)
      onActions([{ type: "remove", ...target }], "Удаление участка");
  };
  const tickCount = Math.min(
    100,
    Math.max(5, Math.round((width * zoom) / 100)),
  );
  const ticks = Array.from(
    { length: tickCount + 1 },
    (_, index) => (index / tickCount) * total,
  );
  return (
    <section className="panel timeline-panel">
      <div className="timeline-toolbar">
        <div className="row">
          <button
            className={`icon-button ${!rangeMode ? "active" : ""}`}
            aria-label="Перемещение playhead"
            title="Перемещение playhead"
            onClick={() => setRangeMode(false)}
          >
            <MousePointer2 size={15} />
          </button>
          <button
            className={`icon-button ${rangeMode ? "active" : ""}`}
            aria-label="Выделить диапазон"
            title="Выделить диапазон (также Shift + drag)"
            onClick={() => setRangeMode(true)}
          >
            <Hand size={15} />
          </button>
          <span className="toolbar-separator" />
          <button
            className="icon-button"
            disabled={disabled || playhead <= 0 || playhead >= total}
            aria-label="Разделить на playhead"
            title="Разделить на playhead"
            onClick={() =>
              onActions(
                [{ type: "split", at: playhead }],
                "Разделение фрагмента",
              )
            }
          >
            <Scissors size={15} />
          </button>
          <button
            className="icon-button"
            disabled={
              disabled ||
              (!selection && (index < 0 || document.segments.length < 2))
            }
            aria-label="Удалить выделение"
            title="Удалить выделение"
            onClick={remove}
          >
            <Trash2 size={15} />
          </button>
          <button
            className="icon-button"
            disabled={disabled || index <= 0}
            aria-label="Переместить фрагмент влево"
            onClick={() =>
              onActions(
                [{ type: "move", index, to: index - 1 }],
                "Перемещение фрагмента",
              )
            }
          >
            <ChevronLeft size={15} />
          </button>
          <button
            className="icon-button"
            disabled={
              disabled || index < 0 || index >= document.segments.length - 1
            }
            aria-label="Переместить фрагмент вправо"
            onClick={() =>
              onActions(
                [{ type: "move", index, to: index + 1 }],
                "Перемещение фрагмента",
              )
            }
          >
            <ChevronRight size={15} />
          </button>
        </div>
        <div className="row">
          <span className="timecode">
            {selection
              ? `${time(selection.start, true)} — ${time(selection.end, true)}`
              : `${document.segments.length} фрагм. · ${time(total, true)}`}
          </span>
          <ZoomIn size={14} />
          <input
            aria-label="Масштаб таймлайна"
            type="range"
            min={1}
            max={8}
            step={1}
            value={zoom}
            onChange={(event) => setZoom(Number(event.target.value))}
          />
        </div>
      </div>
      <div className="timeline-body">
        <div className="timeline-labels">
          <span />
          <span>Видео</span>
          <span>Аудио</span>
          <span>Субтитры</span>
          <span>AI-правки</span>
        </div>
        <div ref={scroll} className="timeline-scroll">
          <div
            ref={inner}
            className="timeline-inner"
            style={{ width: `${zoom * 100}%`, minWidth: "100%" }}
            onPointerDown={down}
            onPointerMove={move}
            onPointerUp={up}
            onPointerCancel={up}
          >
            <div className="timeline-ruler">
              {ticks.map((value) => (
                <span
                  className="ruler-tick"
                  key={value}
                  style={{ left: `${total ? (value / total) * 100 : 0}%` }}
                >
                  {time(value)}
                </span>
              ))}
            </div>
            <div className="timeline-track video-track">
              {positions.map(({ segment, offset, length }, index) => (
                <div
                  className={`video-segment ${segment.id === selectedId ? "segment-selected" : ""}`}
                  key={segment.id}
                  style={{
                    left: `${(offset / total) * 100}%`,
                    width: `${(length / total) * 100}%`,
                  }}
                  onPointerDown={(event) => {
                    if (drag.current) return;
                    event.stopPropagation();
                    if (rangeMode || event.shiftKey) {
                      down(event);
                    } else {
                      onSelected(segment.id);
                      onSelection(null);
                      onSeek(point(event));
                    }
                  }}
                >
                  <span
                    className="trim-handle trim-left"
                    role="slider"
                    aria-label={`Начало фрагмента ${index + 1}`}
                    aria-valuenow={segment.start}
                    tabIndex={0}
                    onPointerDown={(event) => trim(event, segment.id, "start")}
                    onPointerMove={move}
                    onPointerUp={up}
                    onKeyDown={(event) => {
                      if (["ArrowLeft", "ArrowRight"].includes(event.key)) {
                        event.preventDefault();
                        const delta = event.key === "ArrowLeft" ? -0.1 : 0.1;
                        onChange(
                          {
                            ...document,
                            segments: document.segments.map((item) =>
                              item.id === segment.id
                                ? {
                                    ...item,
                                    start: Math.max(
                                      0,
                                      Math.min(
                                        item.end - 0.2,
                                        item.start + delta,
                                      ),
                                    ),
                                  }
                                : item,
                            ),
                          },
                          "Границы фрагмента",
                        );
                      }
                    }}
                  />
                  <span className="segment-label">
                    {index + 1} · {time(segment.start)}
                    {segment.speed !== 1 ? ` · ${segment.speed}×` : ""}
                  </span>
                  <span
                    className="trim-handle trim-right"
                    role="slider"
                    aria-label={`Конец фрагмента ${index + 1}`}
                    aria-valuenow={segment.end}
                    tabIndex={0}
                    onPointerDown={(event) => trim(event, segment.id, "end")}
                    onPointerMove={move}
                    onPointerUp={up}
                    onKeyDown={(event) => {
                      if (["ArrowLeft", "ArrowRight"].includes(event.key)) {
                        event.preventDefault();
                        const delta = event.key === "ArrowLeft" ? -0.1 : 0.1;
                        onChange(
                          {
                            ...document,
                            segments: document.segments.map((item) =>
                              item.id === segment.id
                                ? {
                                    ...item,
                                    end: Math.min(
                                      sourceDuration,
                                      Math.max(
                                        item.start + 0.2,
                                        item.end + delta,
                                      ),
                                    ),
                                  }
                                : item,
                            ),
                          },
                          "Границы фрагмента",
                        );
                      }
                    }}
                  />
                </div>
              ))}
            </div>
            <div className="timeline-track audio-track">
              <canvas
                ref={canvas}
                className="waveform-canvas"
                aria-label={
                  peaks.length
                    ? "Аудиоволна исходника"
                    : "Аудиоволна появится после подготовки аудио"
                }
              />
              {!peaks.length && (
                <span className="track-placeholder">Аудио готовится</span>
              )}
            </div>
            <div className="timeline-track captions-track">
              {captions.map((caption, index) => (
                <span
                  className="caption-segment"
                  key={`${caption.start}-${index}`}
                  style={{
                    left: `${(caption.start / total) * 100}%`,
                    width: `${((caption.end - caption.start) / total) * 100}%`,
                  }}
                >
                  {caption.text}
                </span>
              ))}
            </div>
            <div className="timeline-track edits-track">
              {proposal ? (
                <span
                  className="edit-marker"
                  style={{ left: 0, width: "100%" }}
                >
                  AI · {proposal.summary}
                </span>
              ) : (
                positions
                  .filter(
                    ({ segment }) =>
                      Object.keys(segment.effects || {}).length > 0,
                  )
                  .map(({ segment, offset, length }) => (
                    <span
                      className="edit-marker"
                      key={segment.id}
                      style={{
                        left: `${(offset / total) * 100}%`,
                        width: `${(length / total) * 100}%`,
                      }}
                    >
                      Правки
                    </span>
                  ))
              )}
            </div>
            {selection && (
              <div
                className="timeline-selection"
                style={{
                  left: `${(selection.start / total) * 100}%`,
                  width: `${((selection.end - selection.start) / total) * 100}%`,
                }}
              />
            )}
            <div
              className="playhead"
              style={{ left: `${(playhead / Math.max(0.01, total)) * 100}%` }}
            >
              <i />
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
