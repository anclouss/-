import { useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Maximize,
  Pause,
  Play,
  Volume2,
} from "lucide-react";
import { time } from "../api";
import type { Word } from "../api";
import type { DocumentState, EditorSession } from "../types/editor";
import { atTime, duration, mappedWords, sourceTime } from "../types/editor";
export function VideoPreview({
  document,
  session,
  url,
  baked,
  playhead,
  onPlayhead,
  onError,
  playing,
  setPlaying,
  onWords,
}: {
  document: DocumentState;
  session: EditorSession;
  url: string;
  baked: boolean;
  playhead: number;
  onPlayhead: (time: number) => void;
  onError: (error: string) => void;
  playing: boolean;
  setPlaying: (value: boolean) => void;
  onWords: (words: Word[]) => void;
}) {
  const audioGraph = useRef<{
    context: AudioContext;
    source: MediaElementAudioSourceNode;
    gain: GainNode;
  } | null>(null);
  const video = useRef<HTMLVideoElement>(null),
    face = useRef<HTMLVideoElement>(null),
    container = useRef<HTMLDivElement>(null),
    head = useRef(playhead),
    playingRef = useRef(playing);
  const [words, setWords] = useState<Word[]>([]),
    [height, setHeight] = useState(480);
  head.current = playhead;
  playingRef.current = playing;
  const found = atTime(document, playhead),
    settings = document.settings,
    total = duration(document),
    source = sourceTime(document, playhead);
  const bucket = Math.floor(source / 12);
  useEffect(() => {
    const controller = new AbortController();
    fetch(
      `/api/editor/${session.clip.id}/transcript?start=${Math.max(0, bucket * 12 - 8)}&end=${bucket * 12 + 30}&limit=200`,
      { signal: controller.signal },
    )
      .then(async (response) => {
        if (response.ok) {
          const value: unknown = await response.json();
          if (
            typeof value === "object" &&
            value !== null &&
            "words" in value &&
            Array.isArray(value.words)
          ) {
            setWords(value.words as Word[]);
            onWords(value.words as Word[]);
          }
        }
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [session.clip.id, session.project.status, bucket, onWords]);
  const captions = useMemo(
    () => mappedWords(document, words),
    [document, words],
  );
  const activeWord = captions.findIndex(
    (word) => playhead >= word.start && playhead < word.end,
  );
  const phrase =
    activeWord < 0
      ? []
      : captions.slice(
          Math.floor(activeWord / 5) * 5,
          Math.floor(activeWord / 5) * 5 + 5,
        );
  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const observer = new ResizeObserver(() => setHeight(element.clientHeight));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const current = video.current;
    if (!current) return;
    const wanted = baked ? playhead : sourceTime(document, playhead);
    if (Math.abs(current.currentTime - wanted) > 0.22)
      current.currentTime = wanted;
    current.playbackRate = baked
      ? 1
      : atTime(document, playhead)?.segment.speed || 1;
    const gain = baked
      ? 1
      : Math.max(0, settings.volume * (found?.segment.effects?.volume ?? 1));
    if (audioGraph.current) {
      current.volume = 1;
      audioGraph.current.gain.gain.value = gain;
    } else current.volume = Math.min(1, gain);
  }, [
    playhead,
    document,
    baked,
    settings.volume,
    found?.segment.effects?.volume,
  ]);
  useEffect(() => {
    const current = video.current;
    if (!current) return;
    if (playing)
      void current.play().catch(() => {
        setPlaying(false);
        onError(
          "Не удалось воспроизвести видео. Проверьте кодек исходника или создайте proxy.",
        );
      });
    else current.pause();
  }, [playing, url, onError, setPlaying]);
  const initializeAudio = () => {
    const current = video.current;
    if (!current) return;
    if (!audioGraph.current) {
      try {
        const context = new AudioContext(),
          source = context.createMediaElementSource(current),
          gain = context.createGain();
        source.connect(gain);
        gain.connect(context.destination);
        audioGraph.current = { context, source, gain };
      } catch {
        return;
      }
    }
    current.volume = 1;
    audioGraph.current.gain.gain.value = baked
      ? 1
      : settings.volume * (found?.segment.effects?.volume ?? 1);
    void audioGraph.current.context.resume().catch(() => undefined);
  };
  useEffect(
    () => () => {
      const graph = audioGraph.current;
      if (graph) {
        graph.source.disconnect();
        graph.gain.disconnect();
        void graph.context.close().catch(() => undefined);
        audioGraph.current = null;
      }
    },
    [],
  );
  const synchronize = () => {
    const current = video.current;
    if (!current) return;
    if (baked) {
      onPlayhead(Math.min(total, current.currentTime));
      return;
    }
    const position = atTime(document, head.current);
    if (!position) return;
    if (current.currentTime >= position.segment.end - 0.03) {
      const next = document.segments[position.index + 1];
      if (next) {
        current.currentTime = next.start;
        onPlayhead(Math.min(total, position.offset + position.length));
      } else {
        current.pause();
        setPlaying(false);
        onPlayhead(total);
      }
    } else
      onPlayhead(
        Math.max(
          0,
          position.offset +
            (current.currentTime - position.segment.start) /
              position.segment.speed,
        ),
      );
    if (face.current) {
      if (Math.abs(face.current.currentTime - current.currentTime) > 0.14)
        face.current.currentTime = current.currentTime;
      face.current.playbackRate = current.playbackRate;
      if (!current.paused) void face.current.play().catch(() => undefined);
      else face.current.pause();
    }
  };
  const zoom = found?.segment.effects?.zoom || 1,
    box = settings.face_box;
  const videoStyle = {
    objectPosition: `${(found?.segment.effects?.crop_x ?? settings.crop_x) * 100}% ${(found?.segment.effects?.crop_y ?? settings.crop_y) * 100}%`,
    objectFit:
      settings.layout === "fit" ? ("contain" as const) : ("cover" as const),
    transform: `scale(${zoom})`,
  };
  const split = !baked && settings.layout === "split";
  return (
    <section className="panel preview-panel">
      <div className="panel-heading">
        <span>Предпросмотр</span>
        <span className="preview-badge">
          {baked ? "Обработанный preview" : "Быстрый · без рендера"}
        </span>
      </div>
      <div className="preview-stage">
        <div
          ref={container}
          className={`video-composition ${split ? "split-composition" : ""}`}
          style={{ aspectRatio: settings.aspect_ratio.replace(":", " / ") }}
        >
          {split && (
            <div key="face" className="split-face">
              <video
                ref={face}
                src={url}
                muted
                playsInline
                preload="metadata"
                style={{
                  position: "absolute",
                  width: `${100 / Math.max(0.05, box[2])}%`,
                  height: `${100 / Math.max(0.05, box[3])}%`,
                  left: `${(-box[0] / Math.max(0.05, box[2])) * 100}%`,
                  top: `${(-box[1] / Math.max(0.05, box[3])) * 100}%`,
                  maxWidth: "none",
                  objectFit: "fill",
                }}
              />
            </div>
          )}
          <div
            key="base"
            className={split ? "split-game" : "preview-video-wrap"}
          >
            <video
              ref={video}
              className="preview-video"
              src={url}
              playsInline
              preload="metadata"
              style={baked ? undefined : videoStyle}
              onLoadedMetadata={(event) => {
                event.currentTarget.currentTime = baked
                  ? head.current
                  : sourceTime(document, head.current);
                if (playingRef.current)
                  void event.currentTarget
                    .play()
                    .catch(() => setPlaying(false));
              }}
              onTimeUpdate={synchronize}
              onPlay={() => {
                initializeAudio();
                setPlaying(true);
              }}
              onPause={() => {
                if (!video.current?.seeking) setPlaying(false);
                face.current?.pause();
              }}
              onEnded={() => {
                setPlaying(false);
                onPlayhead(total);
              }}
              onError={() =>
                onError(
                  "Не удалось открыть видео. Попробуйте proxy 480p или проверьте исходный файл.",
                )
              }
            />
          </div>
          {!baked && settings.subtitles && phrase.length > 0 && (
            <div
              className="caption-layer"
              style={{
                top: `${settings.subtitle_position * 100}%`,
                fontFamily: settings.subtitle_font,
                fontSize: Math.max(
                  10,
                  (settings.subtitle_size * height) / 1920,
                ),
              }}
            >
              {phrase.map((word, index) => (
                <span
                  className={`caption-word ${settings.subtitle_highlight && playhead >= word.start && playhead < word.end ? "spoken" : ""}`}
                  style={
                    settings.subtitle_highlight &&
                    playhead >= word.start &&
                    playhead < word.end
                      ? { color: settings.subtitle_color }
                      : undefined
                  }
                  key={`${word.start}-${index}`}
                >
                  {word.text}{" "}
                </span>
              ))}
            </div>
          )}
          {!baked && found?.segment.effects?.text && (
            <div
              className="overlay-text"
              style={{
                color: found.segment.effects.text_color || "#ffffff",
                fontSize: Math.max(
                  12,
                  ((found.segment.effects.text_size || 58) * height) / 1920,
                ),
              }}
            >
              {found.segment.effects.text}
            </div>
          )}
          {!url && (
            <span className="preview-status">Исходное видео недоступно</span>
          )}
        </div>
      </div>
      <div className="preview-transport">
        <div className="row">
          <button
            className="icon-button"
            aria-label="Назад на секунду"
            onClick={() => onPlayhead(Math.max(0, playhead - 1))}
          >
            <ChevronLeft size={17} />
          </button>
          <button
            className="icon-button transport-play"
            aria-label={playing ? "Пауза" : "Воспроизвести"}
            onClick={() => setPlaying(!playing)}
          >
            {playing ? <Pause size={17} /> : <Play size={17} />}
          </button>
          <button
            className="icon-button"
            aria-label="Вперёд на секунду"
            onClick={() => onPlayhead(Math.min(total, playhead + 1))}
          >
            <ChevronRight size={17} />
          </button>
        </div>
        <span className="timecode">
          {time(playhead, true)} <span>/ {time(total, true)}</span>
        </span>
        <div className="row">
          <Volume2 size={15} />
          <button
            className="icon-button"
            aria-label="Полный экран"
            onClick={() => void container.current?.requestFullscreen()}
          >
            <Maximize size={15} />
          </button>
        </div>
      </div>
    </section>
  );
}
