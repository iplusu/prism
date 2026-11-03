import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import type { ChatMessage, DiscussionStatement, Proposal, Task, TaskContextSpec, TaskMode } from "../types";
import { MODE_LABELS, modeLabel, relTime, useAction, useAsync } from "../util";
import type { SelectionInfo } from "./CodeEditor";
import { IconReview, IconRetry, IconStop } from "./Icons";
import { EventList, executorFromEvents, isActive, LiveDot, useTaskStream } from "./TaskProgress";
import { Badge, CopyButton, Empty, ErrorBox, Loading, Tag } from "./ui";

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
  /** Proposal files were just written to the manuscript: reload them in the editor. */
  onApplied: (paths: string[]) => void;
}

// Analysis presets. They run with answer_only: the server never lets them edit files or call a tool.
const PRESETS: { id: string; label: string; title: string; prompt: string }[] = [
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

  const [reviewOpen, setReviewOpen] = useState(false);
  const [persona, setPersona] = useState("");
  const [reviewTools, setReviewTools] = useState<Record<string, boolean>>({});
  const sys = useAsync(() => (reviewOpen ? api.system() : Promise.resolve(null)), [reviewOpen]);
  const reviewers = useMemo(
    () => (sys.data?.tools ?? []).filter((x) => ["codex", "claude", "antigravity"].includes(x.name)),
    [sys.data],
  );
  const chosen = reviewers.filter((x) => x.status === "available" && reviewTools[x.name] !== false).map((x) => x.name);
  const startReview = useAction(async () => {
    const t = await api.createTask(
      p.pid,
      persona.trim() ? `Reviewer report as: ${persona.trim()}` : "Reviewer report",
      "llm",
      { scope: "project", review: { tools: chosen, persona: persona.trim() } },
    );
    tasks.setData([t, ...(tasks.data ?? [])]);
    setReviewOpen(false);
    refreshAll();
  });

  const [discussOpen, setDiscussOpen] = useState(false);
  const [dFocus, setDFocus] = useState<"review" | "direction" | "strategy" | "experiments">("review");
  const [dRounds, setDRounds] = useState(2);
  const [dApply, setDApply] = useState(true);
  const [dPersona, setDPersona] = useState("");
  const [dAsk, setDAsk] = useState("");
  const [dTools, setDTools] = useState<Record<string, boolean>>({});
  const dSys = useAsync(() => (discussOpen ? api.system() : Promise.resolve(null)), [discussOpen]);
  const dAll = useMemo(
    () => (dSys.data?.tools ?? []).filter((x) => ["codex", "claude", "antigravity"].includes(x.name)),
    [dSys.data],
  );
  const dChosen = dAll.filter((x) => x.status === "available" && dTools[x.name] !== false).map((x) => x.name);
  const startDiscuss = useAction(async () => {
    const t = await api.createTask(
      p.pid,
      (dFocus === "review" ? "Tool discussion: review the manuscript"
        : dFocus === "strategy" ? "Tool discussion: plan the paper's strategy"
        : dFocus === "experiments" ? "Tool discussion: design the additional experiments" : "Tool discussion: decide the revision direction")
        + (dAsk.trim() ? `\n\n${dAsk.trim()}` : ""),
      "llm",
      { scope: "project", discuss: { tools: dChosen, focus: dFocus, rounds: dRounds, persona: dPersona.trim(), apply: dApply } },
    );
    tasks.setData([t, ...(tasks.data ?? [])]);
    setDiscussOpen(false);
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
            pid={p.pid}
            m={m}
            task={m.task_id ? taskById.get(m.task_id) : undefined}
            proposal={typeof m.meta?.proposal_id === "string" ? propById.get(m.meta.proposal_id as string) : undefined}
            canEdit={p.canEdit}
            onChanged={refreshAll}
            onReview={(id) => navigate(`/p/${p.pid}/review/${id}`)}
            onApplied={p.onApplied}
            reloadProposals={() => proposals.reload()}
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
          <button type="button" className="btn btn-sm" aria-expanded={reviewOpen} disabled={send.busy || preset.busy} onClick={() => setReviewOpen((v) => !v)}
            title="Codex, Claude Code and Antigravity each review the paper read-only; the orchestrator merges their reviews and proposes improvements.">
            Reviewer report…
          </button>
          {reviewOpen && (
            <div className="review-setup small" role="group" aria-label="Reviewer report">
              <div className="tiny muted">Each selected tool reads the paper as a separate reviewer (read-only, in its sandbox); the orchestrator then merges the reviews. Takes several minutes.</div>
              {sys.loading && !sys.data && <Loading text="Checking tools…" />}
              {reviewers.map((x) => {
                const ok = x.status === "available";
                return (
                  <label key={x.name} className="check-row" title={ok ? "" : `${x.status}: ${x.detail}`}>
                    <input type="checkbox" disabled={!ok} checked={ok && reviewTools[x.name] !== false} onChange={(e) => setReviewTools((s) => ({ ...s, [x.name]: e.target.checked }))} />{" "}
                    {x.label} {!ok && <span className="muted tiny">({x.status})</span>}
                  </label>
                );
              })}
              <label className="field">
                <span className="tiny muted">Reviewer persona (optional): who should the reviewers be?</span>
                <input value={persona} maxLength={2000} placeholder="e.g. an ACL area chair who cares about reproducibility" onChange={(e) => setPersona(e.target.value)} />
              </label>
              <div className="row-actions">
                <button type="button" className="btn btn-sm btn-primary" disabled={chosen.length === 0 || startReview.busy} onClick={() => void startReview.run()}>
                  {startReview.busy ? "Starting…" : `Run ${chosen.length} reviewer${chosen.length === 1 ? "" : "s"}`}
                </button>
                <button type="button" className="btn btn-sm" onClick={() => setReviewOpen(false)}>
                  Cancel
                </button>
              </div>
              <ErrorBox error={startReview.error} />
            </div>
          )}
          <button type="button" className="btn btn-sm" aria-expanded={discussOpen} disabled={send.busy || preset.busy} onClick={() => setDiscussOpen((v) => !v)}
            title="The tools read each other's statements over several rounds; the orchestrator moderates, and the changes they all accept can become a proposal.">
            Tool discussion…
          </button>
          {discussOpen && (
            <div className="review-setup small" role="group" aria-label="Tool discussion">
              <div className="tiny muted">The selected tools first answer on their own (read-only), then answer each other for up to the chosen number of rounds. Takes many minutes and uses each tool's quota once per round.</div>
              {dSys.loading && !dSys.data && <Loading text="Checking tools…" />}
              {dAll.map((x) => {
                const ok = x.status === "available";
                return (
                  <label key={x.name} className="check-row" title={ok ? "" : `${x.status}: ${x.detail}`}>
                    <input type="checkbox" disabled={!ok} checked={ok && dTools[x.name] !== false} onChange={(e) => setDTools((s) => ({ ...s, [x.name]: e.target.checked }))} />{" "}
                    {x.label} {!ok && <span className="muted tiny">({x.status})</span>}
                  </label>
                );
              })}
              <label className="field">
                <span className="tiny muted">Topic</span>
                <select value={dFocus} onChange={(e) => { const f = e.target.value as "review" | "direction" | "strategy" | "experiments"; setDFocus(f); setDApply(f !== "strategy" && f !== "experiments"); }}>
                  <option value="review">Manuscript review (find weaknesses)</option>
                  <option value="strategy">Strategy (positioning, storyline, order of work; no edits by default)</option>
                  <option value="experiments">Experiment design (protocols: arms, N, analysis, decision rule)</option>
                  <option value="direction">Revision direction (what to change, in what order)</option>
                </select>
              </label>
              <label className="field">
                <span className="tiny muted">Rounds</span>
                <select value={dRounds} onChange={(e) => setDRounds(Number(e.target.value))}>
                  {[1, 2, 3].map((n) => (
                    <option key={n} value={n}>
                      {n}
                      {n === 1 ? " (independent statements only)" : ""}
                    </option>
                  ))}
                </select>
              </label>
              <label className="check-row">
                <input type="checkbox" checked={dApply} onChange={(e) => setDApply(e.target.checked)} /> Turn the agreed changes into a proposal (you still review and apply it)
              </label>
              <label className="field">
                <span className="tiny muted">What the tools should work on (optional; they all receive it, e.g. "design the additional experiments")</span>
                <textarea rows={3} value={dAsk} maxLength={4000} onChange={(e) => setDAsk(e.target.value)} />
              </label>
              <label className="field">
                <span className="tiny muted">Role for the participants (optional; who they should act as, not the task)</span>
                <input value={dPersona} maxLength={2000} placeholder="e.g. a JAMS referee" onChange={(e) => setDPersona(e.target.value)} />
              </label>
              <div className="row-actions">
                <button type="button" className="btn btn-sm btn-primary" disabled={dChosen.length < 2 || startDiscuss.busy} onClick={() => void startDiscuss.run()}
                  title={dChosen.length < 2 ? "Select at least two available tools" : ""}>
                  {startDiscuss.busy ? "Starting…" : `Start with ${dChosen.length} tools`}
                </button>
                <button type="button" className="btn btn-sm" onClick={() => setDiscussOpen(false)}>
                  Cancel
                </button>
              </div>
              <ErrorBox error={startDiscuss.error} />
            </div>
          )}
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
  pid,
  m,
  task,
  proposal,
  canEdit,
  onChanged,
  onReview,
  onApplied,
  reloadProposals,
}: {
  pid: string;
  m: ChatMessage;
  task: Task | undefined;
  proposal: Proposal | undefined;
  canEdit: boolean;
  onChanged: () => void;
  onReview: (id: string) => void;
  onApplied: (paths: string[]) => void;
  reloadProposals: () => void;
}) {
  const meta = m.meta ?? {};
  const ctx = meta.context as TaskContextSpec | undefined;
  if (m.role === "user") {
    return (
      <div className="msg msg-user">
        <div className="msg-meta tiny muted">
          You · {relTime(m.created_at)} · {modeLabel(meta.mode as string)}
          {ctx ? ` · ${ctx.scope === "selection" && ctx.selection ? `${ctx.path} L${ctx.selection.from_line}–${ctx.selection.to_line}` : ctx.scope === "file" ? ctx.path : "project"}` : ""}
          <CopyButton text={m.content} />
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
        <CopyButton text={m.content} />
      </div>
      <div className="msg-body pre-wrap">{m.content}</div>
      {Array.isArray(meta.reviews) && (
        <div className="review-reports">
          {(meta.reviews as { tool: string; label: string; status: string; persona: string; text: string; error: string }[]).map((r) => (
            <details key={r.tool} className="review-one">
              <summary className="small">
                {r.label} · {r.status === "succeeded" ? "review" : r.status}
                {r.persona && <span className="muted tiny"> · as {r.persona}</span>}
              </summary>
              <div className="pre-wrap small">{r.status === "succeeded" ? r.text : r.error || "no review"}</div>
              {r.status === "succeeded" && <CopyButton text={r.text} label={`Copy ${r.label} review`} />}
            </details>
          ))}
        </div>
      )}
      {meta.kind === "discussion" && canEdit && <SaveDiscussion pid={pid} m={m} onSaved={onApplied} />}
      {Array.isArray(meta.rounds) && (
        <div className="review-reports">
          {(meta.rounds as DiscussionStatement[][]).map((stm, i) => (
            <details key={i} className="review-one" open={i === (meta.rounds as unknown[]).length - 1}>
              <summary className="small">
                Round {i + 1}
                <span className="muted tiny"> · {stm.map((x) => `${x.label}${x.status !== "succeeded" ? ` (${x.status})` : x.agree === true ? " ✓" : x.agree === false ? " ✗" : ""}`).join(", ")}</span>
              </summary>
              {stm.map((x) => (
                <div key={x.tool} className="small">
                  <strong>{x.label}</strong>
                  <div className="pre-wrap">{x.status === "succeeded" ? x.text : x.error || x.status}</div>
                  {x.status === "succeeded" && <CopyButton text={x.text} label={`Copy ${x.label} statement`} />}
                </div>
              ))}
            </details>
          ))}
        </div>
      )}
      {propId && <ProposalCard id={propId} proposal={proposal} canEdit={canEdit} onReview={onReview} onChanged={onChanged} onApplied={onApplied} reloadProposals={reloadProposals} />}
    </div>
  );
}

/** Saves the discussion report (summary, final design, consensus, disagreements) as a Markdown note in the project.
 *  Only a new file under notes/ is created; no manuscript file is touched and the build does not use it. */
function SaveDiscussion({ pid, m, onSaved }: { pid: string; m: ChatMessage; onSaved: (paths: string[]) => void }) {
  const [saved, setSaved] = useState<string | null>(null);
  const save = useAction(async () => {
    const d = new Date(m.created_at);
    const z = (n: number) => String(n).padStart(2, "0");
    const stamp = `${d.getFullYear()}${z(d.getMonth() + 1)}${z(d.getDate())}-${z(d.getHours())}${z(d.getMinutes())}${z(d.getSeconds())}`;
    const path = `notes/${String(m.meta?.focus ?? "discussion")}-${stamp}.md`;
    await api.createFile(pid, path, `# Tool discussion (${String(m.meta?.focus ?? "")}), ${d.toLocaleString()}\n\n${m.content}\n`);
    setSaved(path);
    onSaved([path]);
  });
  return (
    <div className="small" style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
      <button type="button" className="btn btn-sm" disabled={save.busy || saved !== null} onClick={() => void save.run()} title="Creates one new Markdown file under notes/; no manuscript file is changed">
        {saved ? "Saved" : save.busy ? "Saving…" : "Save as file"}
      </button>
      {saved && <span className="muted tiny">{saved}</span>}
      {save.error && <span className="tiny" style={{ color: "var(--danger, #b00020)" }}>{save.error}</span>}
    </div>
  );
}

function ProposalCard({
  id,
  proposal,
  canEdit,
  onReview,
  onChanged,
  onApplied,
  reloadProposals,
}: {
  id: string;
  proposal: Proposal | undefined;
  canEdit: boolean;
  onReview: (id: string) => void;
  onChanged: () => void;
  onApplied: (paths: string[]) => void;
  reloadProposals: () => void;
}) {
  // Apply straight from the chat (one click). Only a verified ("ready") proposal offers it; applying is
  // asynchronous on the server, so follow the status until it is applied (or a conflict/failure shows up).
  const [waiting, setWaiting] = useState(false);
  const paths = useRef<string[]>([]);
  paths.current = proposal?.files.map((f) => f.path) ?? [];
  const apply = useAction(async () => {
    await api.applyProposal(id);
    setWaiting(true);
    onChanged();
  });
  useEffect(() => {
    if (!waiting) return;
    let stopped = false;
    let tries = 0;
    const tick = async () => {
      if (stopped) return;
      const cur = await api.proposal(id).catch(() => null);
      if (stopped) return;
      if (cur && cur.status !== "applying" && cur.status !== "ready") {
        setWaiting(false);
        if (cur.status === "applied") onApplied(paths.current);
        reloadProposals();
        onChanged();
        return;
      }
      if (++tries > 60) {
        setWaiting(false);
        return;
      }
      window.setTimeout(() => void tick(), 1000);
    };
    void tick();
    return () => {
      stopped = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [waiting, id]);
  const busy = apply.busy || waiting || proposal?.status === "applying";
  return (
    <div className="proposal-card">
      <div className="row-actions">
        <strong className="small">Proposed changes</strong>
        {proposal && <Badge status={proposal.status} />}
        {proposal?.demo && <Tag tone="warn">DEMO</Tag>}
        <div className="spacer" />
        {canEdit && proposal?.status === "ready" && (
          <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => void apply.run()} title="Apply all changes to the manuscript (it was compiled and checked)">
            {busy ? "Applying…" : "Apply"}
          </button>
        )}
        <button className="btn btn-sm" onClick={() => onReview(id)}>
          <IconReview /> Review changes
        </button>
      </div>
      <ErrorBox error={apply.error} />
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
