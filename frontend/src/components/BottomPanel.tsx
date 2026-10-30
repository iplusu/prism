import { useMemo, useState } from "react";
import { api } from "../api";
import type { Build, Diagnostic, ProjectStatus, Task } from "../types";
import { fmtDuration, fmtTime, modeLabel, relTime, shortRev, useAction, useAsync } from "../util";
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
  canEdit: boolean;
  /** Install the TeX package behind a missing file, then recompile. */
  onInstallPackage: (file: string) => Promise<void>;
  /** A task was created from here: refresh the AI panel and task lists. */
  onTaskCreated: () => void;
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
        {p.tab === "log" && <BuildLog pid={p.pid} build={p.build} error={p.buildError} status={p.status} canEdit={p.canEdit} onInstall={p.onInstallPackage} onTaskCreated={p.onTaskCreated} />}
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

/** What a failed build offers: install a missing TeX package, or hand the errors to the AI. */
function FailedBuildHelp({ pid, build, canEdit, onInstall, onTaskCreated }: { pid: string; build: Build; canEdit: boolean; onInstall: (file: string) => Promise<void>; onTaskCreated: () => void }) {
  const missing = build.missing_files ?? [];
  const errors = build.diagnostics.filter((d) => d.severity === "error");
  const [sent, setSent] = useState(false);
  const install = useAction(async (file: string) => onInstall(file));
  const fix = useAction(async () => {
    const lines = errors.slice(0, 12).map((d) => `- ${d.file ?? "?"}${d.line ? `:${d.line}` : ""}: ${d.message}`).join("\n");
    const tail = (build.log ?? "").split("\n").slice(-40).join("\n");
    await api.createTask(
      pid,
      `The last compile of ${build.main_file} failed. Fix the LaTeX source so that it compiles, changing as little as possible and never removing content.\n\nErrors reported:\n${lines || "(none parsed)"}\n\nEnd of the build log:\n${tail}`,
      "auto",
      { scope: "project" },
    );
    setSent(true);
    onTaskCreated();
  });
  if (build.status !== "failed" || !canEdit) return null;
  return (
    <div className="build-help small" role="group" aria-label="Fix this failure">
      {missing.map((f) => (
        <div key={f} className="build-help-row">
          <span>
            Missing LaTeX file <code>{f}</code>: it belongs to a TeX package that is not installed on this server.
          </span>
          <button className="btn btn-sm btn-primary" disabled={install.busy} onClick={() => void install.run(f)}>
            {install.busy ? "Installing…" : "Install package and recompile"}
          </button>
        </div>
      ))}
      {missing.length === 0 && errors.length > 0 && (
        <div className="build-help-row">
          <span>
            {errors.length} error{errors.length === 1 ? "" : "s"}. The AI can propose a fix; you review and apply it as usual.
          </span>
          <button className="btn btn-sm" disabled={fix.busy} onClick={() => void fix.run()}>
            {fix.busy ? "Sending…" : "Fix with AI"}
          </button>
        </div>
      )}
      {sent && !fix.error && <div className="muted tiny">Task created: follow it in the AI panel or Task history.</div>}
      <ErrorBox error={install.error || fix.error} />
    </div>
  );
}

function BuildLog({ pid, build, error, status, canEdit, onInstall, onTaskCreated }: { pid: string; build: Build | undefined; error: string | null; status: ProjectStatus | undefined; canEdit: boolean; onInstall: (file: string) => Promise<void>; onTaskCreated: () => void }) {
  if (error) return <ErrorBox error={error} />;
  if (!status?.last_build) return <Empty>No builds yet.</Empty>;
  if (!build) return <Loading text="Loading build log…" />;
  return (
    <div className="build-log">
      <BuildSummary b={build} />
      <FailedBuildHelp pid={pid} build={build} canEdit={canEdit} onInstall={onInstall} onTaskCreated={onTaskCreated} />
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
