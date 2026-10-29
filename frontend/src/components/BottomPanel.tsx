import { useMemo, useState } from "react";
import { api } from "../api";
import type { Build, Diagnostic, ProjectStatus, Task } from "../types";
import { fmtDuration, fmtTime, modeLabel, relTime, shortRev, useAsync } from "../util";
import { IconRetry } from "./Icons";
import { Badge, Empty, ErrorBox, Loading, Tabs, Tag } from "./ui";

export type BottomTab = "log" | "problems" | "tasks";

interface Props {
  pid: string;
  tab: BottomTab;
  onTab: (t: BottomTab) => void;
  status: ProjectStatus | undefined;
  /** Full record of the latest build (with log); fetched by the workspace. */
  build: Build | undefined;
  buildError: string | null;
  onJump: (path: string, line: number) => void;
  tasksKey: unknown;
}

export default function BottomPanel(p: Props) {
  const diags = p.build?.diagnostics ?? [];
  const errors = diags.filter((d) => d.severity === "error").length;
  const warnings = diags.filter((d) => d.severity === "warning").length;
  return (
    <div className="bottom-panel">
      <Tabs
        label="Output panels"
        value={p.tab}
        onChange={p.onTab}
        tabs={[
          { id: "log", label: "Build log" },
          {
            id: "problems",
            label: (
              <>
                Problems{" "}
                {errors + warnings > 0 && (
                  <span className={`count ${errors ? "bad" : "warn"}`} aria-label={`${errors} errors, ${warnings} warnings`} title={`${errors} errors, ${warnings} warnings`}>
                    {errors ? errors : warnings}
                  </span>
                )}
              </>
            ),
          },
          { id: "tasks", label: "Task history" },
        ]}
      />
      <div className="bottom-body" role="tabpanel">
        {p.tab === "log" && <BuildLog build={p.build} error={p.buildError} status={p.status} />}
        {p.tab === "problems" && <Problems diags={diags} build={p.build} onJump={p.onJump} />}
        {p.tab === "tasks" && <TaskHistory pid={p.pid} refreshKey={p.tasksKey} />}
      </div>
    </div>
  );
}

function BuildSummary({ b }: { b: Build }) {
  return (
    <div className="build-summary small">
      <Badge status={b.status} />
      <span>{fmtTime(b.created_at)}</span>
      <span>
        rev <code>{shortRev(b.revision)}</code>
      </span>
      <span>
        {b.engine} · {b.main_file}
      </span>
      {b.exit_code != null && <span>exit {b.exit_code}</span>}
      {b.duration_seconds != null && <span>{fmtDuration(b.duration_seconds)}</span>}
      {b.isolation && (
        <Tag tone={b.isolation === "bwrap" ? "muted" : "warn"} title="Compile isolation">
          isolation: {b.isolation}
        </Tag>
      )}
    </div>
  );
}

function BuildLog({ build, error, status }: { build: Build | undefined; error: string | null; status: ProjectStatus | undefined }) {
  if (error) return <ErrorBox error={error} />;
  if (!status?.last_build) return <Empty>No builds yet.</Empty>;
  if (!build) return <Loading text="Loading build log…" />;
  return (
    <div className="build-log">
      <BuildSummary b={build} />
      {build.error && (
        <div className={`error-box ${build.status === "blocked" ? "" : "soft"}`} role={build.status === "blocked" ? "alert" : undefined}>
          {build.status === "blocked" ? "Build blocked: " : ""}
          {build.error}
        </div>
      )}
      {build.status === "queued" || build.status === "running" ? (
        <p className="muted small">The log appears when the build finishes.</p>
      ) : build.log ? (
        <pre className="log" tabIndex={0} aria-label="Build log">
          {build.log}
        </pre>
      ) : (
        <p className="muted small">This build produced no log output.</p>
      )}
    </div>
  );
}

function Problems({ diags, build, onJump }: { diags: Diagnostic[]; build: Build | undefined; onJump: (path: string, line: number) => void }) {
  const [filter, setFilter] = useState<"all" | "error" | "warning" | "info">("all");
  const shown = useMemo(() => diags.filter((d) => filter === "all" || d.severity === filter), [diags, filter]);
  if (!build) return <Empty>No build yet.</Empty>;
  if (diags.length === 0)
    return <Empty>{build.status === "succeeded" ? "No problems reported by the last build." : "No diagnostics were parsed from the last build."}</Empty>;
  return (
    <div>
      <div className="row-actions small problems-filter" role="group" aria-label="Filter problems">
        {(["all", "error", "warning", "info"] as const).map((f) => (
          <button key={f} className={`seg-btn ${filter === f ? "active" : ""}`} aria-pressed={filter === f} onClick={() => setFilter(f)}>
            {f === "all" ? `All (${diags.length})` : `${f}s (${diags.filter((d) => d.severity === f).length})`}
          </button>
        ))}
      </div>
      <ul className="problems">
        {shown.map((d, i) => (
          <li key={i}>
            <button
              className="problem-row"
              disabled={!d.file}
              onClick={() => d.file && onJump(d.file, d.line ?? 1)}
              title={d.file ? `Go to ${d.file}${d.line ? `:${d.line}` : ""}` : "No file location reported"}
            >
              <span className={`sev sev-${d.severity}`}>{d.severity}</span>
              <span className="problem-msg">{d.message}</span>
              <span className="muted tiny">
                {d.file ?? "—"}
                {d.line ? `:${d.line}` : ""}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function TaskHistory({ pid, refreshKey }: { pid: string; refreshKey: unknown }) {
  const tasks = useAsync(() => api.tasks(pid), [pid, refreshKey]);
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div>
      <div className="row-actions">
        <span className="muted small">Most recent first.</span>
        <div className="spacer" />
        <button className="icon-btn sm" aria-label="Refresh task history" onClick={tasks.reload}>
          <IconRetry />
        </button>
      </div>
      <ErrorBox error={tasks.error} onRetry={tasks.reload} />
      {tasks.loading && !tasks.data && <Loading />}
      {tasks.data && tasks.data.length === 0 && <Empty>No AI tasks yet.</Empty>}
      <ul className="task-history">
        {tasks.data?.map((t) => (
          <li key={t.id}>
            <button className="task-row" aria-expanded={open === t.id} onClick={() => setOpen(open === t.id ? null : t.id)}>
              <Badge status={t.status} />
              <span className="ellipsis task-req">{t.request}</span>
              <span className="muted tiny">
                {modeLabel(t.mode)}
                {t.executor_used ? ` → ${modeLabel(t.executor_used)}` : ""} · {relTime(t.created_at)}
              </span>
              {t.demo && <Tag tone="warn">DEMO</Tag>}
            </button>
            {open === t.id && <TaskRuns task={t} />}
          </li>
        ))}
      </ul>
    </div>
  );
}

function TaskRuns({ task }: { task: Task }) {
  const d = useAsync(() => api.task(task.id), [task.id, task.status]);
  if (d.error) return <ErrorBox error={d.error} onRetry={d.reload} />;
  if (!d.data) return <Loading />;
  const t = d.data;
  return (
    <div className="task-detail small">
      <div className="kv">
        <span>Plan source</span>
        <span>{t.plan_source || "—"}</span>
        <span>Executor</span>
        <span>{t.executor_used ? modeLabel(t.executor_used) : "—"}</span>
        <span>Base revision</span>
        <span>
          <code>{shortRev(t.base_revision) || "—"}</code>
        </span>
        {t.retry_of && (
          <>
            <span>Retry of</span>
            <span>
              <code>{t.retry_of.slice(0, 8)}</code>
            </span>
          </>
        )}
        {t.error && (
          <>
            <span>Error</span>
            <span className="text-bad">{t.error}</span>
          </>
        )}
      </div>
      {t.tool_runs.length === 0 ? (
        <p className="muted">No external tool runs for this task.</p>
      ) : (
        <div className="table-wrap">
          <table className="table compact">
            <thead>
              <tr>
                <th>Executor</th>
                <th>Attempt</th>
                <th>Status</th>
                <th>Exit</th>
                <th>Isolation</th>
                <th>Violations</th>
                <th>Started</th>
              </tr>
            </thead>
            <tbody>
              {t.tool_runs.map((r) => (
                <tr key={r.id}>
                  <td>{modeLabel(r.executor)}</td>
                  <td>{r.attempt}</td>
                  <td>
                    <Badge status={r.status} />
                  </td>
                  <td>{r.exit_code ?? "—"}</td>
                  <td>
                    <Tag tone={r.isolation === "bwrap" ? "muted" : "warn"}>{r.isolation || "—"}</Tag>
                  </td>
                  <td>
                    {r.violations.length === 0
                      ? "none"
                      : r.violations.map((v, i) => (
                          <div key={i} className="text-bad">
                            {v.path}: {v.reason}
                          </div>
                        ))}
                  </td>
                  <td title={fmtTime(r.started_at)}>{relTime(r.started_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {t.tool_runs.some((r) => r.summary) && (
        <details>
          <summary>Tool summaries</summary>
          {t.tool_runs
            .filter((r) => r.summary)
            .map((r) => (
              <p key={r.id}>
                <strong>
                  {modeLabel(r.executor)} #{r.attempt}:
                </strong>{" "}
                <span className="pre-wrap">{r.summary}</span>
              </p>
            ))}
        </details>
      )}
    </div>
  );
}
