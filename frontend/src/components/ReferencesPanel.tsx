import { useState, type FormEvent } from "react";
import { api } from "../api";
import type { PaperHit, ReferenceImport } from "../types";
import { useAction } from "../util";
import { Empty, ErrorBox, Spinner } from "./ui";

// arXiv search + BibTeX import (arXiv id or DOI). Entries are written to the
// project's .bib exactly as arXiv / doi.org return them.
export default function ReferencesPanel({ pid, canEdit, onImported }: { pid: string; canEdit: boolean; onImported: () => void }) {
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
