import { api } from "../api";
import type { CollabSession } from "../collab";
import type { CompletionData } from "../editor/setup";
import type { Diagnostic, FileEntry } from "../types";
import { basename, fmtBytes, isImagePath } from "../util";
import { CollabEditor, RestEditor, type DocCache, type JumpTarget, type SaveState, type SelectionInfo } from "./CodeEditor";
import { IconDownload, IconX } from "./Icons";
import PdfViewer from "./PdfViewer";
import { Empty, Loading } from "./ui";

interface Props {
  pid: string;
  tabs: string[];
  active: string | null;
  files: FileEntry[] | undefined;
  onActivate: (path: string) => void;
  onClose: (path: string) => void;
  readOnly: boolean;
  collab: { enabled: boolean | undefined; session: CollabSession | null };
  cache: DocCache;
  saveStates: Record<string, SaveState>;
  jump: JumpTarget | null;
  diagnostics: Diagnostic[];
  completion: { current: CompletionData };
  onSelection: (s: SelectionInfo | null) => void;
  onSaveState: (path: string, s: SaveState) => void;
  onSaved: () => void;
}

function tabMarker(s: SaveState | undefined): string {
  if (!s) return "";
  if (s.kind === "dirty" || s.kind === "saving") return "•";
  if (s.kind === "error" || s.kind === "conflict") return "!";
  return "";
}

export default function EditorArea(p: Props) {
  const entry = p.active ? p.files?.find((f) => f.path === p.active) : undefined;
  const fileDiags = p.active ? p.diagnostics.filter((d) => d.file && d.file.replace(/^\.\//, "") === p.active) : [];

  let body;
  if (!p.active) {
    body = <Empty>Select a file in the sidebar to start editing.</Empty>;
  } else if (!p.files || p.collab.enabled === undefined) {
    body = <Loading />;
  } else if (!entry) {
    body = (
      <Empty>
        <code>{p.active}</code> no longer exists in the project.
      </Empty>
    );
  } else if (!entry.is_text) {
    body = <BinaryPreview pid={p.pid} file={entry} />;
  } else if (p.collab.enabled && p.collab.session) {
    body = (
      <CollabEditor
        key={p.active}
        pid={p.pid}
        path={p.active}
        session={p.collab.session}
        readOnly={p.readOnly}
        jump={p.jump}
        diagnostics={fileDiags}
        completion={p.completion}
        onSelection={p.onSelection}
      />
    );
  } else {
    body = (
      <RestEditor
        key={p.active}
        pid={p.pid}
        path={p.active}
        readOnly={p.readOnly}
        cache={p.cache}
        jump={p.jump}
        diagnostics={fileDiags}
        completion={p.completion}
        onSelection={p.onSelection}
        onSaveState={p.onSaveState}
        onSaved={p.onSaved}
      />
    );
  }

  return (
    <div className="editor-area">
      <div className="file-tabs" role="tablist" aria-label="Open files">
        {p.tabs.map((t) => {
          const mark = tabMarker(p.saveStates[t]);
          return (
            <div key={t} className={`file-tab ${t === p.active ? "active" : ""}`} title={t}>
              <button role="tab" aria-selected={t === p.active} className="file-tab-btn" onClick={() => p.onActivate(t)}>
                {basename(t)}
                {mark && (
                  <span className={`tab-mark ${mark === "!" ? "bad" : ""}`} aria-label={mark === "!" ? "not saved" : "unsaved changes"}>
                    {mark}
                  </span>
                )}
              </button>
              <button className="file-tab-close" aria-label={`Close ${t}`} onClick={() => p.onClose(t)}>
                <IconX width={12} height={12} />
              </button>
            </div>
          );
        })}
      </div>
      <div className="editor-body">{body}</div>
    </div>
  );
}

function BinaryPreview({ pid, file }: { pid: string; file: FileEntry }) {
  const url = api.rawUrl(pid, file.path);
  const isPdf = /\.pdf$/i.test(file.path);
  return (
    <div className="binary-preview">
      <div className="row-actions binary-head">
        <code>{file.path}</code>
        <span className="muted small">
          {fmtBytes(file.size)} · binary file (not editable here)
        </span>
        <div className="spacer" />
        <a className="btn btn-sm" href={url} download={basename(file.path)}>
          <IconDownload /> Download
        </a>
      </div>
      {isImagePath(file.path) ? (
        <div className="image-preview">
          <img src={url} alt={`Preview of ${file.path}`} />
        </div>
      ) : isPdf ? (
        <PdfViewer url={url} downloadName={basename(file.path)} />
      ) : (
        <Empty>No preview available for this file type.</Empty>
      )}
    </div>
  );
}
