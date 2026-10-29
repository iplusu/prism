import type { CounterDiff, ProposalDetail } from "../types";
import { Badge, Tag } from "./ui";

const CHECK_LABELS: Record<string, string> = {
  compiles: "Candidate compiles",
  only_write_scope: "Only files in the allowed scope changed",
  no_new_undefined_citations: "No new undefined citations",
  no_new_undefined_references: "No new undefined references",
  preserve_citations: "Citations preserved",
  preserve_labels: "Labels preserved",
  preserve_numbers: "Numbers preserved",
  preserve_equations: "Equations preserved",
};

function List({ title, items, tone }: { title: string; items: string[]; tone?: string }) {
  if (!items?.length) return null;
  return (
    <div className="kv-row">
      <span className="muted">{title}</span>
      <span>
        {items.map((x) => (
          <Tag key={x} tone={tone}>
            {x}
          </Tag>
        ))}
      </span>
    </div>
  );
}

function CounterBlock({ title, data }: { title: string; data: Record<string, CounterDiff> }) {
  const entries = Object.entries(data ?? {});
  if (!entries.length) return null;
  return (
    <div>
      <h4>{title}</h4>
      {entries.map(([path, d]) => (
        <div key={path} className="counter-diff small">
          <code>{path}</code>
          {d.removed_count > 0 && (
            <div>
              <span className="del-count">removed ({d.removed_count}):</span> {d.removed.map((x, i) => <code key={i} className="chip del">{x}</code>)}
              {d.removed_count > d.removed.length && <span className="muted"> …</span>}
            </div>
          )}
          {d.added_count > 0 && (
            <div>
              <span className="add-count">added ({d.added_count}):</span> {d.added.map((x, i) => <code key={i} className="chip add">{x}</code>)}
              {d.added_count > d.added.length && <span className="muted"> …</span>}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export default function VerificationReport({ p }: { p: ProposalDetail }) {
  const r = p.report ?? {};
  const checks = r.checks ?? p.checks ?? [];
  const b = r.build;
  const a = r.analysis;
  if (!r.verified_at && p.status === "verifying") {
    return <p className="muted">Verification is running: the candidate is being compiled and checked…</p>;
  }
  return (
    <div className="verification">
      <section>
        <h3>Checks</h3>
        {checks.length === 0 ? (
          <p className="muted small">No checks were recorded.</p>
        ) : (
          <ul className="checks">
            {checks.map((c) => (
              <li key={c.check}>
                <Badge status={c.status}>{c.status === "not_evaluated" ? "not evaluated" : c.status}</Badge>
                <span>{CHECK_LABELS[c.check] ?? c.check}</span>
                {c.detail && <span className="muted small">— {c.detail}</span>}
              </li>
            ))}
          </ul>
        )}
        {r.scope_ok === false && <p className="text-bad small">The tool changed files outside the allowed write scope.</p>}
      </section>

      {b && (
        <section>
          <h3>Build</h3>
          <div className="kv small">
            <span>Candidate</span>
            <span>
              <Badge status={b.candidate} /> {b.candidate_isolation && <Tag tone={b.candidate_isolation === "bwrap" ? "muted" : "warn"}>isolation: {b.candidate_isolation}</Tag>}
            </span>
            <span>Base (before the change)</span>
            <span>
              <Badge status={b.base} />
            </span>
            {b.blocked_reason && (
              <>
                <span>Blocked</span>
                <span className="text-bad">{b.blocked_reason}</span>
              </>
            )}
            {b.warnings_base && b.warnings_candidate && (
              <>
                <span>Warnings (base → candidate)</span>
                <span>
                  {Object.keys(b.warnings_candidate).map((k) => {
                    const d = (b.warnings_candidate[k] ?? 0) - (b.warnings_base[k] ?? 0);
                    return (
                      <span key={k} className={`warn-delta ${d > 0 ? "text-bad" : ""}`}>
                        {k}: {b.warnings_base[k] ?? 0} → {b.warnings_candidate[k]}
                        {d !== 0 ? ` (${d > 0 ? "+" : ""}${d})` : ""}
                      </span>
                    );
                  })}
                </span>
              </>
            )}
          </div>
        </section>
      )}

      {a && (
        <section>
          <h3>Textual analysis</h3>
          <div className="kv-list small">
            <List title="Citations added" items={a.citations.added} tone="ok" />
            <List title="Citations removed" items={a.citations.removed} tone="bad" />
            <List title="Bib entries added" items={a.citations.bib_entries_added} tone="ok" />
            <List title="Bib entries removed" items={a.citations.bib_entries_removed} tone="bad" />
            <List title="New undefined citations" items={a.citations.new_undefined} tone="bad" />
            <List title="Labels added" items={a.labels.added} tone="ok" />
            <List title="Labels removed" items={a.labels.removed} tone="bad" />
            <List title="New dangling references" items={a.labels.new_dangling_refs} tone="bad" />
          </div>
          {!a.citations.added.length &&
            !a.citations.removed.length &&
            !a.citations.new_undefined.length &&
            !a.labels.added.length &&
            !a.labels.removed.length &&
            !a.labels.new_dangling_refs.length && <p className="muted small">No citation or label changes.</p>}
          <CounterBlock title="Numbers changed" data={a.numbers} />
          <CounterBlock title="Equations changed" data={a.equations} />
          {!Object.keys(a.numbers ?? {}).length && !Object.keys(a.equations ?? {}).length && (
            <p className="muted small">No numbers or equations changed in the edited .tex files.</p>
          )}
        </section>
      )}

      {r.limitations && r.limitations.length > 0 && (
        <section>
          <h3>Limitations</h3>
          <ul className="limitations">
            {r.limitations.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        </section>
      )}

      {typeof r.tool_message === "string" && r.tool_message && (
        <details>
          <summary>Message from the tool</summary>
          <p className="pre-wrap small">{r.tool_message}</p>
        </details>
      )}
    </div>
  );
}
