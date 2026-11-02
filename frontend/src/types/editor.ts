import type { Clip, ClipSettings, Project, Word } from "../api";
export type SegmentEffects = {
  zoom?: number;
  crop_x?: number;
  crop_y?: number;
  volume?: number;
  text?: string;
  text_size?: number;
  text_color?: string;
};
export type Segment = {
  id: string;
  start: number;
  end: number;
  speed: number;
  effects?: SegmentEffects;
};
export type EditorSettings = ClipSettings & {
  subtitle_highlight: boolean;
  volume: number;
  normalize_audio: boolean;
  aspect_ratio: "9:16" | "16:9" | "1:1" | "4:5";
};
export type DocumentState = {
  version: number;
  segments: Segment[];
  settings: EditorSettings;
  transition: { type: "none" | "fade"; duration: number };
  caption_overrides: Record<string, string>;
};
export type EditingAction = { type: string; [key: string]: unknown };
export type Proposal = {
  id: string;
  base_revision: number;
  summary: string;
  actions: EditingAction[];
  state: DocumentState;
  status: "pending" | "applied" | "cancelled";
  created_at: string;
};
export type ChatMessage = {
  id?: string | number;
  role: "user" | "assistant" | "system";
  content?: string;
  text?: string;
  created_at?: string;
  proposal_id?: string;
  status?: string;
};
export type EditorJob = {
  id: string | number;
  kind: string;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  stage: string;
  progress: number;
  error?: string | null;
  url?: string | null;
  download_url?: string | null;
  estimated_bytes?: number;
  revision?: number;
  options?: ExportOptions;
};
export type EditorSession = {
  clip: Clip;
  project: Project;
  revision: number;
  state: DocumentState;
  history: {
    can_undo: boolean;
    can_redo: boolean;
    cursor: number;
    entries: { label: string; created_at: string }[];
  };
  messages: ChatMessage[];
  proposals: Proposal[];
  source: {
    duration: number;
    width: number;
    height: number;
    fps: number;
    available: boolean;
  };
  proxy: {
    status?: string;
    quality?: string;
    url?: string | null;
    error?: string | null;
  };
  waveform: { peaks: number[]; duration: number };
  exports: EditorJob[];
};
export type TranscriptPage = { words: Word[]; total: number; offset: number };
export type PreviewQuality = "auto" | "360p" | "480p" | "720p" | "1080p";
export type ExportOptions = {
  platform: "tiktok" | "shorts" | "reels" | "custom";
  resolution: "720p" | "1080p" | "1440p" | "4k";
  fps: "original" | "30" | "60";
  preset: "fast" | "balanced" | "high" | "maximum";
  codec: "h264" | "h265";
  subtitles: boolean;
  watermark: string;
  max_bitrate_mbps?: number;
  crf?: number;
};
export type Selection = { start: number; end: number };
export function duration(document: DocumentState): number {
  const total = document.segments.reduce(
    (sum, segment) => sum + (segment.end - segment.start) / segment.speed,
    0,
  );
  return Math.max(
    0,
    total -
      (document.transition.type === "fade"
        ? Math.max(0, document.segments.length - 1) *
          document.transition.duration
        : 0),
  );
}
export function segmentPositions(
  document: DocumentState,
): { segment: Segment; offset: number; length: number; index: number }[] {
  let offset = 0;
  return document.segments.map((segment, index) => {
    const length = (segment.end - segment.start) / segment.speed;
    const result = { segment, offset, length, index };
    offset +=
      length -
      (document.transition.type === "fade" &&
      index < document.segments.length - 1
        ? document.transition.duration
        : 0);
    return result;
  });
}
export function atTime(document: DocumentState, output: number) {
  const positions = segmentPositions(document);
  return (
    positions.find(
      ({ offset, length }, index) =>
        output >= offset &&
        (output < offset + length || index === positions.length - 1),
    ) || positions[0]
  );
}
export function sourceTime(document: DocumentState, output: number): number {
  const found = atTime(document, output);
  return found
    ? found.segment.start +
        Math.max(0, output - found.offset) * found.segment.speed
    : 0;
}
export function mappedWords(document: DocumentState, words: Word[]): Word[] {
  return segmentPositions(document).flatMap(({ segment, offset }) =>
    words
      .filter((word) => word.end > segment.start && word.start < segment.end)
      .map((word) => ({
        start:
          offset +
          (Math.max(segment.start, word.start) - segment.start) / segment.speed,
        end:
          offset +
          (Math.min(segment.end, word.end) - segment.start) / segment.speed,
        text: document.caption_overrides[String(word.start)] ?? word.text,
      })),
  );
}
