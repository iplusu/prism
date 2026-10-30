import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import type { ChatMessage, Proposal, Task, TaskContextSpec, TaskMode } from "../types";
import { MODE_LABELS, modeLabel, relTime, useAction, useAsync } from "../util";
import type { SelectionInfo } from "./CodeEditor";
import { IconReview, IconRetry, IconStop } from "./Icons";
import { EventList, executorFromEvents, isActive, LiveDot, useTaskStream } from "./TaskProgress";
import { Badge, Empty, ErrorBox, Loading, Tag } from "./ui";

type Scope = TaskContextSpec["scope"];

interface Props {
  pid: string;
  canEdit: boolean;
  demoMode: boolean;
  defaultMode: string | undefined;
  selection: SelectionInfo | null;
  /** Set by the editor's "Ask AI" button: pins that selection as the target and focuses the request box. */
  ask: { sel: SelectionInfo; nonce: number } | null;
  activePath: string | null;
  /** Changes whenever the project status poll sees different tasks/proposals. */
  refreshKey: string;
  onTaskChange: () => void;
}

// Analysis presets. They run with answer_only: the server never lets them edit files or call a tool.
const PRESETS: { id: string; label: string; title: string; prompt: string }[] = [
  {
    id: "review",
    label: "Reviewer report",
    title: "A critical but fair peer-review of the manuscript. Answers only; changes nothing.",
    prompt:
      "Review this manuscript as a critical but fair peer reviewer. Give: a two-sentence summary of the claimed contribution; the main strengths; the main weaknesses, each tied to a file and line; questions for the authors; and presentation problems (unclear definitions, notation, figures). Use only what is in the project text. Do not invent related work or citations. Do not change any file. Reply in the language of the manuscript.",
  },
  {
    id: "terms",
    label: "Terminology",
    title: "Finds names, spellings and symbols used inconsistently. Answers only; changes nothing.",
    prompt:
      "Find terminology and notation that is used inconsistently across the project: different names or spellings for the same concept, one symbol used for two things, hyphenation or capitalization variants. For each case list the variants with file and line, and recommend one canonical form. Do not change any file. Reply in the language of the manuscript.",
  },
  {
    id: "cites",
    label: "Missing citations",
    title: "Finds claims that need a citation. Never invents references. Answers only; changes nothing.",
    prompt:
      "Find statements that present prior work, established facts or comparisons without a \\cite, or that cite a key missing from the bibliography. Quote each sentence with file and line, say why a citation is needed, and suggest search keywords. Do NOT invent references, authors, titles or DOIs. Do not change any file. Reply in the language of the manuscript.",
  },
];

const MODES: TaskMode[] = ["auto", "llm", "codex", "claude", "antigravity"];

export default function AiPanel(p: Props) {
  const messages = useAsync(() => api.messages(p.pid), [p.pid]);
  const tasks = useAsync(() => api.tasks(p.pid, 100), [p.pid]);
  const proposals = useAsync(() => api.proposals(p.pid, "all"), [p.pid, p.refreshKey]);
  const [text, setText] = useState("");
  const modes = useMemo(() => (p.demoMode ? [...MODES, "demo" as TaskMode] : MODES), [p.demoMode]);
  const [mode, setMode] = useState<TaskMode>("auto");
  const [scope, setScope] = useState<Scope>("project");
  const listRef = useRef<HTMLDivElement>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const [pinned, setPinned] = useState<SelectionInfo | null>(null);
  const navigate = useNavigate();

  // Project default executor (policy) preselects the tool once known.
  useEffect(() => {
    if (p.defaultMode && (modes as string[]).includes(p.defaultMode)) setMode(p.defaultMode as TaskMode);
  }, [p.defaultMode, modes]);

  // Reload tasks/messages when the status poll shows a change made elsewhere.
  useEffect(() => {
    void tasks.reload();
    void messages.reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.refreshKey]);

  const taskById = useMemo(() => new Map((tasks.data ?? []).map((t) => [t.id, t])), [tasks.data]);
  const propById = useMemo(() => new Map((proposals.data ?? []).map((x) => [x.id, x])), [proposals.data]);

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.data?.length]);

  const refreshAll = () => {
    void messages.reload();
    void tasks.reload();
    void proposals.reload();
    p.onTaskChange();
  };

  useEffect(() => {
    if (!p.ask) return;
    setPinned(p.ask.sel);
    setScope("selection");
    textRef.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.ask?.nonce]);

  const sel = pinned ?? p.selection;
  const effectiveScope: Scope = scope === "selection" && !sel ? "project" : scope === "file" && !p.activePath ? "project" : scope;

  const send = useAction(async () => {
    const ctx: TaskContextSpec =
      effectiveScope === "selection" && sel
        ? {
            scope: "selection",
            path: sel.path,
            selection: { from_line: sel.from_line, to_line: sel.to_line, text: sel.text },
          }
        : effectiveScope === "file"
          ? { scope: "file", path: p.activePath }
          : { scope: "project" };
    const t = await api.createTask(p.pid, text.trim(), mode, ctx);
    setText("");
    setPinned(null);
    tasks.setData([t, ...(tasks.data ?? [])]);
    refreshAll();
  });

  const preset = useAction(async (prompt: string) => {
    const t = await api.createTask(p.pid, prompt, "llm", { scope: "project", answer_only: true });
    tasks.setData([t, ...(tasks.data ?? [])]);
    refreshAll();
  });

  function submit(e?: FormEvent) {
    e?.preventDefault();
    if (text.trim() && !send.busy) void send.run();
  }

  return (
    <div className="ai-panel">
      <div className="panel-head">
        <span className="side-title">AI assistant</span>
        <div className="spacer" />
        <button className="icon-btn sm" aria-label="Refresh conversation" onClick={refreshAll}>
          <IconRetry />
        </button>
      </div>
      <div className="ai-messages" ref={listRef}>
        <ErrorBox error={messages.error} onRetry={messages.reload} />
        {messages.loading && !messages.data && <Loading text="Loading conversation…" />}
        {messages.data && messages.data.length === 0 && (
          <Empty>
            <p>Ask for an edit, a review, or a question about the paper.</p>
            <p className="muted small">
              Changes are never written directly: they arrive as proposals that are compiled and checked, and that you review before
              applying.
            </p>
          </Empty>
        )}
        {messages.data?.map((m) => (
          <MessageItem
            key={m.id}
            m={m}
            task={m.task_id ? taskById.get(m.task_id) : undefined}
            proposal={typeof m.meta?.proposal_id === "string" ? propById.get(m.meta.proposal_id as string) : undefined}
            canEdit={p.canEdit}
            onChanged={refreshAll}
            onReview={(id) => navigate(`/p/${p.pid}/review/${id}`)}
          />
        ))}
      </div>
      {p.canEdit ? (
        <form className="ai-input" onSubmit={submit}>
          <div className="presets" role="group" aria-label="Analysis presets (answer only, nothing is changed)">
            {PRESETS.map((x) => (
              <button type="button" key={x.id} className="btn btn-sm" title={x.title} disabled={send.busy || preset.busy} onClick={() => void preset.run(x.prompt)}>
                {x.label}
              </button>
            ))}
          </div>
          <ErrorBox error={preset.error} />
          <fieldset className="ctx-select">
            <legend className="sr-only">Context</legend>
            <span className="tiny muted">Context</span>
            <div className="seg" role="radiogroup" aria-label="Context sent with the request">
              {(
                [
                  ["selection", "Selection", !sel],
                  ["file", "Current file", !p.activePath],
                  ["project", "Project", false],
                ] as [Scope, string, boolean][]
              ).map(([id, label, disabled]) => (
                <button
                  type="button"
                  key={id}
                  role="radio"
                  aria-checked={effectiveScope === id}
                  className={`seg-btn ${effectiveScope === id ? "active" : ""}`}
                  disabled={disabled}
                  title={disabled ? (id === "selection" ? "Select text in the editor first" : "Open a file first") : undefined}
                  onClick={() => {
                    setScope(id);
                    if (id !== "selection") setPinned(null);
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          </fieldset>
          <div className="tiny muted ctx-desc" aria-live="polite">
            {effectiveScope === "selection" && sel
              ? `${sel.path}, lines ${sel.from_line}–${sel.to_line} (${sel.text.length} chars)`
              : effectiveScope === "file"
                ? p.activePath
                : "Whole project"}
          </div>
          <label className="sr-only" htmlFor="ai-text">
            Request
          </label>
          <textarea
            id="ai-text"
            ref={textRef}
            rows={3}
            maxLength={20000}
            placeholder="e.g. Tighten the abstract to 150 words without changing any numbers"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                submit();
              }
            }}
          />
          <div className="row-actions">
            <label className="tool-select">
              <span className="tiny muted">Tool</span>
              <select value={mode} onChange={(e) => setMode(e.target.value as TaskMode)} aria-label="Tool that performs the task">
                {modes.map((m) => (
                  <option key={m} value={m}>
                    {MODE_LABELS[m]}
                    {m === "auto" ? " (recommended)" : ""}
                  </option>
                ))}
              </select>
            </label>
            <div className="spacer" />
            <span className="tiny muted hide-narrow">Enter to send · Shift+Enter newline</span>
            <button className="btn btn-primary btn-sm" type="submit" disabled={!text.trim() || send.busy}>
              {send.busy ? "Sending…" : "Send"}
            </button>
          </div>
          <ErrorBox error={send.error} />
        </form>
      ) : (
        <div className="ai-input muted small">Viewers cannot start AI tasks.</div>
      )}
    </div>
  );
}

function MessageItem({
  m,
  task,
  proposal,
  canEdit,
  onChanged,
  onReview,
}: {
  m: ChatMessage;
  task: Task | undefined;
  proposal: Proposal | undefined;
  canEdit: boolean;
  onChanged: () => void;
  onReview: (id: string) => void;
}) {
  const meta = m.meta ?? {};
  const ctx = meta.context as TaskContextSpec | undefined;
  if (m.role === "user") {
    return (
      <div className="msg msg-user">
        <div className="msg-meta tiny muted">
          You · {relTime(m.created_at)} · {modeLabel(meta.mode as string)}
          {ctx ? ` · ${ctx.scope === "selection" && ctx.selection ? `${ctx.path} L${ctx.selection.from_line}–${ctx.selection.to_line}` : ctx.scope === "file" ? ctx.path : "project"}` : ""}
        </div>
        <div className="msg-body pre-wrap">{m.content}</div>
        {task && <TaskCard task={task} canEdit={canEdit} onChanged={onChanged} />}
      </div>
    );
  }
  const propId = typeof meta.proposal_id === "string" ? meta.proposal_id : null;
  return (
    <div className={`msg msg-${m.role}`}>
      <div className="msg-meta tiny muted">
        {m.role === "assistant" ? "Prism" : "System"} · {relTime(m.created_at)}
        {typeof meta.executor === "string" && meta.executor ? ` · via ${modeLabel(meta.executor)}` : ""}
        {meta.demo === true && (
          <>
            {" "}
            <Tag tone="warn" title="Produced by the rule-based demo executor, not a language model">
              DEMO
            </Tag>
          </>
        )}
      </div>
      <div className="msg-body pre-wrap">{m.content}</div>
      {propId && <ProposalCard id={propId} proposal={proposal} onReview={onReview} />}
    </div>
  );
}

function ProposalCard({ id, proposal, onReview }: { id: string; proposal: Proposal | undefined; onReview: (id: string) => void }) {
  return (
    <div className="proposal-card">
      <div className="row-actions">
        <strong className="small">Proposed changes</strong>
        {proposal && <Badge status={proposal.status} />}
        {proposal?.demo && <Tag tone="warn">DEMO</Tag>}
        <div className="spacer" />
        <button className="btn btn-sm" onClick={() => onReview(id)}>
          <IconReview /> Review changes
        </button>
      </div>
      {proposal && proposal.files.length > 0 && (
        <ul className="plain-list small changed-files">
          {proposal.files.map((f) => (
            <li key={f.path}>
              <code>{f.path}</code> <span className="muted">{f.change}</span>{" "}
              {f.added != null && (
                <>
                  <span className="add-count">+{f.added}</span> <span className="del-count">−{f.removed}</span>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
      {proposal?.status === "superseded" && <p className="tiny muted">This proposal was replaced by a newer one; open the review to follow it.</p>}
    </div>
  );
}

function TaskCard({ task, canEdit, onChanged }: { task: Task; canEdit: boolean; onChanged: () => void }) {
  const active = isActive(task.status);
  const stream = useTaskStream(task.id, active, () => onChanged());
  const [showLog, setShowLog] = useState(false);
  const detail = useAsync(async () => (showLog && !active ? api.task(task.id) : null), [showLog, active, task.id, task.status]);
  const cancel = useAction(async () => {
    await api.cancelTask(task.id);
    onChanged();
  });
  const retry = useAction(async () => {
    await api.retryTask(task.id);
    onChanged();
  });
  const events = active ? stream.events : (detail.data?.events ?? []);
  const lastStatus = [...stream.events].reverse().find((e) => e.type === "status")?.message;
  const executor = executorFromEvents(task, stream.events);

  return (
    <div className={`task-card ${active ? "active" : ""}`}>
      <div className="row-actions">
        <Badge status={task.status} />
        {active && <LiveDot connected={stream.connected} />}
        {active && lastStatus && lastStatus !== task.status && <span className="tiny muted">{lastStatus}</span>}
        <span className="tiny">
          {modeLabel(task.mode)}
          {executor && executor !== task.mode ? ` → ${modeLabel(executor)}` : ""}
        </span>
        {task.plan_source && (
          <Tag tone={task.plan_source === "demo" ? "warn" : "muted"} title="How the plan was produced (llm-tools / llm-json = orchestrator model; server = fallback built by the server; demo = rule-based)">
            plan: {task.plan_source}
          </Tag>
        )}
        {task.demo && <Tag tone="warn">DEMO</Tag>}
        {task.cancel_requested && active && <span className="tiny muted">stopping…</span>}
        <div className="spacer" />
        {canEdit && active && (
          <button className="btn btn-xs" onClick={() => cancel.run()} disabled={cancel.busy || task.cancel_requested} aria-label="Stop task">
            <IconStop /> Stop
          </button>
        )}
        {canEdit && !active && (task.status === "failed" || task.status === "cancelled") && (
          <button className="btn btn-xs" onClick={() => retry.run()} disabled={retry.busy} aria-label="Retry task">
            <IconRetry /> Retry
          </button>
        )}
      </div>
      {task.error && <div className="tiny text-bad pre-wrap">{task.error}</div>}
      {active ? (
        <EventList events={events} compact />
      ) : (
        <button className="link-btn tiny" aria-expanded={showLog} onClick={() => setShowLog((s) => !s)}>
          {showLog ? "Hide progress log" : "Show progress log"}
        </button>
      )}
      {!active && showLog && (detail.loading && !detail.data ? <Loading /> : <EventList events={events} />)}
      <ErrorBox error={cancel.error || retry.error || detail.error} />
    </div>
  );
}
