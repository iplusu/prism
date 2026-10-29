import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { ACTIVE_TASK_STATUSES, type Task, type TaskEvent, type TaskStatus } from "../types";
import { modeLabel } from "../util";
import { Spinner } from "./ui";

/**
 * Live task events over SSE (`task` events, then `end`). The backend resumes
 * from the `after` query parameter (not Last-Event-ID), so reconnects are
 * done manually from the last event id.
 */
export function useTaskStream(taskId: string, active: boolean, onEnd: (status: TaskStatus | string) => void) {
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const lastId = useRef(0);
  const endRef = useRef(onEnd);
  endRef.current = onEnd;

  useEffect(() => {
    if (!active) return;
    let es: EventSource | null = null;
    let retry: number | undefined;
    let stopped = false;
    const open = () => {
      es = new EventSource(api.taskStreamUrl(taskId, lastId.current), { withCredentials: true });
      es.onopen = () => setConnected(true);
      es.addEventListener("task", (ev) => {
        const e = JSON.parse((ev as MessageEvent<string>).data) as TaskEvent;
        if (e.id <= lastId.current) return;
        lastId.current = e.id;
        setEvents((prev) => [...prev, e]);
      });
      es.addEventListener("end", (ev) => {
        stopped = true;
        es?.close();
        setConnected(false);
        let status = "";
        try {
          status = (JSON.parse((ev as MessageEvent<string>).data) as { status: string }).status;
        } catch {
          /* ignore */
        }
        endRef.current(status);
      });
      es.onerror = () => {
        es?.close();
        setConnected(false);
        if (!stopped) retry = window.setTimeout(open, 2500);
      };
    };
    open();
    return () => {
      stopped = true;
      window.clearTimeout(retry);
      es?.close();
    };
  }, [taskId, active]);

  return { events, connected, setEvents, lastId };
}

const QUIET_TYPES = new Set(["tool_event"]);

export function EventList({ events, compact }: { events: TaskEvent[]; compact?: boolean }) {
  const [showAll, setShowAll] = useState(false);
  const shown = compact && !showAll ? events.filter((e, i) => !QUIET_TYPES.has(e.type) || i >= events.length - 4) : events;
  const hidden = events.length - shown.length;
  return (
    <div className="events">
      {hidden > 0 && (
        <button className="link-btn tiny" onClick={() => setShowAll(true)}>
          Show {hidden} more tool event{hidden === 1 ? "" : "s"}
        </button>
      )}
      <ol className="event-list" aria-live="polite">
        {shown.map((e) => (
          <li key={e.id} className={`ev ev-${e.type}`}>
            <span className="ev-type">{e.type.replace(/_/g, " ")}</span>
            <span className="ev-msg">
              {e.message}
              {typeof e.data?.executor === "string" && e.type !== "tool_event" ? ` [${modeLabel(e.data.executor as string)}]` : ""}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

/** Executor that is running / ran, derived from events when the task row does not say yet. */
export function executorFromEvents(task: Task, events: TaskEvent[]): string {
  if (task.executor_used) return task.executor_used;
  for (let i = events.length - 1; i >= 0; i--) {
    const ex = events[i].data?.executor;
    if (typeof ex === "string" && ex) return ex;
  }
  return "";
}

export function isActive(s: string): boolean {
  return (ACTIVE_TASK_STATUSES as string[]).includes(s);
}

export function LiveDot({ connected }: { connected: boolean }) {
  return connected ? <span className="live-dot" title="Receiving live updates" aria-label="live" /> : <Spinner label="Connecting" />;
}
