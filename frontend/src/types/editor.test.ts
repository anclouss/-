import { describe, expect, it } from "vitest";
import type { DocumentState } from "./editor";
import { duration, mappedWords, segmentPositions, sourceTime } from "./editor";
const state: DocumentState = {
  version: 1,
  segments: [
    { id: "reaction", start: 100, end: 120, speed: 2, effects: {} },
    { id: "setup", start: 0, end: 10, speed: 1, effects: {} },
  ],
  settings: {
    layout: "crop",
    crop_x: 0.5,
    crop_y: 0.5,
    face_box: [0.65, 0, 0.35, 0.4],
    face_tracking: false,
    subtitle_font: "Arial",
    subtitle_size: 58,
    subtitle_position: 0.76,
    subtitle_color: "#B8FF60",
    subtitles: true,
    subtitle_highlight: true,
    auto_zoom: false,
    remove_silence: false,
    volume: 1,
    normalize_audio: true,
    aspect_ratio: "9:16",
  },
  transition: { type: "none", duration: 0.2 },
  caption_overrides: { "102": "Исправлено" },
};
describe("source and editing timeline synchronization", () => {
  it("computes duration and seeks into moved and accelerated fragments", () => {
    expect(duration(state)).toBe(20);
    expect(sourceTime(state, 3)).toBe(106);
    expect(sourceTime(state, 12)).toBe(2);
    expect(segmentPositions(state)[1].offset).toBe(10);
  });
  it("maps edited captions into output time without words from removed regions", () => {
    expect(
      mappedWords(state, [
        { start: 102, end: 104, text: "Реакция" },
        { start: 1, end: 2, text: "Начало" },
        { start: 50, end: 51, text: "Удалено" },
      ]),
    ).toEqual([
      { start: 1, end: 2, text: "Исправлено" },
      { start: 11, end: 12, text: "Начало" },
    ]);
  });
  it("accounts for transition overlap when determining following fragment position", () => {
    const faded = {
      ...state,
      transition: { type: "fade" as const, duration: 0.2 },
    };
    expect(duration(faded)).toBeCloseTo(19.8);
    expect(segmentPositions(faded)[1].offset).toBeCloseTo(9.8);
  });
  it("clips words at selected boundaries", () => {
    expect(
      mappedWords(state, [{ start: 119, end: 121, text: "Конец" }]),
    ).toEqual([{ start: 9.5, end: 10, text: "Конец" }]);
  });
});
