import { api } from "../api";
import type { Build, ProjectStatus } from "../types";
import { fmtDuration, fmtTime, relTime, shortRev } from "../util";
import PdfViewer from "./PdfViewer";
import { Empty, Spinner, Tag } from "./ui";

function BuildInfo({ b }: { b: Build }) {
  return (
    <span className="build-info small" title={`Build ${b.id}\nRevision ${b.revision}\nEngine ${b.engine}, main file ${b.main_file}\nIsolation: ${b.isolation || "n/a"}`}>
      Build {relTime(b.finished_at || b.created_at)} · <code>{shortRev(b.revision)}</code>
    </span>
  );
}

export default function PdfPanel({
  status,
  projectName,
  onShowLog,
  canCompile,
}: {
  status: ProjectStatus | undefined;
  projectName: string;
  onShowLog: () => void;
  canCompile: boolean;
}) {
  if (!status) {
    return (
      <div className="pdf-panel">
        <Empty>
          <Spinner /> Loading build status…
        </Empty>
      </div>
    );
  }
  const ok = status.last_success_build;
  const last = status.last_build;
  const lastIsNewer = last && (!ok || last.id !== ok.id);
  const building = last && (last.status === "queued" || last.status === "running");
  const lastFailed = lastIsNewer && last && (last.status === "failed" || last.status === "blocked" || last.status === "cancelled");

  const notices = (
    <>
      {building && (
        <div className="pdf-notice info" role="status">
          <Spinner label="Compiling" /> {last!.status === "queued" ? "Compile queued…" : "Compiling…"}
        </div>
      )}
      {lastFailed && last && (
        <div className={`pdf-notice ${last.status === "cancelled" ? "muted" : "bad"}`} role="alert">
          <div>
            <strong>
              Latest build {last.status === "blocked" ? "was blocked" : last.status}
            </strong>{" "}
            ({fmtTime(last.finished_at || last.created_at)}).
            {ok ? " Showing the last successful PDF." : ""}
          </div>
          {last.error && <div className="pdf-notice-detail">{last.error}</div>}
          <button className="link-btn small" onClick={onShowLog}>
            Open build log &amp; problems
          </button>
        </div>
      )}
      {status.live_error && (
        <div className="pdf-notice bad" role="alert">
          Live document state unavailable: {status.live_error}
        </div>
      )}
    </>
  );

  if (!ok || !ok.has_pdf) {
    return (
      <div className="pdf-panel">
        {notices}
        <Empty>
          {ok && !ok.has_pdf ? (
            <>The last successful build's PDF is no longer stored.{canCompile ? " Compile again." : ""}</>
          ) : (
            <>No PDF yet.{canCompile ? <> Press <strong>Compile</strong> to build the paper.</> : " An editor needs to compile the paper."}</>
          )}
        </Empty>
      </div>
    );
  }

  const header = (
    <div className="pdf-head-info">
      {status.pdf_stale && (
        <Tag tone="warn" title="The manuscript changed since this PDF was built">
          stale
        </Tag>
      )}
      <BuildInfo b={ok} />
      {ok.duration_seconds != null && <span className="muted tiny hide-narrow">{fmtDuration(ok.duration_seconds)}</span>}
      {ok.isolation && ok.isolation !== "bwrap" && <Tag tone="warn" title="Compilation was not sandboxed">{`isolation: ${ok.isolation}`}</Tag>}
    </div>
  );

  return (
    <div className="pdf-panel">
      {notices}
      <PdfViewer key={ok.id} url={api.buildPdfUrl(ok.id)} downloadName={`${projectName || "paper"}.pdf`} header={header} />
    </div>
  );
}
