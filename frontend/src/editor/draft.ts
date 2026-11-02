import type { DocumentState } from "../types/editor";
export type LocalDraft = {
  base_revision: number;
  state: DocumentState;
  label: string;
  saved_at: number;
};
export type DraftStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;
const key = (id: string) => `clipforge:editor-draft:${id}`;
export function readDraft(
  storage: DraftStorage,
  id: string,
): LocalDraft | null {
  try {
    const raw = storage.getItem(key(id));
    if (!raw || raw.length > 500000) return null;
    const value: unknown = JSON.parse(raw);
    if (
      typeof value !== "object" ||
      value === null ||
      !("base_revision" in value) ||
      !Number.isInteger(value.base_revision) ||
      !("state" in value) ||
      typeof value.state !== "object" ||
      value.state === null ||
      !("segments" in value.state) ||
      !Array.isArray(value.state.segments) ||
      !value.state.segments.length ||
      value.state.segments.length > 256 ||
      !("settings" in value.state) ||
      typeof value.state.settings !== "object" ||
      value.state.settings === null ||
      !("caption_overrides" in value.state) ||
      typeof value.state.caption_overrides !== "object" ||
      value.state.caption_overrides === null ||
      !("transition" in value.state) ||
      typeof value.state.transition !== "object" ||
      value.state.transition === null ||
      !("label" in value) ||
      typeof value.label !== "string" ||
      !("saved_at" in value) ||
      typeof value.saved_at !== "number"
    )
      return null;
    return value as LocalDraft;
  } catch {
    return null;
  }
}
export function writeDraft(
  storage: DraftStorage,
  id: string,
  value: LocalDraft,
): boolean {
  try {
    const raw = JSON.stringify(value);
    if (raw.length > 500000) return false;
    storage.setItem(key(id), raw);
    return true;
  } catch {
    return false;
  }
}
export function clearDraft(storage: DraftStorage, id: string) {
  try {
    storage.removeItem(key(id));
  } catch {
    /* Server autosave remains available when browser storage is disabled. */
  }
}
