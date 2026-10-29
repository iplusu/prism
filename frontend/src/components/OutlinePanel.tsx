import { api } from "../api";
import { useAsync } from "../util";
import { IconRetry } from "./Icons";
import { Empty, ErrorBox, Loading } from "./ui";

const LEVEL: Record<string, number> = { part: 0, chapter: 0, section: 1, subsection: 2, subsubsection: 3, paragraph: 4, subparagraph: 5 };

export default function OutlinePanel({
  pid,
  refreshKey,
  onJump,
}: {
  pid: string;
  refreshKey: unknown;
  onJump: (path: string, line: number) => void;
}) {
  const o = useAsync(() => api.outline(pid), [pid, refreshKey]);
  return (
    <div className="side-panel">
      <div className="side-head">
        <span className="side-title">Outline</span>
        <div className="spacer" />
        <button className="icon-btn sm" aria-label="Refresh outline" onClick={o.reload}>
          <IconRetry />
        </button>
      </div>
      <ErrorBox error={o.error} onRetry={o.reload} />
      <div className="side-scroll">
        {o.loading && !o.data && <Loading />}
        {o.data && o.data.sections.length === 0 && <Empty>No \section commands found.</Empty>}
        {o.data && o.data.sections.length > 0 && (
          <ul className="outline">
            {o.data.sections.map((s, i) => (
              <li key={i}>
                <button
                  className="outline-row"
                  style={{ paddingLeft: 8 + (LEVEL[s.level.replace("*", "")] ?? 1) * 12 }}
                  onClick={() => onJump(s.file, s.line)}
                  title={`${s.file}:${s.line}`}
                >
                  <span className="ellipsis">{s.title || `(${s.level})`}</span>
                  <span className="muted tiny">{s.file.split("/").pop()}:{s.line}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {o.data && (o.data.labels.length > 0 || o.data.bib.length > 0) && (
          <p className="muted small side-note">
            {o.data.labels.length} label(s), {o.data.bib.length} bibliography entr{o.data.bib.length === 1 ? "y" : "ies"}.
          </p>
        )}
      </div>
    </div>
  );
}
