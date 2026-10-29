import type { Hunk } from "../types";

/** Renders hunks from the backend diff (lines tagged ' ', '-', '+'). */
export function HunkView({
  hunk,
  selectable,
  checked,
  onToggle,
  path,
}: {
  hunk: Hunk;
  selectable?: boolean;
  checked?: boolean;
  onToggle?: (v: boolean) => void;
  path: string;
}) {
  // Compute line numbers on both sides.
  let oldNo = hunk.base_start - hunk.context_before;
  let newNo = hunk.new_start - hunk.context_before;
  const rows = hunk.lines.map((l, i) => {
    const o = l.t === "+" ? "" : String(oldNo++);
    const n = l.t === "-" ? "" : String(newNo++);
    return (
      <tr key={i} className={l.t === "+" ? "add" : l.t === "-" ? "del" : "ctx"}>
        <td className="ln">{o}</td>
        <td className="ln">{n}</td>
        <td className="sign" aria-hidden="true">
          {l.t === " " ? "" : l.t}
        </td>
        <td className="code">
          <span className="sr-only">{l.t === "+" ? "added: " : l.t === "-" ? "removed: " : ""}</span>
          {l.s || " "}
        </td>
      </tr>
    );
  });
  const range =
    hunk.kind === "insert"
      ? hunk.base_end === 0
        ? "insert at start of file"
        : `insert after line ${hunk.base_end}`
      : `lines ${hunk.base_start}–${Math.max(hunk.base_start, hunk.base_end)}`;
  return (
    <div className={`hunk ${selectable && !checked ? "hunk-off" : ""}`}>
      <div className="hunk-head">
        {selectable ? (
          <label className="check">
            <input type="checkbox" checked={!!checked} onChange={(e) => onToggle?.(e.target.checked)} aria-label={`Include change ${hunk.id + 1} in ${path} (${range})`} />
            <span>
              Change {hunk.id + 1} · {hunk.kind} · {range}
            </span>
          </label>
        ) : (
          <span>
            Change {hunk.id + 1} · {hunk.kind} · {range}
          </span>
        )}
      </div>
      <div className="diff-scroll">
        <table className="diff-table">
          <tbody>{rows}</tbody>
        </table>
      </div>
    </div>
  );
}

export function FileDiffHeader({ path, change, stats }: { path: string; change: string; stats?: { added?: number; removed?: number } }) {
  return (
    <div className="file-diff-head">
      <code className="path">{path}</code>
      <span className={`badge badge-${change === "added" ? "ok" : change === "deleted" ? "bad" : "info"}`}>{change}</span>
      {stats && (stats.added != null || stats.removed != null) && (
        <span className="small">
          <span className="add-count">+{stats.added ?? 0}</span> <span className="del-count">−{stats.removed ?? 0}</span>
        </span>
      )}
    </div>
  );
}
