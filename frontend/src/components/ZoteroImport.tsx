import { useState, type FormEvent } from "react";
import { api } from "../api";
import type { ZoteroCollection, ZoteroLibrary, ZoteroResult } from "../types";
import { useAction } from "../util";
import { ErrorBox, Spinner } from "./ui";

// Imports BibTeX from a Zotero library. The API key is kept in this component's memory only: it is sent with
// each request, never stored. A public group library needs no key.
export default function ZoteroImport({ pid, canEdit, onImported }: { pid: string; canEdit: boolean; onImported: () => void }) {
  const [key, setKey] = useState("");
  const [libs, setLibs] = useState<ZoteroLibrary[] | null>(null);
  const [lib, setLib] = useState("");
  const [groupId, setGroupId] = useState("");
  const [cols, setCols] = useState<ZoteroCollection[] | null>(null);
  const [col, setCol] = useState("");
  const [result, setResult] = useState<ZoteroResult | null>(null);

  const library = lib === "public" ? (/^\d{1,12}$/.test(groupId.trim()) ? `group:${groupId.trim()}` : "") : lib;
  const apiKey = key.trim() || null;

  const connect = useAction(async () => {
    const l = await api.zoteroLibraries(key.trim());
    setLibs(l);
    setLib(l[0]?.id ?? "");
    setCols(null);
    setCol("");
  });
  const loadCols = useAction(async () => {
    setCols(await api.zoteroCollections(library, apiKey));
    setCol("");
  });
  const doImport = useAction(async () => {
    setResult(await api.zoteroImport(pid, library, apiKey, col || null));
    onImported();
  });

  function connectSubmit(e: FormEvent) {
    e.preventDefault();
    if (key.trim()) void connect.run();
  }

  return (
    <details className="zotero-import">
      <summary className="small">Import from Zotero</summary>
      <form className="side-form" onSubmit={connectSubmit} autoComplete="off">
        <label className="small muted" htmlFor="zot-key">
          API key (zotero.org/settings/keys) — not stored, only sent to Zotero
        </label>
        <div className="row-actions">
          <input id="zot-key" type="password" autoComplete="off" value={key} maxLength={100} onChange={(e) => setKey(e.target.value)} placeholder="for your own or a private library" />
          <button className="btn btn-sm" type="submit" disabled={!key.trim() || connect.busy}>
            {connect.busy ? <Spinner /> : "Connect"}
          </button>
        </div>
      </form>
      <ErrorBox error={connect.error} />
      <div className="side-form">
        <label className="small muted" htmlFor="zot-lib">
          Library
        </label>
        <select
          id="zot-lib"
          value={lib}
          onChange={(e) => {
            setLib(e.target.value);
            setCols(null);
            setCol("");
          }}
        >
          <option value="">Choose…</option>
          {libs?.map((l) => (
            <option key={l.id} value={l.id}>
              {l.name}
            </option>
          ))}
          <option value="public">Public group by id…</option>
        </select>
        {lib === "public" && (
          <input aria-label="Public group id" inputMode="numeric" placeholder="group id (a number)" value={groupId} maxLength={12} onChange={(e) => setGroupId(e.target.value.replace(/\D/g, ""))} />
        )}
        {library && (
          <div className="row-actions">
            <button className="btn btn-sm" onClick={() => void loadCols.run()} disabled={loadCols.busy || (library === "user" && !apiKey)}>
              {loadCols.busy ? <Spinner /> : "Load collections"}
            </button>
            {cols && (
              <select aria-label="Collection" value={col} onChange={(e) => setCol(e.target.value)}>
                <option value="">All items</option>
                {cols.map((c) => (
                  <option key={c.key} value={c.key}>
                    {c.name} ({c.items})
                  </option>
                ))}
              </select>
            )}
          </div>
        )}
        <ErrorBox error={loadCols.error} />
        <button className="btn btn-sm btn-primary" onClick={() => void doImport.run()} disabled={!canEdit || !library || doImport.busy || (library === "user" && !apiKey)}>
          {doImport.busy ? <Spinner /> : "Import into bibliography"}
        </button>
        <ErrorBox error={doImport.error} />
        {result && (
          <p className="small side-note" role="status">
            Added {result.added} to <code>{result.file}</code>; {result.skipped_existing} already there
            {result.renamed.length > 0 && <>; {result.renamed.length} keys renamed to avoid clashes</>}
            {result.truncated && <> · only the first 500 of {result.total_in_zotero} were imported</>}.
          </p>
        )}
      </div>
    </details>
  );
}
