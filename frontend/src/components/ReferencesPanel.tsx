import { useState, type FormEvent } from "react";
import { api } from "../api";
import type { BibStatus, BibVerifyReport, PaperHit, ReferenceImport } from "../types";
import { useAction } from "../util";
import { Empty, ErrorBox, Spinner } from "./ui";
import ZoteroImport from "./ZoteroImport";

// arXiv search + BibTeX import (arXiv id or DOI). Entries are written to the
// project's .bib exactly as arXiv / doi.org return them.
const STATUS_LABEL: Record<BibStatus, string> = {
  verified: "verified",
  mismatch: "different work",
  not_found: "not found",
  unchecked: "not checked",
};

// Looks every entry up in public databases. "not found" is a hint, not a verdict: books, theses and
// non-English works are often missing from them.
function VerifyBibliography({ pid, canEdit, onJump }: { pid: string; canEdit: boolean; onJump: (path: string, line: number) => void }) {
  const [rep, setRep] = useState<BibVerifyReport | null>(null);
  const [all, setAll] = useState(false);
  const run = useAction(async () => setRep(await api.verifyReferences(pid)));
  const shown = rep?.results.filter((x) => all || x.status !== "verified") ?? [];
  return (
    <div className="verify-bib">
      <div className="row-actions">
        <span className="small muted">Check the bibliography against Crossref, arXiv and OpenAlex</span>
        <div className="spacer" />
        <button className="btn btn-sm" onClick={() => void run.run()} disabled={!canEdit || run.busy}>
          {run.busy ? <Spinner /> : rep ? "Re-check" : "Verify"}
        </button>
      </div>
      <ErrorBox error={run.error} />
      {rep && (
        <>
          <p className="small side-note" role="status">
            {(["verified", "mismatch", "not_found", "unchecked"] as const).map((s) => (
              <span key={s} className={`check-count bib-${s}`}>
                {rep.counts[s]} {STATUS_LABEL[s]}
              </span>
            ))}
            {rep.truncated && <span className="muted"> · first {rep.checked} of {rep.total} entries</span>}
            <label className="check tiny">
              <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> show verified
            </label>
          </p>
          {shown.length === 0 && <Empty>{rep.results.length ? "Everything checked was verified." : "No entries in the .bib files."}</Empty>}
          <ul className="search-results">
            {shown.map((x) => (
              <li key={`${x.file}:${x.key}`}>
                <button className="search-row" onClick={() => x.line && onJump(x.file, x.line)}>
                  <span className={`tiny bib-${x.status}`}>
                    {STATUS_LABEL[x.status]} · {x.key}
                  </span>
                  <span className="small">{x.detail}</span>
                  {x.found_title && (
                    <span className="tiny muted">
                      {x.source}: {x.found_title} {x.found_id && `(${x.found_id})`}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

export default function ReferencesPanel({
  pid,
  canEdit,
  onImported,
  onJump,
}: {
  pid: string;
  canEdit: boolean;
  onImported: () => void;
  onJump: (path: string, line: number) => void;
}) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<{ q: string; items: PaperHit[] } | null>(null);
  const [ident, setIdent] = useState("");
  const [last, setLast] = useState<ReferenceImport | null>(null);
  const search = useAction(async () => setHits({ q, items: await api.searchPapers(q) }));
  const add = useAction(async (source: "arxiv" | "doi", id: string) => {
    const r = await api.importReference(pid, source, id);
    setLast(r);
    onImported();
  });

  function submitSearch(e: FormEvent) {
    e.preventDefault();
    if (q.trim()) void search.run();
  }
  function submitId(e: FormEvent) {
    e.preventDefault();
    const v = ident.trim();
    if (v) void add.run(/^(https?:\/\/(dx\.)?doi\.org\/|doi:)?10\./i.test(v) ? "doi" : "arxiv", v);
  }
  async function copyCite(key: string) {
    try {
      await navigator.clipboard.writeText(`\\cite{${key}}`);
    } catch {
      /* clipboard may be unavailable; the key is shown on screen */
    }
  }

  return (
    <div className="side-panel">
      <div className="side-head">
        <span className="side-title">References</span>
      </div>
      <form className="side-form" onSubmit={submitId}>
        <label className="small muted" htmlFor="ref-id">
          Add by arXiv id or DOI
        </label>
        <div className="row-actions">
          <input id="ref-id" placeholder="1706.03762 or 10.1038/…" value={ident} maxLength={300} onChange={(e) => setIdent(e.target.value)} />
          <button className="btn btn-sm" type="submit" disabled={!canEdit || !ident.trim() || add.busy}>
            {add.busy ? <Spinner /> : "Add"}
          </button>
        </div>
      </form>
      {last && (
        <p className="small side-note" role="status">
          {last.added ? "Added" : "Already in"} <code>{last.file}</code> as <code>{last.key}</code>{" "}
          <button className="btn btn-sm btn-ghost" onClick={() => void copyCite(last.key)}>
            Copy \cite
          </button>
        </p>
      )}
      <ErrorBox error={add.error} />
      <form className="side-form" onSubmit={submitSearch} role="search">
        <label className="small muted" htmlFor="ref-q">
          Search arXiv
        </label>
        <div className="row-actions">
          <input id="ref-q" type="search" placeholder="title, author, topic" value={q} maxLength={300} onChange={(e) => setQ(e.target.value)} />
          <button className="btn btn-sm" type="submit" disabled={!q.trim() || search.busy}>
            {search.busy ? <Spinner /> : "Search"}
          </button>
        </div>
      </form>
      <ErrorBox error={search.error} />
      <div className="side-scroll">
        <VerifyBibliography pid={pid} canEdit={canEdit} onJump={onJump} />
        <ZoteroImport pid={pid} canEdit={canEdit} onImported={onImported} />
        {hits && hits.items.length === 0 && <Empty>No arXiv results for “{hits.q}”.</Empty>}
        {hits && hits.items.length > 0 && (
          <ul className="paper-hits">
            {hits.items.map((h) => (
              <li key={h.id} className="paper-hit">
                <a href={h.url} target="_blank" rel="noreferrer" className="paper-title">
                  {h.title}
                </a>
                <span className="muted tiny">
                  {h.authors.slice(0, 3).join(", ")}
                  {h.authors.length > 3 ? " et al." : ""} · {h.year} · arXiv:{h.id}
                </span>
                <details>
                  <summary className="tiny muted">Abstract</summary>
                  <p className="tiny">{h.summary}</p>
                </details>
                <button className="btn btn-sm" disabled={!canEdit || add.busy} onClick={() => void add.run("arxiv", h.id)}>
                  Add to bibliography
                </button>
              </li>
            ))}
          </ul>
        )}
        {!canEdit && <p className="muted small side-note">Viewers can search but not change the bibliography.</p>}
      </div>
    </div>
  );
}
