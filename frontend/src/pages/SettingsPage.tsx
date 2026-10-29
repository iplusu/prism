import { useEffect, useState, type FormEvent } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { useApp } from "../appContext";
import { AppHeader } from "../components/AppHeader";
import { IconBack, IconDown, IconRetry, IconUp } from "../components/Icons";
import { Badge, ConfirmButton, Empty, ErrorBox, Loading, Tabs, Tag } from "../components/ui";
import type { AgentPolicy, LlmTestReport, Project, Role, SystemStatus, ToolHealth } from "../types";
import { fmtTime, modeLabel, useAction, useAsync, type AsyncState } from "../util";

type Tab = "llm" | "tools" | "project" | "members" | "system";
const EXECUTORS = ["codex", "claude", "antigravity"];

export default function SettingsPage() {
  const { pid } = useParams();
  const { user } = useApp();
  const [params, setParams] = useSearchParams();
  const system = useAsync(() => api.system(), []);
  const project = useAsync(async () => (pid ? api.project(pid) : null), [pid]);

  const tabs: { id: Tab; label: string }[] = [
    ...(pid ? [{ id: "project" as Tab, label: "Project" }] : []),
    ...(pid || user.is_admin ? [{ id: "members" as Tab, label: pid ? "Members & roles" : "Users" }] : []),
    { id: "llm", label: "Orchestrator LLM" },
    { id: "tools", label: "Tools" },
    { id: "system", label: "System" },
  ];
  const requested = params.get("tab") as Tab | null;
  const tab: Tab = tabs.some((t) => t.id === requested) ? requested! : tabs[0].id;

  useEffect(() => {
    document.title = `Settings · Prism`;
  }, []);

  return (
    <div className="page">
      <AppHeader
        title={
          <span className="row-actions">
            {pid && (
              <>
                <Link to={`/p/${pid}`} className="btn btn-ghost btn-sm">
                  <IconBack /> {project.data?.name ?? "Workspace"}
                </Link>
                <span className="muted">/</span>
              </>
            )}
            <strong>Settings</strong>
          </span>
        }
      />
      <main className="page-main settings">
        <Tabs label="Settings sections" value={tab} onChange={(t) => setParams({ tab: t }, { replace: true })} tabs={tabs} />
        <div className="settings-body" role="tabpanel">
          {tab === "llm" && <LlmTab system={system} />}
          {tab === "tools" && <ToolsTab system={system} isAdmin={user.is_admin} />}
          {tab === "project" && pid && <ProjectTab pid={pid} project={project as AsyncState<Project | null>} system={system.data} />}
          {tab === "members" && <MembersTab pid={pid} project={project as AsyncState<Project | null>} />}
          {tab === "system" && <SystemTab system={system} />}
        </div>
      </main>
    </div>
  );
}

// -------------------------------------------------------------- LLM tab ---

function YesNo({ v }: { v: boolean | undefined }) {
  return v ? <Badge status="passed">yes</Badge> : <Badge status="not_evaluated">no</Badge>;
}

function LlmTestView({ r }: { r: LlmTestReport }) {
  const c = r.capabilities ?? {};
  return (
    <div className="llm-test">
      <p className="small">
        {r.ok ? <Badge status="passed">connection OK</Badge> : <Badge status="failed">connection problem</Badge>} tested {fmtTime(r.tested_at)}
        {r.base_url ? (
          <>
            {" "}against <code>{r.base_url}</code> with model <code>{r.model || "(not set)"}</code>
          </>
        ) : (
          " (no endpoint configured)"
        )}
        {r.served_model_id && r.served_model_id !== r.model ? (
          <>
            {" "}
            (server reported <code>{r.served_model_id}</code>)
          </>
        ) : null}
        {r.error_kind ? ` · error kind: ${r.error_kind}` : ""}
      </p>
      <div className="table-wrap">
        <table className="table compact">
          <thead>
            <tr>
              <th>Step</th>
              <th>Status</th>
              <th>Detail</th>
              <th>Time</th>
            </tr>
          </thead>
          <tbody>
            {r.steps.map((s) => (
              <tr key={s.name}>
                <td>{s.name.replace(/_/g, " ")}</td>
                <td>
                  <Badge status={s.status} />
                </td>
                <td className="pre-wrap small">{s.detail || "—"}</td>
                <td className="small">{s.duration_ms != null ? `${s.duration_ms} ms` : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {Object.keys(c).length > 0 && (
        <div className="kv small">
          <span>Tool calling</span>
          <YesNo v={c.tool_calling} />
          <span>JSON output</span>
          <span>
            json_object <YesNo v={c.json_object} /> json_schema <YesNo v={c.json_schema} />
          </span>
          <span>Streaming</span>
          <YesNo v={c.streaming} />
          {c.planning_mode && (
            <>
              <span>Planning mode used</span>
              <code>{c.planning_mode}</code>
            </>
          )}
        </div>
      )}
      {r.models && r.models.length > 0 && <p className="small muted">Models served: {r.models.join(", ")}</p>}
      {r.context_reported !== undefined && (
        <p className="small muted">Context length reported: {typeof r.context_reported === "string" ? r.context_reported : JSON.stringify(r.context_reported)}</p>
      )}
    </div>
  );
}

function LlmTab({ system }: { system: AsyncState<SystemStatus> }) {
  const [result, setResult] = useState<LlmTestReport | null>(null);
  const test = useAction(async () => {
    setResult(await api.testLlm());
    void system.reload();
  });
  if (system.error) return <ErrorBox error={system.error} onRetry={system.reload} />;
  if (!system.data) return <Loading />;
  const l = system.data.llm;
  const shown = result ?? system.data.llm_last_test;
  return (
    <section>
      <h2>Orchestrator LLM</h2>
      <p className="muted small">
        The orchestrator model plans each AI request and reviews verification reports. It runs on a separate inference server that only the Prism
        backend talks to — your browser never contacts it. Values are read from the server environment variables <code>LLM_BASE_URL</code>,{" "}
        <code>LLM_MODEL</code> and <code>LLM_API_KEY</code> (plus <code>LLM_PROVIDER</code>) and cannot be changed here.
      </p>
      <div className="kv">
        <span>Provider</span>
        <span>{l.provider || "—"}</span>
        <span>Base URL</span>
        <code>{l.base_url || "(not set)"}</code>
        <span>Model</span>
        <code>{l.model || "(not set)"}</code>
        <span>API key</span>
        <span>{l.api_key_set ? <Badge status="passed">set (hidden)</Badge> : <Badge status="not_evaluated">not set</Badge>}</span>
        <span>Configured</span>
        <YesNo v={l.configured} />
        <span>Timeout</span>
        <span>{l.timeout_seconds} s</span>
        <span>Context budget</span>
        <span>{l.context_chars.toLocaleString()} characters</span>
        <span>Planning mode setting</span>
        <code>{l.planning_mode_setting}</code>
      </div>
      <div className="row-actions">
        <button className="btn btn-primary" onClick={() => test.run()} disabled={test.busy}>
          {test.busy ? "Testing… (this can take a minute)" : "Test connection"}
        </button>
      </div>
      <ErrorBox error={test.error} />
      {shown ? (
        <>
          <h3>{result ? "Test result" : "Last recorded test"}</h3>
          <LlmTestView r={shown} />
        </>
      ) : (
        <p className="muted small">No connection test has been recorded for this endpoint and model yet.</p>
      )}
    </section>
  );
}

// ------------------------------------------------------------ Tools tab ---

function ToolsTab({ system, isAdmin }: { system: AsyncState<SystemStatus>; isAdmin: boolean }) {
  const [tools, setTools] = useState<ToolHealth[] | null>(null);
  const refresh = useAction(async () => setTools(await api.refreshTools()));
  if (system.error) return <ErrorBox error={system.error} onRetry={system.reload} />;
  if (!system.data) return <Loading />;
  const list = tools ?? system.data.tools;
  const ts = system.data.tool_sandbox;
  return (
    <section>
      <div className="row-actions">
        <h2>Coding tools</h2>
        <div className="spacer" />
        {isAdmin && (
          <button className="btn" onClick={() => refresh.run()} disabled={refresh.busy}>
            <IconRetry /> {refresh.busy ? "Checking…" : "Re-check tools"}
          </button>
        )}
      </div>
      <p className="muted small">
        External tools run in an isolated copy of the project and their changes always come back as proposals. Health is cached for about a minute.
        Tool sandbox: <code>{ts.mode}</code> (bubblewrap {ts.bwrap ? "working" : "not working"}
        {ts.allow_unsandboxed ? "; unsandboxed runs allowed" : ""}).
      </p>
      <ErrorBox error={refresh.error} />
      {list.length === 0 ? (
        <Empty>No tool adapters are registered.</Empty>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Tool</th>
                <th>Status</th>
                <th>Installed</th>
                <th>Version</th>
                <th>Auth</th>
                <th>Detail</th>
                <th>Capabilities</th>
              </tr>
            </thead>
            <tbody>
              {list.map((t) => (
                <tr key={t.name}>
                  <td>
                    <strong>{t.label || modeLabel(t.name)}</strong>
                    <div className="muted tiny">{t.name}</div>
                  </td>
                  <td>
                    <Badge status={t.status} />
                  </td>
                  <td>{t.installed ? "yes" : "no"}</td>
                  <td className="small">{t.version || "—"}</td>
                  <td className="small">{t.auth}</td>
                  <td className="small pre-wrap">{t.detail || "—"}</td>
                  <td className="small">
                    {Object.entries(t.capabilities ?? {}).map(([k, v]) => (
                      <Tag key={k} tone={v === false ? "muted" : "info"}>
                        {k.replace(/_/g, " ")}
                        {typeof v === "boolean" ? (v ? "" : ": no") : `: ${String(v)}`}
                      </Tag>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------- Project tab ---

function ProjectTab({ pid, project, system }: { pid: string; project: AsyncState<Project | null>; system: SystemStatus | undefined }) {
  const files = useAsync(() => api.files(pid), [pid]);
  if (project.error) return <ErrorBox error={project.error} onRetry={project.reload} />;
  if (!project.data) return <Loading />;
  const p = project.data;
  const canEdit = p.role === "owner" || p.role === "editor";
  const texFiles = (files.data ?? []).filter((f) => f.path.endsWith(".tex")).map((f) => f.path);
  return (
    <>
      <ProjectForm key={p.updated_at} p={p} texFiles={texFiles} canEdit={canEdit} onSaved={(np) => project.setData(np)} />
      <ErrorBox error={files.error} />
      <PolicyForm key={`pol-${p.updated_at}`} p={p} system={system} canEdit={p.role === "owner"} onSaved={(np) => project.setData(np)} />
    </>
  );
}

function ProjectForm({ p, texFiles, canEdit, onSaved }: { p: Project; texFiles: string[]; canEdit: boolean; onSaved: (p: Project) => void }) {
  const [name, setName] = useState(p.name);
  const [description, setDescription] = useState(p.description);
  const [main, setMain] = useState(p.main_file);
  const [engine, setEngine] = useState(p.engine);
  const [saved, setSaved] = useState(false);
  const act = useAction(async () => {
    const np = await api.patchProject(p.id, { name: name.trim(), description, main_file: main, engine });
    setSaved(true);
    onSaved(np);
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    setSaved(false);
    void act.run();
  }
  const mainOptions = texFiles.includes(main) ? texFiles : [main, ...texFiles];
  return (
    <section>
      <h2>Project</h2>
      {!canEdit && <p className="muted small">You have read-only access to this project.</p>}
      <form onSubmit={submit} className="form">
        <fieldset disabled={!canEdit}>
          <label className="field">
            <span>Name</span>
            <input required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="field">
            <span>Description</span>
            <textarea rows={2} maxLength={2000} value={description} onChange={(e) => setDescription(e.target.value)} />
          </label>
          <div className="field-row">
            <label className="field">
              <span>Main file</span>
              <select value={main} onChange={(e) => setMain(e.target.value)}>
                {mainOptions.map((f) => (
                  <option key={f} value={f}>
                    {f}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>LaTeX engine</span>
              <select value={engine} onChange={(e) => setEngine(e.target.value)}>
                <option value="pdflatex">pdfLaTeX</option>
                <option value="xelatex">XeLaTeX</option>
                <option value="lualatex">LuaLaTeX</option>
              </select>
            </label>
          </div>
          {canEdit && (
            <div className="form-actions start">
              <button className="btn btn-primary" type="submit" disabled={act.busy}>
                {act.busy ? "Saving…" : "Save project settings"}
              </button>
              {saved && !act.busy && <span className="text-ok small">Saved.</span>}
            </div>
          )}
        </fieldset>
        <ErrorBox error={act.error} />
      </form>
    </section>
  );
}

function PolicyForm({ p, system, canEdit, onSaved }: { p: Project; system: SystemStatus | undefined; canEdit: boolean; onSaved: (p: Project) => void }) {
  const pol = p.policy;
  const [allowExternal, setAllowExternal] = useState(pol?.allow_external ?? true);
  const [allowed, setAllowed] = useState<string[]>((pol?.allowed_executors ?? EXECUTORS).filter((e) => EXECUTORS.includes(e)));
  const [def, setDef] = useState(pol?.default_executor ?? "auto");
  const [order, setOrder] = useState<string[]>(() => {
    const o = (pol?.preference_order ?? []).filter((e) => EXECUTORS.includes(e));
    return [...o, ...EXECUTORS.filter((e) => !o.includes(e))];
  });
  const [runT, setRunT] = useState(pol?.run_timeout_seconds ?? 600);
  const [taskT, setTaskT] = useState(pol?.task_timeout_seconds ?? 1500);
  const [attempts, setAttempts] = useState(pol?.max_attempts ?? 2);
  const [saved, setSaved] = useState(false);
  const lim = system?.limits;
  const act = useAction(async () => {
    const body: AgentPolicy = {
      allow_external: allowExternal,
      allowed_executors: allowed,
      default_executor: def,
      preference_order: order,
      routing: pol?.routing ?? {},
      run_timeout_seconds: runT,
      task_timeout_seconds: taskT,
      max_attempts: attempts,
    };
    onSaved(await api.putPolicy(p.id, body));
    setSaved(true);
  });
  const move = (i: number, d: -1 | 1) =>
    setOrder((o) => {
      const n = [...o];
      const j = i + d;
      if (j < 0 || j >= n.length) return o;
      [n[i], n[j]] = [n[j], n[i]];
      return n;
    });
  return (
    <section>
      <h2>AI agent policy</h2>
      <p className="muted small">
        Controls which external coding tools may work on this project and their budgets. {canEdit ? "" : "Only project owners can change it."}
        {lim && (
          <>
            {" "}
            Server ceilings always apply: run ≤ {lim.run_timeout_seconds} s, task ≤ {lim.task_timeout_seconds} s, attempts ≤ {lim.max_attempts}.
          </>
        )}
      </p>
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          setSaved(false);
          void act.run();
        }}
      >
        <fieldset disabled={!canEdit}>
          <label className="check">
            <input type="checkbox" checked={allowExternal} onChange={(e) => setAllowExternal(e.target.checked)} /> Allow external tools in Auto mode
          </label>
          <fieldset className="field">
            <legend>Allowed executors</legend>
            {EXECUTORS.map((e) => (
              <label key={e} className="check">
                <input
                  type="checkbox"
                  checked={allowed.includes(e)}
                  onChange={(ev) => setAllowed((a) => (ev.target.checked ? [...a, e] : a.filter((x) => x !== e)))}
                />{" "}
                {modeLabel(e)}
              </label>
            ))}
          </fieldset>
          <label className="field">
            <span>Default tool in the AI panel</span>
            <select value={def} onChange={(e) => setDef(e.target.value)}>
              {["auto", "llm", ...EXECUTORS].map((m) => (
                <option key={m} value={m}>
                  {modeLabel(m)}
                </option>
              ))}
            </select>
          </label>
          <fieldset className="field">
            <legend>Preference order for Auto</legend>
            <ol className="order-list">
              {order.map((e, i) => (
                <li key={e}>
                  <span>{modeLabel(e)}</span>
                  {!allowed.includes(e) && <span className="muted tiny">(not allowed)</span>}
                  <span className="spacer" />
                  <button type="button" className="icon-btn sm" aria-label={`Move ${modeLabel(e)} up`} disabled={i === 0} onClick={() => move(i, -1)}>
                    <IconUp />
                  </button>
                  <button
                    type="button"
                    className="icon-btn sm"
                    aria-label={`Move ${modeLabel(e)} down`}
                    disabled={i === order.length - 1}
                    onClick={() => move(i, 1)}
                  >
                    <IconDown />
                  </button>
                </li>
              ))}
            </ol>
          </fieldset>
          <div className="field-row">
            <label className="field">
              <span>Run timeout (s)</span>
              <input type="number" min={30} max={lim?.run_timeout_seconds} value={runT} onChange={(e) => setRunT(Number(e.target.value))} />
            </label>
            <label className="field">
              <span>Task timeout (s)</span>
              <input type="number" min={60} max={lim?.task_timeout_seconds} value={taskT} onChange={(e) => setTaskT(Number(e.target.value))} />
            </label>
            <label className="field">
              <span>Max attempts</span>
              <input type="number" min={1} max={Math.min(5, lim?.max_attempts ?? 5)} value={attempts} onChange={(e) => setAttempts(Number(e.target.value))} />
            </label>
          </div>
          {pol && Object.keys(pol.routing ?? {}).length > 0 && (
            <p className="muted small">Per-task-kind routing is configured ({Object.keys(pol.routing).join(", ")}) and is kept unchanged.</p>
          )}
          {canEdit && (
            <div className="form-actions start">
              <button className="btn btn-primary" type="submit" disabled={act.busy}>
                {act.busy ? "Saving…" : "Save policy"}
              </button>
              {saved && !act.busy && <span className="text-ok small">Saved (values above server ceilings were lowered).</span>}
            </div>
          )}
        </fieldset>
        <ErrorBox error={act.error} />
      </form>
    </section>
  );
}

// ---------------------------------------------------------- Members tab ---

function MembersTab({ pid, project }: { pid: string | undefined; project: AsyncState<Project | null> }) {
  const { user } = useApp();
  return (
    <>
      {pid && <ProjectMembers pid={pid} project={project} />}
      {user.is_admin && <CreateUser />}
    </>
  );
}

function ProjectMembers({ pid, project }: { pid: string; project: AsyncState<Project | null> }) {
  const users = useAsync(() => api.users(), []);
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<Role>("editor");
  const add = useAction(async (u: string, r: Role) => {
    project.setData(await api.addMember(pid, u, r));
    setUsername("");
  });
  const remove = useAction(async (uid: string) => project.setData(await api.removeMember(pid, uid)));
  if (project.error) return <ErrorBox error={project.error} onRetry={project.reload} />;
  if (!project.data) return <Loading />;
  const p = project.data;
  const isOwner = p.role === "owner";
  const members = p.members ?? [];
  return (
    <section>
      <h2>Members &amp; roles</h2>
      <p className="muted small">
        Owners manage members and the AI policy; editors edit, compile and run AI tasks; viewers can read, download and review.
        {!isOwner && " Only owners can change membership."}
      </p>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>User</th>
              <th>Role</th>
              <th>
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.user.id}>
                <td>
                  {m.user.display_name} <span className="muted small">@{m.user.username}</span>
                  {m.user.is_admin && <Tag tone="accent">admin</Tag>}
                </td>
                <td>
                  {isOwner ? (
                    <select
                      aria-label={`Role of ${m.user.username}`}
                      value={m.role}
                      onChange={(e) => void add.run(m.user.username, e.target.value as Role)}
                      disabled={add.busy}
                    >
                      <option value="owner">owner</option>
                      <option value="editor">editor</option>
                      <option value="viewer">viewer</option>
                    </select>
                  ) : (
                    <Badge status={m.role} />
                  )}
                </td>
                <td className="row-actions">
                  {isOwner && (
                    <ConfirmButton
                      label="Remove"
                      question={`Remove ${m.user.username} from the project?`}
                      confirmLabel="Remove"
                      onConfirm={() => remove.run(m.user.id)}
                    />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ErrorBox error={add.error || remove.error} />
      {isOwner && (
        <form
          className="form inline-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (username.trim()) void add.run(username.trim(), role);
          }}
        >
          <label className="field">
            <span>Add member by username</span>
            <input list="known-users" value={username} onChange={(e) => setUsername(e.target.value)} required />
            <datalist id="known-users">
              {(users.data ?? [])
                .filter((u) => !members.some((m) => m.user.id === u.id))
                .map((u) => (
                  <option key={u.id} value={u.username}>
                    {u.display_name}
                  </option>
                ))}
            </datalist>
          </label>
          <label className="field">
            <span>Role</span>
            <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
              <option value="editor">editor</option>
              <option value="viewer">viewer</option>
              <option value="owner">owner</option>
            </select>
          </label>
          <button className="btn btn-primary" type="submit" disabled={add.busy || !username.trim()}>
            Add
          </button>
        </form>
      )}
    </section>
  );
}

function CreateUser() {
  const users = useAsync(() => api.users(), []);
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [created, setCreated] = useState<string | null>(null);
  const act = useAction(async () => {
    const u = await api.createUser({ username: username.trim(), password, display_name: displayName.trim(), is_admin: isAdmin });
    setCreated(u.username);
    setUsername("");
    setDisplayName("");
    setPassword("");
    setIsAdmin(false);
    void users.reload();
  });
  return (
    <section>
      <h2>Create user (administrator)</h2>
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          setCreated(null);
          void act.run();
        }}
      >
        <div className="field-row">
          <label className="field">
            <span>Username</span>
            <input required minLength={2} maxLength={64} pattern="[A-Za-z0-9_.\-]{2,64}" value={username} onChange={(e) => setUsername(e.target.value)} />
          </label>
          <label className="field">
            <span>Display name</span>
            <input maxLength={128} value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
          </label>
          <label className="field">
            <span>Initial password</span>
            <input type="password" autoComplete="new-password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
        </div>
        <label className="check">
          <input type="checkbox" checked={isAdmin} onChange={(e) => setIsAdmin(e.target.checked)} /> Administrator
        </label>
        <div className="form-actions start">
          <button className="btn btn-primary" type="submit" disabled={act.busy}>
            Create user
          </button>
          {created && <span className="text-ok small">Created “{created}”. Add them to projects from each project's Members tab.</span>}
        </div>
        <ErrorBox error={act.error} />
      </form>
      {users.data && (
        <details>
          <summary>All users ({users.data.length})</summary>
          <ul className="plain-list small">
            {users.data.map((u) => (
              <li key={u.id}>
                {u.display_name} <span className="muted">@{u.username}</span> {u.is_admin && <Tag tone="accent">admin</Tag>}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

// ----------------------------------------------------------- System tab ---

function SystemTab({ system }: { system: AsyncState<SystemStatus> }) {
  if (system.error) return <ErrorBox error={system.error} onRetry={system.reload} />;
  if (!system.data) return <Loading />;
  const s = system.data;
  return (
    <section>
      <div className="row-actions">
        <h2>System status</h2>
        <div className="spacer" />
        <button className="btn btn-sm" onClick={system.reload} disabled={system.loading}>
          <IconRetry /> Refresh
        </button>
      </div>
      <div className="kv">
        <span>Compile sandbox</span>
        <span>
          <Badge status={s.compile_sandbox.mode === "blocked" ? "failed" : s.compile_sandbox.isolated ? "passed" : "needs_auth"}>{s.compile_sandbox.mode}</Badge>{" "}
          {s.compile_sandbox.isolated ? "isolated" : "NOT isolated"} — <span className="small">{s.compile_sandbox.detail}</span>
        </span>
        <span>Tool sandbox</span>
        <span>
          <code>{s.tool_sandbox.mode}</code> · bubblewrap {s.tool_sandbox.bwrap ? "works" : "unavailable"}
          {s.tool_sandbox.allow_unsandboxed ? " · unsandboxed tool runs ALLOWED" : ""}
          {s.tool_sandbox.detail && <div className="small muted">{s.tool_sandbox.detail}</div>}
        </span>
        <span>TeX</span>
        <span>
          <Badge status={s.tex.available ? "available" : "unavailable"} /> <span className="small">{s.tex.detail}</span>
          {s.tex.bin_dir && (
            <div className="small muted">
              <code>{s.tex.bin_dir}</code>
            </div>
          )}
        </span>
        <span>Collaboration</span>
        <span>
          {s.collab.enabled ? <Badge status={s.collab.status === "ok" ? "available" : "error"}>{s.collab.status}</Badge> : <Badge status="unavailable">disabled</Badge>}
          {!s.collab.enabled && <span className="small muted"> — editing uses autosave with conflict detection</span>}
        </span>
        <span>Demo mode</span>
        <span>{s.demo_mode ? <Tag tone="warn">on</Tag> : "off"}</span>
        <span>Agent limits</span>
        <span className="small">
          run ≤ {s.limits.run_timeout_seconds} s · task ≤ {s.limits.task_timeout_seconds} s · attempts ≤ {s.limits.max_attempts}
        </span>
        <span>Compile limits</span>
        <span className="small">
          {s.limits.compile_timeout_seconds} s · {s.limits.compile_memory_mb} MB
        </span>
        <span>Default tool order</span>
        <code>{s.agent_default_order || "—"}</code>
      </div>
    </section>
  );
}
