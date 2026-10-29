import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import type { Version } from "../types";
import { fmtTime, relTime, shortRev, useAction, useAsync } from "../util";
import { FileDiffHeader, HunkView } from "./DiffView";
import { IconRetry } from "./Icons";
import { ConfirmButton, Empty, ErrorBox, Loading, Modal, Tag } from "./ui";

export default function HistoryPanel({ pid, canEdit }: { pid: string; canEdit: boolean }) {
  const list = useAsync(() => api.versions(pid), [pid]);
  const [label, setLabel] = useState("");
  const [diffFor, setDiffFor] = useState<Version | null>(null);
  const [labelledOnly, setLabelledOnly] = useState(false);
  const navigate = useNavigate();
  const save = useAction(async () => {
    await api.saveVersion(pid, label.trim());
    setLabel("");
    await list.reload();
  });
  const restore = useAction(async (rev: string) => {
    const prop = await api.restoreVersion(pid, rev);
    navigate(`/p/${pid}/review/${prop.id}`);
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    if (label.trim()) void save.run();
  }

  const versions = (list.data ?? []).filter((v) => !labelledOnly || v.labels.length > 0);

  return (
    <div className="side-panel">
      <div className="side-head">
        <span className="side-title">History</span>
        <div className="spacer" />
        <button className="icon-btn sm" aria-label="Refresh history" onClick={list.reload}>
          <IconRetry />
        </button>
      </div>
      {canEdit && (
        <form className="side-form" onSubmit={submit}>
          <label className="sr-only" htmlFor="ver-label">
            Version label
          </label>
          <div className="row-actions">
            <input id="ver-label" placeholder="Label, e.g. Submitted draft" maxLength={200} value={label} onChange={(e) => setLabel(e.target.value)} />
            <button className="btn btn-sm" type="submit" disabled={!label.trim() || save.busy}>
              Save version
            </button>
          </div>
        </form>
      )}
      <label className="check small side-note">
        <input type="checkbox" checked={labelledOnly} onChange={(e) => setLabelledOnly(e.target.checked)} /> Only labelled versions
      </label>
      <ErrorBox error={list.error || save.error || restore.error} onRetry={list.error ? list.reload : undefined} />
      <div className="side-scroll">
        {list.loading && !list.data && <Loading />}
        {list.data && versions.length === 0 && <Empty>No versions yet.</Empty>}
        <ul className="versions">
          {versions.map((v) => (
            <li key={v.revision} className="version">
              <div className="version-main">
                {v.labels.map((l) => (
                  <Tag key={l} tone="accent">
                    {l}
                  </Tag>
                ))}
                <span className="ellipsis" title={v.message}>
                  {v.message}
                </span>
              </div>
              <div className="muted tiny" title={fmtTime(v.date)}>
                <code>{shortRev(v.revision)}</code> · {v.author} · {relTime(v.date)}
              </div>
              <div className="row-actions">
                <button className="btn btn-xs" onClick={() => setDiffFor(v)}>
                  Diff vs current
                </button>
                {canEdit && (
                  <ConfirmButton
                    className="btn btn-xs"
                    label="Restore…"
                    question="Create a restore proposal?"
                    confirmLabel="Create proposal"
                    onConfirm={() => restore.run(v.revision)}
                  />
                )}
              </div>
            </li>
          ))}
        </ul>
      </div>
      {diffFor && <VersionDiffDialog pid={pid} version={diffFor} onClose={() => setDiffFor(null)} />}
    </div>
  );
}

function VersionDiffDialog({ pid, version, onClose }: { pid: string; version: Version; onClose: () => void }) {
  const d = useAsync(() => api.versionDiff(pid, version.revision), [pid, version.revision]);
  return (
    <Modal title={`Changes since ${shortRev(version.revision)}${version.labels.length ? ` (${version.labels.join(", ")})` : ""}`} onClose={onClose} wide>
      <p className="muted small">
        Lines marked “−” exist in version {shortRev(version.revision)} ({fmtTime(version.date)}); lines marked “+” are in the current manuscript.
      </p>
      <ErrorBox error={d.error} onRetry={d.reload} />
      {d.loading && !d.data && <Loading />}
      {d.data && d.data.files.length === 0 && <Empty>The current manuscript is identical to this version.</Empty>}
      {d.data?.files.map((f) => (
        <section key={f.path} className="file-diff">
          <FileDiffHeader path={f.path} change={f.change} />
          {f.hunks ? f.hunks.map((h) => <HunkView key={h.id} hunk={h} path={f.path} />) : <p className="muted small">Binary file — no text diff.</p>}
        </section>
      ))}
    </Modal>
  );
}
