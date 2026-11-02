import { describe, expect, it } from "vitest";
import { clearDraft, readDraft, writeDraft } from "./draft";
import type { DraftStorage, LocalDraft } from "./draft";
import type { DocumentState } from "../types/editor";
const state: DocumentState = {
  version: 1,
  segments: [
    { id: "second", start: 20, end: 25, speed: 2 },
    { id: "first", start: 0, end: 10, speed: 1 },
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
  caption_overrides: { "22.5": "Правка" },
};
function storage(): DraftStorage {
  const values = new Map<string, string>();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => {
      values.set(key, value);
    },
    removeItem: (key) => {
      values.delete(key);
    },
  };
}
const record: LocalDraft = {
  base_revision: 7,
  state,
  label: "Перемещение",
  saved_at: 1234,
};
describe("recovery of edits before server autosave", () => {
  it("retains base revision, fragment order and caption edits across reload", () => {
    const cache = storage();
    expect(writeDraft(cache, "clip", record)).toBe(true);
    const restored = readDraft(cache, "clip");
    expect(restored?.base_revision).toBe(7);
    expect(restored?.state.segments.map((segment) => segment.id)).toEqual([
      "second",
      "first",
    ]);
    expect(restored?.state.caption_overrides["22.5"]).toBe("Правка");
    expect(readDraft(cache, "another")).toBeNull();
  });
  it("removes only the saved clip draft", () => {
    const cache = storage();
    writeDraft(cache, "a", record);
    writeDraft(cache, "b", record);
    clearDraft(cache, "a");
    expect(readDraft(cache, "a")).toBeNull();
    expect(readDraft(cache, "b")).not.toBeNull();
  });
  it("rejects corrupt browser data and bounds cached state size", () => {
    const cache = storage();
    cache.setItem("clipforge:editor-draft:broken", "{");
    expect(readDraft(cache, "broken")).toBeNull();
    expect(
      writeDraft(cache, "big", { ...record, label: "x".repeat(500001) }),
    ).toBe(false);
    expect(readDraft(cache, "big")).toBeNull();
  });
  it("does not crash editing when local storage is unavailable", () => {
    const cache: DraftStorage = {
      getItem: () => {
        throw new Error("denied");
      },
      setItem: () => {
        throw new Error("full");
      },
      removeItem: () => {
        throw new Error("denied");
      },
    };
    expect(readDraft(cache, "clip")).toBeNull();
    expect(writeDraft(cache, "clip", record)).toBe(false);
    expect(() => clearDraft(cache, "clip")).not.toThrow();
  });
});
