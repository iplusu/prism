import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage } from "./api";
import type { TaskMode } from "./types";

export const MODE_LABELS: Record<TaskMode, string> = {
  auto: "Auto",
  llm: "Orchestrator LLM",
  codex: "Codex",
  claude: "Claude Code",
  antigravity: "Antigravity",
  demo: "Demo",
};

export function modeLabel(m: string | null | undefined): string {
  if (!m) return "";
  return (MODE_LABELS as Record<string, string>)[m] ?? m;
}

export function shortRev(rev: string | null | undefined): string {
  return rev ? rev.slice(0, 8) : "";
}

/**
 * Parse a backend timestamp. With SQLite the backend returns naive ISO
 * strings (no offset) that are UTC; treat them as UTC, not local time.
 */
export function parseTime(iso: string): Date {
  const hasZone = /([zZ]|[+-]\d\d:?\d\d)$/.test(iso);
  return new Date(hasZone || !iso.includes("T") ? iso : `${iso}Z`);
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = parseTime(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function relTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = parseTime(iso).getTime();
  if (Number.isNaN(d)) return iso;
  const s = Math.round((Date.now() - d) / 1000);
  if (s < 45) return "just now";
  if (s < 90) return "1 min ago";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const days = Math.round(h / 24);
  if (days < 14) return `${days} d ago`;
  return parseTime(iso).toLocaleDateString();
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function fmtDuration(sec: number | null | undefined): string {
  if (sec == null) return "";
  if (sec < 60) return `${sec.toFixed(1)} s`;
  return `${Math.floor(sec / 60)} min ${Math.round(sec % 60)} s`;
}

export function basename(p: string): string {
  const i = p.lastIndexOf("/");
  return i >= 0 ? p.slice(i + 1) : p;
}

export function dirname(p: string): string {
  const i = p.lastIndexOf("/");
  return i >= 0 ? p.slice(0, i) : "";
}

export function isImagePath(p: string): boolean {
  return /\.(png|jpe?g|gif|webp|bmp)$/i.test(p);
}

/** Stable pleasant color for a user name (awareness cursors, avatars). */
export function colorFor(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return `hsl(${h % 360} 65% 45%)`;
}

export interface AsyncState<T> {
  data: T | undefined;
  error: string | null;
  loading: boolean;
  reload: () => Promise<void>;
  setData: (d: T | undefined) => void;
}

/** Load data from the API; re-run when deps change. Keeps stale data during reloads. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const reload = useCallback(async () => {
    const my = ++seq.current;
    setLoading(true);
    try {
      const d = await fnRef.current();
      if (my === seq.current) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      if (my === seq.current) setError(errorMessage(e));
    } finally {
      if (my === seq.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error, loading, reload, setData };
}

/** setInterval that always calls the latest callback; delay null pauses. */
export function useInterval(cb: () => void, delay: number | null): void {
  const ref = useRef(cb);
  ref.current = cb;
  useEffect(() => {
    if (delay == null) return;
    const id = window.setInterval(() => ref.current(), delay);
    return () => window.clearInterval(id);
  }, [delay]);
}

export function useMediaQuery(query: string): boolean {
  const [match, setMatch] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setMatch(mq.matches);
    mq.addEventListener("change", on);
    on();
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return match;
}

/** Wrap an async action with busy/error state for buttons. */
export function useAction<A extends unknown[], R>(fn: (...a: A) => Promise<R>) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const run = useCallback(async (...a: A): Promise<R | undefined> => {
    setBusy(true);
    setError(null);
    try {
      return await fnRef.current(...a);
    } catch (e) {
      setError(errorMessage(e));
      return undefined;
    } finally {
      setBusy(false);
    }
  }, []);
  return { run, busy, error, setError };
}
