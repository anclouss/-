import { useEffect, useState } from "react";
import { aborted, api } from "../api";
import type { EditorJob } from "../types/editor";
export function useJob() {
  const [job, setJob] = useState<EditorJob | null>(null);
  useEffect(() => {
    if (!job || !["queued", "running"].includes(job.status)) return;
    const controller = new AbortController();
    let timer: number | undefined,
      stopped = false;
    const poll = async () => {
      try {
        const next = await api<EditorJob>(`/editor-jobs/${job.id}`, {
          signal: controller.signal,
        });
        if (!stopped) setJob(next);
        if (!stopped && ["queued", "running"].includes(next.status))
          timer = window.setTimeout(poll, 1500);
      } catch (error) {
        if (!stopped && !aborted(error)) timer = window.setTimeout(poll, 3500);
      }
    };
    timer = window.setTimeout(poll, 1000);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [job]);
  return [job, setJob] as const;
}
