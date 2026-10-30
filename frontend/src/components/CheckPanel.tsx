import { useState } from "react";
import { api } from "../api";
import type { ReviewReport } from "../types";
import { useAction } from "../util";
import { Empty, ErrorBox, Spinner } from "./ui";

const LABEL: Record<string, string> = { error: "Errors", warning: "Warnings", info: "Notes" };

// Deterministic manuscript checks (citations, references, labels, figures,
// inputs, TODOs). No AI involved; every item links to its file and line.
export default function CheckPanel({ pid, onJump }: { pid: string; onJump: (path: string, line: number) => void }) {
  const [rep, setRep] = useState<ReviewReport | null>(null);
  const [showInfo, setShowInfo] = useState(false);
  const act = useAction(async () => setRep(await api.review(pid)));
  const shown = rep?.issues.filter((i) => showInfo || i.severity !== "info") ?? [];
  return (
    <div className="side-panel">
      <div className="side-head">
        <span className="side-title">Manuscript check</span>
        <div className="spacer" />
        <button className="btn btn-sm" onClick={() => void act.run()} disabled={act.busy}>
          {act.busy ? <Spinner /> : rep ? "Re-run" : "Run checks"}
        </button>
      </div>
      <ErrorBox error={act.error} />
      {!rep && !act.busy && (
        <p className="muted small side-note">
          Finds undefined citations and references, duplicate labels or keys, missing figure and input files, and TODO
          markers. Runs on the live text; no AI involved.
        </p>
      )}
      {rep && (
        <div className="side-scroll">
          <p className="small side-note" role="status">
            {(["error", "warning", "info"] as const).map((s) => (
              <span key={s} className={`check-count sev-${s}`}>
                {rep.counts[s]} {LABEL[s].toLowerCase()}
              </span>
            ))}
            <label className="check tiny">
              <input type="checkbox" checked={showInfo} onChange={(e) => setShowInfo(e.target.checked)} /> show notes
            </label>
          </p>
          {shown.length === 0 && <Empty>{rep.issues.length ? "Only notes; tick “show notes” to see them." : "No issues found."}</Empty>}
          <ul className="search-results">
            {shown.map((i, n) => (
              <li key={n}>
                <button className="search-row" onClick={() => onJump(i.path, i.line ?? 1)}>
                  <span className={`tiny sev-${i.severity}`}>
                    {i.severity} · {i.path}
                    {i.line ? `:${i.line}` : ""}
                  </span>
                  <span className="small">{i.message}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
