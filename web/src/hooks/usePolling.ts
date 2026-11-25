import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Load data now, then again every `intervalMs` ("● Live"). Pauses while the tab is hidden
 * (saves phone battery and data) and refreshes as soon as it's visible again.
 * Changing `key` (e.g. a filter) reloads immediately.
 */
export function usePolling<T>(load: () => Promise<T>, key: string, intervalMs = 10_000) {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<unknown>(undefined);
  const [loading, setLoading] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<number | undefined>(undefined);
  const loadRef = useRef(load);
  loadRef.current = load;
  const seq = useRef(0);

  const refresh = useCallback(async () => {
    const mine = ++seq.current;
    try {
      const value = await loadRef.current();
      if (mine !== seq.current) return; // a newer request already answered
      setData(value);
      setError(undefined);
      setUpdatedAt(Date.now());
    } catch (err) {
      if (mine === seq.current) setError(err);
    } finally {
      if (mine === seq.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    void refresh();
    const timer = window.setInterval(() => {
      if (!document.hidden) void refresh();
    }, intervalMs);
    const onVisible = () => {
      if (!document.hidden) void refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [key, intervalMs, refresh]);

  // Used after a change (e.g. a PATCH) to show the server's new answer right away.
  // Bumping `seq` makes any GET that was already in flight ignore its (older) answer,
  // so a slow poll can't put the old data back on screen.
  const set = useCallback((value: T) => {
    seq.current++;
    setData(value);
    setError(undefined);
    setLoading(false); // the ignored GET won't clear it any more
  }, []);

  return { data, error, loading, updatedAt, refresh, setData: set };
}

/** Re-render every second, for ticking timers ("Received 12 min ago"). */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(t);
  }, [intervalMs]);
  return now;
}

/** A value that only updates after the user stops typing for `ms`. */
export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return v;
}
