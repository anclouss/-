import { useCallback, useEffect, useRef, useState } from "react";
import { aborted, api, message } from "../api";
export function useResource<T>(path: string, poll = 0) {
  const [data, setData] = useState<T | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true);
  const controller = useRef<AbortController | null>(null),
    generation = useRef(0);
  const refresh = useCallback(async () => {
    controller.current?.abort();
    const current = ++generation.current;
    const request = new AbortController();
    controller.current = request;
    try {
      const value = await api<T>(path, { signal: request.signal });
      if (generation.current === current) {
        setData(value);
        setError("");
        return value;
      }
    } catch (failure) {
      if (!aborted(failure) && generation.current === current)
        setError(message(failure));
    } finally {
      if (generation.current === current) setLoading(false);
    }
    return undefined;
  }, [path]);
  const invalidate = useCallback(() => {
    generation.current++;
    controller.current?.abort();
  }, []);
  useEffect(() => {
    let stopped = false,
      timer: number | undefined;
    setLoading(true);
    setData(null);
    setError("");
    const cycle = async () => {
      await refresh();
      if (!stopped && poll) timer = window.setTimeout(cycle, poll);
    };
    void cycle();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      invalidate();
    };
  }, [refresh, poll, invalidate]);
  return { data, setData, error, loading, refresh };
}
