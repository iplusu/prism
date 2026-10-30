import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { useApp } from "../appContext";
import { AppHeader } from "../components/AppHeader";
import { IconPlus, IconSearch, IconSettings, IconUpload } from "../components/Icons";
import { Badge, ConfirmButton, Empty, ErrorBox, Loading, Modal, Tag } from "../components/ui";
import type { Project, TemplateInfo } from "../types";
import { fmtTime, relTime, useAction, useAsync } from "../util";

export default function ProjectsPage() {
  const { user } = useApp();
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [dialog, setDialog] = useState<"new" | "import" | null>(null);
  const [imported, setImported] = useState<Project | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(q.trim()), 250);
    return () => window.clearTimeout(t);
  }, [q]);

  const list = useAsync(() => api.projects(debounced), [debounced]);
  const del = useAction(async (pid: string) => {
    await api.deleteProject(pid);
    await list.reload();
  });

  useEffect(() => {
    document.title = "Projects · Prism";
  }, []);

  return (
    <div className="page">
      <AppHeader title="Projects">
        <Link to="/settings" className="btn btn-ghost" aria-label="System settings">
          <IconSettings /> <span className="hide-narrow">Settings</span>
        </Link>
      </AppHeader>
      <main className="page-main">
        <div className="toolbar">
          <label className="search-box">
            <IconSearch />
            <span className="sr-only">Search projects</span>
            <input type="search" placeholder="Search projects" value={q} onChange={(e) => setQ(e.target.value)} />
          </label>
          <div className="spacer" />
          <button className="btn" onClick={() => setDialog("import")}>
            <IconUpload /> Import ZIP
          </button>
          <button className="btn btn-primary" onClick={() => setDialog("new")}>
            <IconPlus /> New project
          </button>
        </div>

        {imported?.import && (
          <div className="notice" role="status">
            <div>
              Imported <strong>{imported.name}</strong>: {imported.import.files} file(s)
              {imported.import.stripped_prefix ? ` (top-level folder “${imported.import.stripped_prefix}” removed)` : ""}. Main file:{" "}
              <code>{imported.main_file}</code>.
            </div>
            {imported.import.skipped.length > 0 && (
              <details open>
                <summary>{imported.import.skipped.length} entr{imported.import.skipped.length === 1 ? "y was" : "ies were"} skipped</summary>
                <ul className="plain-list small">
                  {imported.import.skipped.map((s, i) => (
                    <li key={i}>
                      <code>{s.path}</code> — {s.reason}
                    </li>
                  ))}
                </ul>
              </details>
            )}
            <div className="row-actions">
              <Link className="btn btn-sm btn-primary" to={`/p/${imported.id}`}>
                Open project
              </Link>
              <button className="btn btn-sm" onClick={() => setImported(null)}>
                Dismiss
              </button>
            </div>
          </div>
        )}

        <ErrorBox error={list.error} onRetry={list.reload} />
        <ErrorBox error={del.error} />
        {list.loading && !list.data ? (
          <Loading text="Loading projects…" />
        ) : list.data && list.data.length === 0 ? (
          <Empty>
            {debounced ? (
              <>No projects match “{debounced}”.</>
            ) : (
              <>
                <p>No projects yet.</p>
                <p className="muted">Create a blank paper, start from the sample paper, or import a LaTeX ZIP.</p>
              </>
            )}
          </Empty>
        ) : (
          list.data && (
            <div className="table-wrap">
              <table className="table projects-table">
                <thead>
                  <tr>
                    <th scope="col">Name</th>
                    <th scope="col">Updated</th>
                    <th scope="col">Last build</th>
                    <th scope="col">AI activity</th>
                    <th scope="col">Role</th>
                    <th scope="col">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {list.data.map((p) => (
                    <tr key={p.id}>
                      <td>
                        <Link to={`/p/${p.id}`} className="project-link">
                          {p.name}
                        </Link>
                        {p.description && <div className="muted small ellipsis">{p.description}</div>}
                      </td>
                      <td title={fmtTime(p.updated_at)}>{relTime(p.updated_at)}</td>
                      <td>
                        {p.last_build ? (
                          <span title={p.last_build.error || fmtTime(p.last_build.created_at)}>
                            <Badge status={p.last_build.status} />
                          </span>
                        ) : (
                          <span className="muted">never</span>
                        )}
                      </td>
                      <td>
                        {p.active_tasks > 0 && <Tag tone="info">{p.active_tasks} running</Tag>}{" "}
                        {p.open_proposals > 0 && <Tag tone="warn">{p.open_proposals} to review</Tag>}
                        {!p.active_tasks && !p.open_proposals && <span className="muted">—</span>}
                      </td>
                      <td>{p.role && <Badge status={p.role} />}</td>
                      <td className="row-actions">
                        <Link className="btn btn-sm" to={`/p/${p.id}/settings?tab=project`}>
                          Settings
                        </Link>
                        <Link className="btn btn-sm" to={`/p/${p.id}/settings?tab=members`}>
                          Members
                        </Link>
                        {p.role === "owner" && (
                          <ConfirmButton
                            label="Delete"
                            question={`Delete “${p.name}” permanently?`}
                            confirmLabel="Delete"
                            onConfirm={() => del.run(p.id)}
                          />
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        )}
        <p className="muted small">
          Signed in as {user.display_name}
          {user.is_admin ? " (administrator — all projects are listed)" : ""}.
        </p>
      </main>

      {dialog === "new" && (
        <NewProjectDialog onClose={() => setDialog(null)} onCreated={(p) => navigate(`/p/${p.id}`)} />
      )}
      {dialog === "import" && (
        <ImportDialog
          onClose={() => setDialog(null)}
          onImported={(p) => {
            setDialog(null);
            setImported(p);
            void list.reload();
          }}
        />
      )}
    </div>
  );
}

function NewProjectDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (p: Project) => void }) {
  const [name, setName] = useState("");
  const [template, setTemplate] = useState<string>("blank");
  const [kits, setKits] = useState<TemplateInfo[]>([]);
  const act = useAction(api.createProject);
  useEffect(() => {
    api.templates().then(setKits, () => setKits([])); // the built-in choices still work without the catalogue
  }, []);
  async function submit(e: FormEvent) {
    e.preventDefault();
    const p = await act.run(name.trim(), template);
    if (p) onCreated(p);
  }
  return (
    <Modal title="New project" onClose={onClose}>
      <form onSubmit={submit} className="form">
        <label className="field">
          <span>Name</span>
          <input required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <fieldset className="field">
          <legend>Template</legend>
          <label className="radio">
            <input type="radio" name="tpl" checked={template === "blank"} onChange={() => setTemplate("blank")} />
            Blank paper <span className="muted small">(main.tex + empty references.bib)</span>
          </label>
          <label className="radio">
            <input type="radio" name="tpl" checked={template === "sample"} onChange={() => setTemplate("sample")} />
            Sample paper <span className="muted small">(sections, figures and bibliography)</span>
          </label>
          {kits.map((k) => (
            <label className="radio radio-block" key={k.id}>
              <input type="radio" name="tpl" checked={template === k.id} onChange={() => setTemplate(k.id)} />
              <span>
                {k.label}
                <span className="muted small radio-desc">{k.description}</span>
              </span>
            </label>
          ))}
          {kits.some((k) => k.id === template) && (
            <p className="muted small">
              The official kit is downloaded from {kits.find((k) => k.id === template)?.source} when the project is created, so this needs
              internet access from the server and can take a few seconds.
            </p>
          )}
        </fieldset>
        <ErrorBox error={act.error} />
        <div className="form-actions">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={act.busy || !name.trim()}>
            {act.busy ? "Creating…" : "Create"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function ImportDialog({ onClose, onImported }: { onClose: () => void; onImported: (p: Project) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const act = useAction(api.importProject);
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    const p = await act.run(file, name.trim());
    if (p) onImported(p);
  }
  return (
    <Modal title="Import LaTeX project (ZIP)" onClose={onClose}>
      <form onSubmit={submit} className="form">
        <label className="field">
          <span>ZIP archive</span>
          <input ref={inputRef} type="file" accept=".zip,application/zip" required onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>
        <label className="field">
          <span>Project name (optional)</span>
          <input maxLength={200} placeholder={file ? file.name.replace(/\.zip$/i, "") : ""} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <p className="muted small">
          Symbolic links and foreign <code>.git</code> data are skipped; the main file is detected automatically and can be changed later in
          project settings.
        </p>
        <ErrorBox error={act.error} />
        <div className="form-actions">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={act.busy || !file}>
            {act.busy ? "Importing…" : "Import"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
