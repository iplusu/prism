import { useState, type FormEvent } from "react";
import { api } from "../api";
import type { SearchResult } from "../types";
import { useAction } from "../util";
import { Empty, ErrorBox, Spinner } from "./ui";

export default function SearchPanel({ pid, onJump }: { pid: string; onJump: (path: string, line: number) => void }) {
  const [q, setQ] = useState("");
  const [regex, setRegex] = useState(false);
  const [cs, setCs] = useState(false);
  const [res, setRes] = useState<{ results: SearchResult[]; truncated: boolean; q: string } | null>(null);
  const act = useAction(async () => {
    const r = await api.search(pid, q, regex, cs);
    setRes({ ...r, q });
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    if (q.trim()) void act.run();
  }
  return (
    <div className="side-panel">
      <div className="side-head">
        <span className="side-title">Search project</span>
      </div>
      <form className="side-form" onSubmit={submit} role="search">
        <label className="sr-only" htmlFor="proj-search">
          Search text
        </label>
        <input id="proj-search" type="search" placeholder="Find in all files" value={q} maxLength={500} onChange={(e) => setQ(e.target.value)} />
        <div className="row-actions small">
          <label className="check">
            <input type="checkbox" checked={regex} onChange={(e) => setRegex(e.target.checked)} /> Regex
          </label>
          <label className="check">
            <input type="checkbox" checked={cs} onChange={(e) => setCs(e.target.checked)} /> Match case
          </label>
          <div className="spacer" />
          <button className="btn btn-sm" type="submit" disabled={!q.trim() || act.busy}>
            {act.busy ? <Spinner /> : "Search"}
          </button>
        </div>
      </form>
      <ErrorBox error={act.error} />
      <div className="side-scroll">
        {res && res.results.length === 0 && <Empty>No matches for “{res.q}”.</Empty>}
        {res && res.results.length > 0 && (
          <>
            <p className="muted small side-note" role="status">
              {res.results.length}
              {res.truncated ? "+" : ""} match{res.results.length === 1 ? "" : "es"}
              {res.truncated ? " (truncated at 500)" : ""}
            </p>
            <ul className="search-results">
              {res.results.map((r, i) => (
                <li key={i}>
                  <button className="search-row" onClick={() => onJump(r.path, r.line)}>
                    <span className="muted tiny">
                      {r.path}:{r.line}
                    </span>
                    <code className="ellipsis">{r.text.trim()}</code>
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}
