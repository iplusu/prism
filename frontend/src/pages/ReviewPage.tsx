import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { AppHeader } from "../components/AppHeader";
import { FileDiffHeader, HunkView } from "../components/DiffView";
import { IconBack } from "../components/Icons";
import PdfViewer from "../components/PdfViewer";
import PlainEditor from "../components/PlainEditor";
import { Badge, ConfirmButton, Empty, ErrorBox, Loading, Tabs, Tag } from "../components/ui";
import VerificationReport from "../components/VerificationReport";
import type { Conflict, Proposal, ProposalDetail } from "../types";
import { fmtTime, modeLabel, relTime, shortRev, useAction, useAsync, useInterval } from "../util";

const OPEN = ["verifying", "ready", "conflict", "applying"];
const ORIGIN_LABEL: Record<string, string> = {
  agent: "AI task",
  selection: "selected changes",
  resolution: "conflict resolution",
  restore: "version restore",
  merge: "3-way merge",
};

export default function ReviewPage() {
  const { pid = "", propId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const notice = (location.state as { notice?: string } | null)?.notice;
  const [showAll, setShowAll] = useState(false);
  const project = useAsync(() => api.project(pid), [pid]);
  const all = useAsync(() => api.proposals(pid, "all"), [pid]);
  const canEdit = project.data?.role === "owner" || project.data?.role === "editor";

  const visible = useMemo(
    () => (all.data ?? []).filter((x) => showAll || OPEN.includes(x.status) || x.id === propId),
    [all.data, showAll, propId],
  );

  useEffect(() => {
    document.title = `Review changes · ${project.data?.name ?? "Prism"}`;
  }, [project.data]);

  // Keep the list fresh while anything is in flight.
  const listBusy = (all.data ?? []).some((x) => x.status === "verifying" || x.status === "applying");
  useInterval(() => void all.reload(), listBusy ? 3000 : null);

  return (
    <div className="page review-page">
      <AppHeader
        title={
          <span className="row-actions">
            <Link to={`/p/${pid}`} className="btn btn-ghost btn-sm">
              <IconBack /> {project.data?.name ?? "Workspace"}
            </Link>
            <span className="muted">/</span> <strong>Review changes</strong>
          </span>
        }
      />
      <div className="review-layout">
        <aside className="review-list" aria-label="Proposals">
          <div className="side-head">
            <span className="side-title">Proposals</span>
            <div className="spacer" />
            <label className="check small">
              <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> Show all
            </label>
          </div>
          <ErrorBox error={all.error} onRetry={all.reload} />
          {all.loading && !all.data && <Loading />}
          {all.data && visible.length === 0 && <Empty>{showAll ? "No proposals yet." : "No open proposals. Tick “Show all” to see past ones."}</Empty>}
          <ul className="prop-list">
            {visible.map((x) => (
              <li key={x.id}>
                <Link to={`/p/${pid}/review/${x.id}`} className={`prop-row ${x.id === propId ? "active" : ""}`} aria-current={x.id === propId ? "page" : undefined}>
                  <span className="ellipsis prop-title">{x.title || "(untitled)"}</span>
                  <span className="row-actions">
                    <Badge status={x.status} />
                    {x.demo && <Tag tone="warn">DEMO</Tag>}
                    <span className="muted tiny">
                      {x.files.length} file{x.files.length === 1 ? "" : "s"} · {relTime(x.created_at)}
                    </span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </aside>
        <main className="review-main">
          {propId ? (
            <ProposalView
              key={propId}
              notice={notice}
              id={propId}
              pid={pid}
              all={all.data ?? []}
              canEdit={canEdit}
              onChanged={() => void all.reload()}
              goTo={(id, n) => navigate(`/p/${pid}/review/${id}`, { state: n ? { notice: n } : null })}
            />
          ) : (
            <Empty>Select a proposal to review its changes, verification results and candidate PDF.</Empty>
          )}
        </main>
      </div>
    </div>
  );
}

type DetailTab = "changes" | "verification" | "pdf" | "conflicts";

function ProposalView({
  notice,
  id,
  pid,
  all,
  canEdit,
  onChanged,
  goTo,
}: {
  notice: string | undefined;
  id: string;
  pid: string;
  all: Proposal[];
  canEdit: boolean;
  onChanged: () => void;
  goTo: (id: string, notice?: string) => void;
}) {
  const d = useAsync(() => api.proposal(id), [id]);
  const p = d.data;
  const polling = p && (p.status === "verifying" || p.status === "applying");
  useInterval(() => void d.reload(), polling ? 2000 : null);
  const [tab, setTab] = useState<DetailTab>("changes");
  useEffect(() => {
    if (p?.status === "conflict") setTab("conflicts");
  }, [p?.status]);
  // Refresh the list when this proposal changes state.
  useEffect(() => {
    if (p) onChanged();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p?.status]);

  // Hunk selection: path -> selected hunk ids (default: everything).
  const [sel, setSel] = useState<Record<string, Set<number>>>({});
  useEffect(() => {
    if (!p) return;
    const s: Record<string, Set<number>> = {};
    for (const f of p.file_diffs) s[f.path] = new Set(f.hunks && f.hunks.length ? f.hunks.map((h) => h.id) : [0]);
    setSel(s);
  }, [p?.id, p?.file_diffs.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const totals = useMemo(() => {
    let total = 0;
    let chosen = 0;
    for (const f of p?.file_diffs ?? []) {
      const s = sel[f.path] ?? new Set<number>();
      const wholeFile = f.binary || f.change !== "modified" || !f.hunks?.length;
      total += wholeFile ? 1 : f.hunks!.length;
      chosen += wholeFile ? (s.size ? 1 : 0) : s.size;
    }
    return { total, chosen };
  }, [p, sel]);

  const apply = useAction(async () => {
    await api.applyProposal(id);
    await d.reload();
    onChanged();
  });
  const reject = useAction(async () => {
    await api.rejectProposal(id);
    await d.reload();
    onChanged();
  });
  const select = useAction(async () => {
    const selections: Record<string, number[]> = {};
    for (const [path, s] of Object.entries(sel)) if (s.size) selections[path] = [...s];
    const child = await api.selectHunks(id, selections);
    onChanged();
    goTo(
      child.id,
      child.status === "failed"
        ? `The selection did not produce a usable proposal: ${child.error || "verification could not start"}.`
        : "This new proposal contains only the changes you selected. It is being compiled and checked again; you can apply it once verification finishes and its status is “ready”.",
    );
  });

  if (d.error && !p) return <ErrorBox error={d.error} onRetry={d.reload} />;
  if (!p) return <Loading text="Loading proposal…" />;

  const children = all.filter((x) => x.parent_id === p.id);
  const parent = p.parent_id ? all.find((x) => x.id === p.parent_id) : undefined;
  const selectable = canEdit && p.status === "ready";
  const actionError = apply.error || reject.error || select.error;

  return (
    <article className="proposal">
      {notice && ["verifying", "ready", "failed"].includes(p.status) && (
        <div className="notice" role="status">
          {notice}
        </div>
      )}
      <header className="proposal-head">
        <div className="row-actions">
          <h2 className="ellipsis">{p.title || "(untitled proposal)"}</h2>
          <Badge status={p.status} />
          {p.demo && (
            <Tag tone="warn" title="Produced by the rule-based demo executor">
              DEMO
            </Tag>
          )}
        </div>
        <div className="muted small">
          {ORIGIN_LABEL[p.origin] ?? p.origin}
          {p.executor ? ` · by ${modeLabel(p.executor)}` : ""} · created {fmtTime(p.created_at)} · base <code>{shortRev(p.base_revision)}</code>
          {p.applied_revision && (
            <>
              {" "}
              · applied as <code>{shortRev(p.applied_revision)}</code>
            </>
          )}
        </div>
        {parent && (
          <div className="small">
            Derived from <Link to={`/p/${pid}/review/${parent.id}`}>{parent.title || parent.id.slice(0, 8)}</Link> ({parent.status})
          </div>
        )}
        <StatusExplainer p={p} kids={children} pid={pid} />
        {p.summary && <p className="pre-wrap proposal-summary">{p.summary}</p>}
        {p.error && (
          <div className={p.status === "superseded" ? "notice" : "error-box"} role={p.status === "superseded" ? undefined : "alert"}>
            {p.error}
          </div>
        )}
        {canEdit && (
          <div className="row-actions proposal-actions">
            {p.status === "ready" && totals.chosen === totals.total && (
              <button className="btn btn-primary" disabled={apply.busy} onClick={() => void apply.run()}>
                {apply.busy ? "Applying…" : "Apply all changes"}
              </button>
            )}
            {p.status === "ready" && totals.chosen < totals.total && (
              <button className="btn btn-primary" disabled={totals.chosen === 0 || select.busy} onClick={() => select.run()}>
                {select.busy ? "Creating…" : `Create proposal from ${totals.chosen} of ${totals.total} selected change${totals.total === 1 ? "" : "s"}`}
              </button>
            )}
            {["ready", "conflict", "verifying", "failed"].includes(p.status) && (
              <ConfirmButton label="Reject" question="Reject this proposal?" confirmLabel="Reject" onConfirm={() => reject.run()} />
            )}
            {p.status === "ready" && totals.chosen < totals.total && (
              <span className="muted small">Partial selections are re-verified as a new proposal before they can be applied.</span>
            )}
          </div>
        )}
        <ErrorBox error={actionError} />
      </header>

      <Tabs
        label="Proposal details"
        value={tab}
        onChange={setTab}
        tabs={[
          { id: "changes" as DetailTab, label: `Changes (${p.files.length})` },
          { id: "verification" as DetailTab, label: "Verification" },
          { id: "pdf" as DetailTab, label: "Candidate PDF" },
          ...(p.status === "conflict" ? [{ id: "conflicts" as DetailTab, label: `Conflicts (${p.conflicts.length})` }] : []),
        ]}
      />
      <div className="proposal-body" role="tabpanel">
        {tab === "changes" && (
          <div>
            {selectable && (
              <p className="muted small">
                Untick changes you do not want. {totals.chosen} of {totals.total} selected.{" "}
                <button className="link-btn" onClick={() => setSel(Object.fromEntries(p.file_diffs.map((f) => [f.path, new Set(f.hunks?.length ? f.hunks.map((h) => h.id) : [0])])))}>
                  Select all
                </button>{" "}
                ·{" "}
                <button className="link-btn" onClick={() => setSel(Object.fromEntries(p.file_diffs.map((f) => [f.path, new Set<number>()])))}>
                  Select none
                </button>
              </p>
            )}
            {p.file_diffs.length === 0 && <Empty>This proposal has no file changes.</Empty>}
            {p.file_diffs.map((f) => {
              const wholeFile = f.binary || f.change !== "modified" || !f.hunks?.length;
              const s = sel[f.path] ?? new Set<number>();
              return (
                <section key={f.path} className="file-diff">
                  <div className="row-actions">
                    {selectable && wholeFile && (
                      <input
                        type="checkbox"
                        aria-label={`Include ${f.change} file ${f.path}`}
                        checked={s.size > 0}
                        onChange={(e) =>
                          setSel((m) => ({ ...m, [f.path]: new Set(e.target.checked ? (f.hunks?.length ? f.hunks.map((h) => h.id) : [0]) : []) }))
                        }
                      />
                    )}
                    <FileDiffHeader path={f.path} change={f.change} stats={{ added: f.added, removed: f.removed }} />
                  </div>
                  {f.binary ? (
                    <p className="muted small">Binary file — no text diff.</p>
                  ) : (
                    f.hunks?.map((h) => (
                      <HunkView
                        key={h.id}
                        hunk={h}
                        path={f.path}
                        selectable={selectable && !wholeFile}
                        checked={s.has(h.id)}
                        onToggle={(v) =>
                          setSel((m) => {
                            const n = new Set(m[f.path] ?? []);
                            if (v) n.add(h.id);
                            else n.delete(h.id);
                            return { ...m, [f.path]: n };
                          })
                        }
                      />
                    ))
                  )}
                </section>
              );
            })}
          </div>
        )}
        {tab === "verification" && <VerificationReport p={p} />}
        {tab === "pdf" && <CandidatePdf p={p} />}
        {tab === "conflicts" && p.status === "conflict" && (
          <ConflictResolver
            p={p}
            canEdit={canEdit}
            onResolved={(child) => {
              onChanged();
              goTo(
                child.id,
                child.status === "failed"
                  ? `Your resolution did not produce a usable proposal: ${child.error || "unknown error"}. (If you kept the current manuscript text, there is nothing left to apply.)`
                  : "Your resolution was saved as a new proposal. It is being compiled and checked; apply it once it is “ready”.",
              );
            }}
          />
        )}
      </div>
    </article>
  );
}

function StatusExplainer({ p, kids, pid }: { p: ProposalDetail; kids: Proposal[]; pid: string }) {
  switch (p.status) {
    case "verifying":
      return <div className="notice info small">Verifying: compiling the candidate and running checks. This page refreshes automatically.</div>;
    case "applying":
      return <div className="notice info small">Applying to the manuscript… This page refreshes automatically.</div>;
    case "conflict":
      return (
        <div className="notice warn small">
          The manuscript changed since this proposal was made and some edits overlap. Resolve the conflicts below; the result becomes a new proposal
          that is verified again.
        </div>
      );
    case "applied":
      return <div className="notice ok small">Applied to the manuscript.</div>;
    case "superseded":
      return (
        <div className="notice small">
          Superseded by{" "}
          {kids.length === 0
            ? "a newer proposal (not in the recent list)."
            : kids.map((c, i) => (
                <span key={c.id}>
                  {i > 0 && ", "}
                  <Link to={`/p/${pid}/review/${c.id}`}>{c.title || c.id.slice(0, 8)}</Link> ({c.status})
                </span>
              ))}
        </div>
      );
    default:
      return null;
  }
}

function CandidatePdf({ p }: { p: ProposalDetail }) {
  const b = p.candidate_build;
  if (!b) return <Empty>{p.status === "verifying" ? "The candidate is being compiled…" : "No candidate build exists for this proposal."}</Empty>;
  return (
    <div className="candidate-pdf">
      <p className="muted small">
        PDF compiled from this proposal (build {b.status}, {fmtTime(b.finished_at || b.created_at)}
        {b.isolation ? `, isolation: ${b.isolation}` : ""}). It has not been inspected by any model — check the layout yourself.
      </p>
      {b.error && <div className={b.status === "blocked" ? "error-box" : "error-box soft"}>{b.error}</div>}
      {b.has_pdf ? (
        <div className="candidate-pdf-view">
          <PdfViewer key={b.id} url={api.proposalPdfUrl(p.id)} downloadName={`proposal-${p.id.slice(0, 8)}.pdf`} />
        </div>
      ) : (
        <Empty>This build produced no PDF.</Empty>
      )}
    </div>
  );
}

const MARKER = /^(<<<<<<< |=======$|>>>>>>> |\|\|\|\|\|\|\| )/m;

function ConflictResolver({ p, canEdit, onResolved }: { p: ProposalDetail; canEdit: boolean; onResolved: (child: Proposal) => void }) {
  const [texts, setTexts] = useState<Record<string, string>>(() =>
    Object.fromEntries(p.conflicts.map((c) => [c.path, c.merged ?? c.current ?? c.proposal ?? ""])),
  );
  // Binary, added or deleted files cannot be merged as text: the user picks a whole version.
  const wholeFile = p.conflicts.filter((c) => c.merged === null && (c.current === null || c.proposal === null));
  const [choices, setChoices] = useState<Record<string, "current" | "proposal">>({});
  const undecided = wholeFile.filter((c) => !choices[c.path]);
  const textConflicts = p.conflicts.filter((c) => !wholeFile.includes(c));
  const withMarkers = textConflicts.filter((c) => MARKER.test(texts[c.path] ?? ""));
  const act = useAction(async () => {
    const resolutions = Object.fromEntries(textConflicts.map((c) => [c.path, texts[c.path] ?? ""]));
    const child = await api.resolveConflicts(p.id, resolutions, choices);
    onResolved(child);
  });
  return (
    <div className="conflicts">
      <p className="small">
        For each file, edit the <strong>resolution</strong> so it contains the final text. Merged text uses diff3 markers:{" "}
        <code>{"<<<<<<< current manuscript"}</code>, <code>{"||||||| proposal base"}</code>, <code>=======</code>, <code>{">>>>>>> proposal"}</code>. Remove all
        markers before submitting.
      </p>
      {textConflicts.map((c) => (
        <ConflictFile key={c.path} c={c} value={texts[c.path] ?? ""} onChange={(v) => setTexts((t) => ({ ...t, [c.path]: v }))} canEdit={canEdit} />
      ))}
      {wholeFile.map((c) => (
        <section className="conflict-file" key={c.path}>
          <div className="row-actions">
            <code className="path">{c.path}</code>
            <Tag tone="warn">{c.reason}</Tag>
          </div>
          <fieldset className="field" disabled={!canEdit}>
            <legend className="small">This file was added, deleted or is binary on one side. Choose which version to keep:</legend>
            <label>
              <input type="radio" name={`choice-${c.path}`} checked={choices[c.path] === "current"}
                onChange={() => setChoices((x) => ({ ...x, [c.path]: "current" }))} /> Keep the current manuscript&apos;s version
              {c.current === null ? " (file absent)" : ""}
            </label>
            <label>
              <input type="radio" name={`choice-${c.path}`} checked={choices[c.path] === "proposal"}
                onChange={() => setChoices((x) => ({ ...x, [c.path]: "proposal" }))} /> Take the proposal&apos;s version
              {c.proposal === null ? " (deletes the file)" : ""}
            </label>
          </fieldset>
        </section>
      ))}
      {withMarkers.length > 0 && <p className="text-bad small">Conflict markers remain in: {withMarkers.map((c) => c.path).join(", ")}</p>}
      <ErrorBox error={act.error} />
      {canEdit && (
        <button className="btn btn-primary" disabled={act.busy || withMarkers.length > 0 || undecided.length > 0} onClick={() => act.run()}>
          {act.busy ? "Submitting…" : "Submit resolution (creates a new proposal)"}
        </button>
      )}
    </div>
  );
}

function ConflictFile({ c, value, onChange, canEdit }: { c: Conflict; value: string; onChange: (v: string) => void; canEdit: boolean }) {
  const [view, setView] = useState<"merged" | "current" | "proposal">(c.merged !== null ? "merged" : "current");
  const ref = view === "merged" ? c.merged : view === "current" ? c.current : c.proposal;
  return (
    <section className="conflict-file">
      <div className="row-actions">
        <code className="path">{c.path}</code>
        <Tag tone="warn">{c.reason}</Tag>
      </div>
      <div className="conflict-grid">
        <div>
          <Tabs
            label={`Versions of ${c.path}`}
            value={view}
            onChange={setView}
            tabs={[
              { id: "merged", label: "Merged (with markers)" },
              { id: "current", label: "Current manuscript" },
              { id: "proposal", label: "Proposal" },
            ]}
          />
          {ref === null ? <p className="muted small">Not available (file missing or binary on this side).</p> : <pre className="code-pane">{ref}</pre>}
        </div>
        <div>
          <div className="row-actions small">
            <strong>Resolution</strong>
            <div className="spacer" />
            {canEdit &&
              (["merged", "current", "proposal"] as const).map((k) =>
                c[k] !== null ? (
                  <button key={k} className="btn btn-xs" onClick={() => onChange(c[k] ?? "")}>
                    Start from {k}
                  </button>
                ) : null,
              )}
          </div>
          <PlainEditor path={c.path} value={value} onChange={onChange} readOnly={!canEdit} label={`Resolution for ${c.path}`} />
        </div>
      </div>
    </section>
  );
}
