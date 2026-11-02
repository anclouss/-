import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, aborted, api, message } from "../api";
import { clearDraft, readDraft, writeDraft } from "../editor/draft";
import type {
  DocumentState,
  EditingAction,
  EditorSession,
  Proposal,
  Selection,
} from "../types/editor";

export function useEditor(id: string | undefined) {
  const [session, setSession] = useState<EditorSession | null>(null),
    [document, setDocument] = useState<DocumentState | null>(null);
  const [error, setError] = useState(""),
    [loading, setLoading] = useState(true),
    [saving, setSaving] = useState(false),
    [working, setWorking] = useState(false),
    [conflict, setConflict] = useState(false);
  const current = useRef<EditorSession | null>(null),
    draft = useRef<DocumentState | null>(null),
    dirty = useRef(false),
    blocked = useRef(false),
    mounted = useRef(true),
    mutation = useRef(false);
  const timer = useRef<number | undefined>(undefined),
    savePromise = useRef<Promise<void> | null>(null),
    controllers = useRef(new Set<AbortController>());
  const saveLabel = useRef("Изменения в редакторе"),
    draftBase = useRef<number | null>(null);
  const persist = useCallback(() => {
    if (id && draft.current && (dirty.current || savePromise.current))
      writeDraft(localStorage, id, {
        base_revision: draftBase.current ?? current.current?.revision ?? 0,
        state: draft.current,
        label: saveLabel.current,
        saved_at: Date.now(),
      });
  }, [id]);
  const request = useCallback(
    async <T>(path: string, options: RequestInit = {}): Promise<T> => {
      const controller = new AbortController();
      controllers.current.add(controller);
      try {
        return await api<T>(path, { ...options, signal: controller.signal });
      } finally {
        controllers.current.delete(controller);
      }
    },
    [],
  );
  const accept = useCallback(
    (value: EditorSession, replaceDocument = true) => {
      current.current = value;
      setSession(value);
      if (replaceDocument) {
        draft.current = value.state;
        setDocument(value.state);
      }
      if (dirty.current) {
        draftBase.current = value.revision;
        persist();
      } else if (id) {
        draftBase.current = value.revision;
        clearDraft(localStorage, id);
      }
    },
    [id, persist],
  );
  const flush = useCallback(async () => {
    window.clearTimeout(timer.current);
    if (savePromise.current) {
      await savePromise.current;
    }
    if (blocked.current)
      throw new Error(
        "Сохранение остановлено из-за конфликта. Обновите состояние редактора.",
      );
    if (!dirty.current || !draft.current || !current.current) return;
    const operation = async () => {
      setSaving(true);
      try {
        while (dirty.current && draft.current && current.current) {
          const snapshot = structuredClone(draft.current),
            revision = current.current.revision;
          dirty.current = false;
          try {
            const response = await request<EditorSession>(`/editor/${id}`, {
              method: "PATCH",
              body: JSON.stringify({
                revision,
                state: snapshot,
                label: saveLabel.current,
              }),
            });
            if (!mounted.current) return;
            accept(response, !dirty.current);
            setError("");
          } catch (failure) {
            dirty.current = true;
            if (failure instanceof ApiError && failure.status === 409) {
              blocked.current = true;
              setConflict(true);
            }
            throw failure;
          }
        }
      } finally {
        if (mounted.current) setSaving(false);
      }
    };
    const pending = operation();
    savePromise.current = pending;
    try {
      await pending;
    } finally {
      if (savePromise.current === pending) savePromise.current = null;
    }
  }, [accept, id, request]);
  const change = useCallback(
    (
      next: DocumentState | ((value: DocumentState) => DocumentState),
      label = "Изменения в редакторе",
    ) => {
      if (!draft.current || blocked.current) return;
      const value = typeof next === "function" ? next(draft.current) : next;
      if (!dirty.current) draftBase.current = current.current?.revision ?? 0;
      draft.current = value;
      dirty.current = true;
      saveLabel.current = label;
      persist();
      setDocument(value);
      setError("");
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => {
        void flush().catch((failure) => {
          if (!aborted(failure)) setError(message(failure));
        });
      }, 550);
    },
    [flush, persist],
  );
  const refresh = useCallback(async () => {
    const value = await request<EditorSession>(`/editor/${id}`);
    if (!mounted.current) return value;
    if (!current.current) {
      const cached = id ? readDraft(localStorage, id) : null;
      accept(value);
      if (
        cached &&
        JSON.stringify(cached.state) !== JSON.stringify(value.state)
      ) {
        draft.current = cached.state;
        draftBase.current = cached.base_revision;
        saveLabel.current = cached.label;
        dirty.current = true;
        setDocument(cached.state);
        if (id) writeDraft(localStorage, id, cached);
        if (cached.base_revision === value.revision) {
          timer.current = window.setTimeout(() => {
            void flush().catch((failure) => {
              if (!aborted(failure)) setError(message(failure));
            });
          }, 550);
        } else {
          blocked.current = true;
          setConflict(true);
          setError(
            "Восстановлен локальный черновик для более ранней версии. Сохранённый проект уже изменился; выберите Undo или загрузите сохранённую версию.",
          );
        }
      }
      return value;
    }
    if (
      current.current &&
      value.revision !== current.current.revision &&
      (dirty.current || savePromise.current)
    ) {
      // This can also be the current save completing; wait for its response.
      if (!savePromise.current) {
        blocked.current = true;
        setConflict(true);
        setError(
          "Проект изменён в другом окне. Ваши локальные правки ещё не сохранены.",
        );
      }
      setSession((previous) =>
        previous
          ? {
              ...previous,
              exports: value.exports,
              proxy: value.proxy,
              messages: value.messages,
              proposals: value.proposals,
            }
          : value,
      );
    } else if (!mutation.current && !savePromise.current && !dirty.current)
      accept(
        value,
        value.revision !== current.current?.revision || !draft.current,
      );
    else
      setSession((previous) =>
        previous
          ? {
              ...previous,
              exports: value.exports,
              proxy: value.proxy,
              messages: value.messages,
              proposals: value.proposals,
            }
          : value,
      );
    return value;
  }, [accept, id, request, flush]);
  const reload = useCallback(async () => {
    const value = await request<EditorSession>(`/editor/${id}`);
    dirty.current = false;
    blocked.current = false;
    setConflict(false);
    setError("");
    accept(value);
  }, [accept, id, request]);
  const mutate = useCallback(
    async (suffix: string, body: Record<string, unknown>) => {
      if (mutation.current) return;
      mutation.current = true;
      setWorking(true);
      setError("");
      try {
        await flush();
        const value = await request<EditorSession>(`/editor/${id}${suffix}`, {
          method: "POST",
          body: JSON.stringify({
            ...body,
            revision: current.current?.revision,
          }),
        });
        dirty.current = false;
        accept(value);
        return value;
      } catch (failure) {
        if (!aborted(failure)) setError(message(failure));
        throw failure;
      } finally {
        mutation.current = false;
        if (mounted.current) setWorking(false);
      }
    },
    [accept, flush, id, request],
  );
  const actions = useCallback(
    (
      items: EditingAction[],
      label = "Монтаж",
      context?: { playhead: number; selection?: Selection },
    ) => mutate("/actions", { actions: items, label, context }),
    [mutate],
  );
  const history = useCallback(
    async (action: "undo" | "redo" | "reset") => {
      if (action === "undo" || action === "reset") {
        window.clearTimeout(timer.current);
        if (savePromise.current) {
          try {
            await savePromise.current;
          } catch {
            /* Undo can discard an invalid local draft. */
          }
        }
        if (dirty.current || blocked.current) {
          const canonical = blocked.current
            ? await request<EditorSession>(`/editor/${id}`)
            : current.current;
          dirty.current = false;
          blocked.current = false;
          setConflict(false);
          setError("");
          if (canonical) accept(canonical);
          if (action === "undo") return canonical;
        }
      }
      return mutate("/history", { action });
    },
    [mutate, request, id, accept],
  );
  const decision = useCallback(
    (proposal: Proposal, action: "apply" | "cancel") =>
      mutate(`/proposals/${proposal.id}`, { action }),
    [mutate],
  );
  useEffect(() => {
    mounted.current = true;
    current.current = null;
    draft.current = null;
    dirty.current = false;
    blocked.current = false;
    setConflict(false);
    setLoading(true);
    setError("");
    let stopped = false,
      polling: number | undefined,
      eventTimer: number | undefined,
      connected = false,
      refreshing = false;
    const cycle = async () => {
      if (refreshing || stopped) return;
      refreshing = true;
      try {
        await refresh();
      } catch (failure) {
        if (!stopped && !aborted(failure)) setError(message(failure));
      } finally {
        refreshing = false;
        if (!stopped) {
          setLoading(false);
          if (!connected) polling = window.setTimeout(cycle, 5000);
        }
      }
    };
    void cycle();
    const stream = new EventSource(`/api/editor/${id}/events`);
    stream.onopen = () => {
      connected = true;
      window.clearTimeout(polling);
    };
    stream.onmessage = () => {
      window.clearTimeout(eventTimer);
      eventTimer = window.setTimeout(() => {
        if (globalThis.document.visibilityState !== "hidden") void cycle();
      }, 180);
    };
    stream.onerror = () => {
      connected = false;
      window.clearTimeout(polling);
      if (!stopped) polling = window.setTimeout(cycle, 2500);
    };
    const visible = () => {
      if (globalThis.document.visibilityState === "visible") void cycle();
    };
    globalThis.document.addEventListener("visibilitychange", visible);
    const active = controllers.current;
    return () => {
      persist();
      stopped = true;
      mounted.current = false;
      stream.close();
      globalThis.document.removeEventListener("visibilitychange", visible);
      window.clearTimeout(eventTimer);
      window.clearTimeout(polling);
      window.clearTimeout(timer.current);
      active.forEach((controller) => controller.abort());
    };
  }, [id, refresh, persist]);
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (dirty.current || savePromise.current) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    const onHidden = () => {
      if (globalThis.document.visibilityState === "hidden")
        void flush().catch(() => undefined);
    };
    const navigate = (event: Event) => {
      const detail = (
        event as CustomEvent<{ waitUntil: (promise: Promise<void>) => void }>
      ).detail;
      detail.waitUntil(
        flush().catch((failure) => {
          setError(message(failure));
          throw failure;
        }),
      );
    };
    window.addEventListener("clipforge:navigate", navigate);
    window.addEventListener("beforeunload", beforeUnload);
    globalThis.document.addEventListener("visibilitychange", onHidden);
    return () => {
      window.removeEventListener("clipforge:navigate", navigate);
      window.removeEventListener("beforeunload", beforeUnload);
      globalThis.document.removeEventListener("visibilitychange", onHidden);
    };
  }, [flush]);
  const getCurrent = useCallback(() => current.current, []);
  return {
    getCurrent,
    hasDraft: dirty.current,
    session,
    document,
    revision: session?.revision ?? 0,
    loading,
    error,
    setError,
    saving,
    working,
    conflict,
    change,
    flush,
    refresh,
    reload,
    actions,
    history,
    decision,
    accept,
  };
}
