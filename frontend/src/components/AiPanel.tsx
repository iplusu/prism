import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api";
import type { ChatMessage, DiscussionStatement, Proposal, Task, TaskContextSpec, TaskMode } from "../types";
import { MODE_LABELS, modeLabel, relTime, useAction, useAsync } from "../util";
import type { SelectionInfo } from "./CodeEditor";
import { IconReview, IconRetry, IconStop, IconUpload } from "./Icons";
import { EventList, executorFromEvents, isActive, LiveDot, useTaskStream } from "./TaskProgress";
import PdfViewer from "./PdfViewer";
import { Badge, ConfirmButton, CopyButton, Empty, ErrorBox, Loading, Modal, Tag } from "./ui";

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

// Templates for the Roundtable prompt (read-only analyses; they only fill the prompt box).
const PRESETS: { id: string; label: string; title: string; prompt: string; apply?: boolean }[] = [
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
  {
    id: "review",
    label: "Review the manuscript",
    title: "Weaknesses in claims, evidence, clarity, structure and presentation, ranked; the agreed fixes can become a proposal.",
    apply: true,
    prompt:
      "Review this manuscript as a critical but fair peer reviewer for the venue it appears to target. Find its real weaknesses in claims, evidence, clarity, structure and presentation. For each weakness give file and line, why it matters, and the concrete change; rank them by importance, and agree on what must be fixed. Do not invent related work, citations or results. Reply in the language of the manuscript.",
  },
  {
    id: "direction",
    label: "Revision direction",
    title: "In which direction and priority to revise; the agreed edits can become a proposal.",
    apply: true,
    prompt:
      "Decide in which direction and in what priority this manuscript should be revised. List the revisions in priority order; for each give what to change, where (file and line), and why it comes before the next one. Also say what should NOT be changed. Do not invent results or citations. Reply in the language of the manuscript.",
  },
];

type Focus = "custom" | "review" | "direction" | "strategy" | "experiments";
const FOCUS_TITLE: Record<Focus, string> = {
  custom: "work on the prompt",
  review: "review the manuscript",
  strategy: "plan the paper's strategy",
  experiments: "design the additional experiments",
  direction: "decide the revision direction",
};

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

  // Attached files are saved in the project under attachments/ (never over an existing file) and named in the request.
  const [attached, setAttached] = useState<File[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);
  async function saveAttachments(): Promise<string[]> {
    const saved: string[] = [];
    for (const f of attached) {
      let file = f;
      try {
        await api.uploadFile(p.pid, file, "attachments", false);
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 409)) throw new Error(`${f.name}: ${e instanceof Error ? e.message : "not attached"}`);
        file = new File([f], `${Date.now().toString(36)}-${f.name}`, { type: f.type }); // the name is taken: keep both
        await api.uploadFile(p.pid, file, "attachments", false);
      }
      saved.push(`attachments/${file.name}`);
    }
    return saved;
  }

  const send = useAction(async () => {
    const saved = attached.length ? await saveAttachments() : [];
    const base: TaskContextSpec =
      effectiveScope === "selection" && sel
        ? {
            scope: "selection",
            path: sel.path,
            selection: { from_line: sel.from_line, to_line: sel.to_line, text: sel.text },
          }
        : effectiveScope === "file"
          ? { scope: "file", path: p.activePath }
          : { scope: "project" };
    const ctx: TaskContextSpec = saved.length ? { ...base, attachments: saved } : base;
    const request = saved.length ? `${text.trim()}\n\nAttached files (saved in the project):\n${saved.map((s) => `- ${s}`).join("\n")}` : text.trim();
    const t = await api.createTask(p.pid, withKo(request), mode, ctx);
    if (saved.length) p.onApplied(saved);
    setAttached([]);
    setText("");
    setPinned(null);
    tasks.setData([t, ...(tasks.data ?? [])]);
    refreshAll();
  });

  // One switch for every request: also answer in Korean. On by default for a Korean browser.
  const [dKorean, setDKorean] = useState(() => (navigator.language || "").toLowerCase().startsWith("ko"));
  const KO_ASK = "\n\nAlso give the complete answer in Korean (한국어), as a last section.";
  const withKo = (s: string) => (dKorean && !/[\uac00-\ud7a3]/.test(s) ? s + KO_ASK : s);
  const [skillOpen, setSkillOpen] = useState(false);
  const [dMode, setDMode] = useState<"discuss" | "reviews">("discuss");
  const [dFocus, setDFocus] = useState<Focus>("custom");
  const [dRounds, setDRounds] = useState(3);
  const [dApply, setDApply] = useState(false);
  const [dAdversary, setDAdversary] = useState(false);
  const [dLead, setDLead] = useState("");
  const [dAsk, setDAsk] = useState("");
  const [dTools, setDTools] = useState<Record<string, boolean>>({});
  const dSys = useAsync(() => (skillOpen ? api.system() : Promise.resolve(null)), [skillOpen]);
  const dAll = useMemo(
    () => (dSys.data?.tools ?? []).filter((x) => ["codex", "claude", "antigravity"].includes(x.name)),
    [dSys.data],
  );
  const dChosen = dAll.filter((x) => x.status === "available" && dTools[x.name] !== false).map((x) => x.name);
  const needsPrompt = dFocus === "custom" && dMode === "discuss";
  const startSkill = useAction(async () => {
    const ask = dAsk.trim() ? `\n\n${dAsk.trim()}` : "";
    const t =
      dMode === "reviews"
        ? await api.createTask(p.pid, `Reviewer report${ask}`, "llm", {
            scope: "project",
            review: { tools: dChosen, korean: dKorean },
          })
        : await api.createTask(p.pid, `Roundtable: ${FOCUS_TITLE[dFocus]}${ask}`, "llm", {
            scope: "project",
            discuss: { tools: dChosen, focus: dFocus, rounds: dRounds, apply: dApply, korean: dKorean, adversary: dAdversary, ...(dLead ? { lead: dLead } : {}) },
          });
    tasks.setData([t, ...(tasks.data ?? [])]);
    setSkillOpen(false);
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
        {p.canEdit && (
          <button type="button" className="btn btn-sm" disabled={send.busy} onClick={() => setSkillOpen(true)}
            title="Codex, Claude Code and Antigravity work on your prompt and answer each other; the orchestrator moderates.">
            Roundtable
          </button>
        )}
        {p.canEdit && (messages.data?.length ?? 0) > 0 && (
          <ConfirmButton
            label="New chat"
            question="Start a new conversation? The AI forgets this chat; your files, tasks and proposals are kept."
            confirmLabel="New chat"
            onConfirm={async () => {
              await api.resetChat(p.pid);
              await messages.reload();
            }}
          />
        )}
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
          {skillOpen && (
            <Modal title="Roundtable" onClose={() => setSkillOpen(false)} wide>
              <div className="skill-form">
                <label className="field">
                  <span className="tiny muted">
                    {needsPrompt ? "Your prompt (what the tools should work on; you can also give them a role, e.g. \"As a JAMS referee, …\")" : "Your prompt (optional; every tool receives it; you can also give them a role)"}
                  </span>
                  <textarea rows={4} value={dAsk} maxLength={4000} autoFocus onChange={(e) => setDAsk(e.target.value)}
                    placeholder='e.g. "Is the ablation in Section 5 enough to support claim 2? If not, what is the smallest extra experiment?"' />
                </label>
                <div className="tiny muted">Templates (fill the prompt; the review and revision templates also switch on the proposal):</div>
                <div className="presets">
                  {PRESETS.map((x) => (
                    <button type="button" key={x.id} className="btn btn-sm" title={x.title} onClick={() => { setDAsk(x.prompt); setDFocus("custom"); setDMode("discuss"); setDApply(!!x.apply); }}>
                      {x.label}
                    </button>
                  ))}
                </div>
                <div className="skill-row">
                  <label className="field">
                    <span className="tiny muted">How</span>
                    <select value={dMode} onChange={(e) => setDMode(e.target.value as "discuss" | "reviews")}>
                      <option value="discuss">Discussion (tools answer each other, @mentions)</option>
                      <option value="reviews">Independent reviews (merged into one report)</option>
                    </select>
                  </label>
                  {dMode === "discuss" && (
                    <label className="field">
                      <span className="tiny muted">Goal</span>
                      <select value={dFocus} onChange={(e) => { const f = e.target.value as Focus; setDFocus(f); setDApply(false); }}>
                        <option value="custom">Your prompt is the goal</option>
                        <option value="strategy">Strategy (positioning, storyline, order of work)</option>
                        <option value="experiments">Experiment design (protocols: arms, N, analysis, decision rule)</option>
                      </select>
                    </label>
                  )}
                </div>
                <div className={`skill-row ${dMode === "discuss" ? "skill-row3" : ""}`}>
                  {dMode === "discuss" && (
                    <>
                      <label className="field">
                        <span className="tiny muted">Rounds</span>
                        <select value={dRounds} onChange={(e) => setDRounds(Number(e.target.value))}>
                          {[3, 4, 5].map((n) => (
                            <option key={n} value={n}>{n}</option>
                          ))}
                        </select>
                      </label>
                      <label className="field" title="Who writes the final report (summary, conclusion, agreed changes). The orchestrator LLM is faster; a tool is slower and uses its quota once more.">
                        <span className="tiny muted">Lead (writes the report)</span>
                        <select value={dLead} onChange={(e) => setDLead(e.target.value)}>
                          <option value="">Orchestrator LLM</option>
                          {dAll.filter((x) => x.status === "available").map((x) => (
                            <option key={x.name} value={x.name}>{x.label}</option>
                          ))}
                        </select>
                      </label>
                    </>
                  )}
                </div>
                <div className="skill-cols">
                  <div className="skill-col">
                    <div className="tiny muted">Participants</div>
                    {dSys.loading && !dSys.data && <Loading text="Checking tools…" />}
                    {dAll.map((x) => {
                      const ok = x.status === "available";
                      return (
                        <label key={x.name} className="check-row" title={ok ? "" : `${x.status}: ${x.detail}`}>
                          <input type="checkbox" disabled={!ok} checked={ok && dTools[x.name] !== false} onChange={(e) => setDTools((v) => ({ ...v, [x.name]: e.target.checked }))} />{" "}
                          {x.label} {!ok && <span className="muted tiny">({x.status})</span>}
                        </label>
                      );
                    })}
                  </div>
                  <div className="skill-col">
                    <div className="tiny muted">Options</div>
                    {dMode === "discuss" && (
                      <>
                        <label className="check-row" title="The agreed changes become an ordinary proposal; you still review and apply it.">
                          <input type="checkbox" checked={dApply} onChange={(e) => setDApply(e.target.checked)} /> Make a proposal from the agreed changes
                        </label>
                        <label className="check-row" title="The last selected tool must state its strongest evidence-based objection before it may accept a point, so agreement is not reached just to be agreeable.">
                          <input type="checkbox" checked={dAdversary} onChange={(e) => setDAdversary(e.target.checked)} /> Last tool plays devil's advocate
                        </label>
                      </>
                    )}
                    <label className="check-row">
                      <input type="checkbox" checked={dKorean} onChange={(e) => setDKorean(e.target.checked)} /> Also write the report in Korean (한국어)
                    </label>
                  </div>
                </div>
                <div className="skill-foot">
                  <span className="tiny muted">Read-only for the tools; takes many minutes and uses each tool's quota once per round.</span>
                  <div className="row-actions">
                  <button type="button" className="btn btn-primary" disabled={dChosen.length < (dMode === "discuss" ? 2 : 1) || startSkill.busy || (needsPrompt && !dAsk.trim())} onClick={() => void startSkill.run()}
                    title={dChosen.length < (dMode === "discuss" ? 2 : 1) ? "Select more available tools" : needsPrompt && !dAsk.trim() ? "Write a prompt first" : ""}>
                    {startSkill.busy ? "Starting…" : `Start with ${dChosen.length} tool${dChosen.length === 1 ? "" : "s"}`}
                  </button>
                  <button type="button" className="btn" onClick={() => setSkillOpen(false)}>Cancel</button>
                  </div>
                </div>
                <ErrorBox error={startSkill.error} />
              </div>
            </Modal>
          )}
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
          <div className="ctx-line">
            <div className="tiny muted ctx-desc" aria-live="polite">
              {effectiveScope === "selection" && sel
                ? `${sel.path}, lines ${sel.from_line}–${sel.to_line} (${sel.text.length} chars)`
                : effectiveScope === "file"
                  ? p.activePath
                  : "Whole project"}
            </div>
            <label className="check-row tiny" title="Chat and the Roundtable also give a Korean version of the answer">
              <input type="checkbox" checked={dKorean} onChange={(e) => setDKorean(e.target.checked)} /> 한국어 병기
            </label>
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
          {attached.length > 0 && (
            <div className="small" style={{ display: "flex", gap: 6, flexWrap: "wrap" }} aria-label="Attached files">
              {attached.map((f, i) => (
                <span key={`${f.name}-${i}`} className="tag">
                  {f.name} ({Math.max(1, Math.round(f.size / 1024))} KB)
                  <button type="button" className="icon-btn sm" aria-label={`Remove ${f.name}`} onClick={() => setAttached(attached.filter((_, j) => j !== i))}>
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
          <div className="row-actions">
            <input
              ref={fileRef}
              type="file"
              multiple
              hidden
              onChange={(e) => {
                setAttached([...attached, ...Array.from(e.target.files ?? [])].slice(0, 10));
                e.target.value = "";
              }}
            />
            <button type="button" className="icon-btn sm" aria-label="Attach files" title="Attach files: saved in the project under attachments/ and named in the request" disabled={attached.length >= 10} onClick={() => fileRef.current?.click()}>
              <IconUpload />
            </button>
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
      {Array.isArray(meta.rounds) && (
        <div className="review-reports">
          {typeof meta.persona === "string" && meta.persona && <div className="muted tiny">Participants acted as: {meta.persona}</div>}
          {typeof meta.lead === "string" && meta.lead && <div className="muted tiny">Report written by: {meta.lead === "claude" ? "Claude Code" : meta.lead === "codex" ? "Codex" : meta.lead === "antigravity" ? "Antigravity" : meta.lead}</div>}
          {(meta.rounds as DiscussionStatement[][]).map((stm, i) => (
            <details key={i} className="review-one">
              <summary className="small">
                Round {i + 1}
                <span className="muted tiny"> · {stm.map((x) => `${x.label}${x.status !== "succeeded" ? ` (${x.status})` : x.agree === true ? (x.echo ? " ✓ (echo)" : " ✓") : x.agree === false ? " ✗" : ""}`).join(", ")}</span>
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
      {propId && <ProposalCard id={propId} proposal={proposal} canEdit={canEdit} onReview={onReview} onChanged={onChanged} onApplied={onApplied} reloadProposals={reloadProposals} />}
      <div className="msg-foot tiny" style={{ marginTop: 6 }}>
        <CopyButton text={m.content} />
      </div>
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
  const [preview, setPreview] = useState(false);
  return (
    <div className="proposal-card">
      {preview && <CandidatePreview id={id} onClose={() => setPreview(false)} />}
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
        {proposal?.candidate_build_id && (
          <button className="btn btn-sm" onClick={() => setPreview(true)} title="The PDF compiled from this proposal, before anything is applied">
            Preview PDF
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

/** The PDF compiled from a proposal, shown over the chat before anything is applied; the changes can be marked (latexdiff). */
function CandidatePreview({ id, onClose }: { id: string; onClose: () => void }) {
  const [marked, setMarked] = useState<"off" | "building" | "on">("off");
  const detail = useAsync(() => api.proposal(id), [id]);
  const doc = detail.data?.candidate_build?.main_file;
  const mark = useAction(async () => {
    setMarked("building");
    try {
      const r = await api.buildMarkedPdf(id);
      if (!r.ok) throw new Error(r.error || "could not mark the changes");
      setMarked("on");
    } catch (e) {
      setMarked("off");
      throw e;
    }
  });
  return (
    <Modal title="Preview: the PDF with this proposal applied (nothing is applied yet)" onClose={onClose} wide>
      {doc && <div className="tiny muted" style={{ marginBottom: 6 }}>Document shown: <code>{doc}</code></div>}
      <div className="row-actions" style={{ marginBottom: 8 }}>
        {marked === "on" ? (
          <button className="btn btn-xs" onClick={() => setMarked("off")}>Show the plain PDF</button>
        ) : (
          <button className="btn btn-xs" disabled={marked === "building"} onClick={() => void mark.run()} title="Compiles base and candidate through latexdiff; takes up to a minute">
            {marked === "building" ? "Building the marked PDF…" : "Show changes marked (latexdiff)"}
          </button>
        )}
      </div>
      <ErrorBox error={mark.error} />
      <div className="candidate-pdf-view">
        {marked === "on" ? (
          <PdfViewer key={`${id}-marked`} url={api.markedPdfUrl(id)} downloadName={`proposal-${id.slice(0, 8)}-marked.pdf`} />
        ) : (
          <PdfViewer key={id} url={api.proposalPdfUrl(id)} downloadName={`proposal-${id.slice(0, 8)}.pdf`} />
        )}
      </div>
    </Modal>
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
