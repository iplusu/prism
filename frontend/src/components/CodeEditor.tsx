import { EditorState, StateEffect, type Extension } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { useCallback, useEffect, useRef, useState } from "react";
import { yCollab, yUndoManagerKeymap } from "y-codemirror.next";
import * as Y from "yjs";
import { api, ApiError, errorMessage } from "../api";
import type { CollabSession } from "../collab";
import { buildExtensions, setDiagnostics, toCmDiagnostics, type CompletionData } from "../editor/setup";
import type { Diagnostic } from "../types";
import { ErrorBox, Loading } from "./ui";

export type SaveState =
  | { kind: "idle" }
  | { kind: "dirty" }
  | { kind: "saving" }
  | { kind: "saved"; at: number }
  | { kind: "error"; message: string }
  | { kind: "conflict" };

export interface SelectionInfo {
  path: string;
  from_line: number;
  to_line: number;
  text: string;
}

/** Per-file editor state kept across tab switches (REST mode). */
export interface CacheEntry {
  state: EditorState;
  sha: string;
  dirty: boolean;
  conflict: boolean;
  saving: boolean;
}
export type DocCache = Map<string, CacheEntry>;

export interface JumpTarget {
  path: string;
  line: number;
  nonce: number;
}

interface CommonProps {
  pid: string;
  path: string;
  readOnly: boolean;
  jump: JumpTarget | null;
  diagnostics: Diagnostic[];
  completion: { current: CompletionData };
  onSelection: (s: SelectionInfo | null) => void;
}

function selectionListener(path: string, cb: { current: (s: SelectionInfo | null) => void }): Extension {
  return EditorView.updateListener.of((u) => {
    if (!u.selectionSet && !u.docChanged && !u.focusChanged) return;
    const sel = u.state.selection.main;
    if (sel.empty) {
      cb.current(null);
      return;
    }
    const a = u.state.doc.lineAt(sel.from);
    const b = u.state.doc.lineAt(sel.to);
    cb.current({ path, from_line: a.number, to_line: b.number, text: u.state.sliceDoc(sel.from, sel.to) });
  });
}

/** Shared behaviour: diagnostics and jump-to-line on an existing view. */
function useViewEffects(view: EditorView | null, path: string, jump: JumpTarget | null, diagnostics: Diagnostic[]) {
  useEffect(() => {
    if (!view) return;
    view.dispatch(setDiagnostics(view.state, toCmDiagnostics(view.state.doc, diagnostics)));
  }, [view, diagnostics]);
  useEffect(() => {
    if (!view || !jump || jump.path !== path) return;
    const n = Math.min(Math.max(1, jump.line), view.state.doc.lines);
    const line = view.state.doc.line(n);
    view.dispatch({ selection: { anchor: line.from }, effects: EditorView.scrollIntoView(line.from, { y: "center" }) });
    view.focus();
  }, [view, jump, path]);
}

// ----------------------------------------------------------------- REST ---

async function saveEntry(
  pid: string,
  path: string,
  entry: CacheEntry,
  report: (s: SaveState) => void,
  onSaved: () => void,
): Promise<void> {
  if (!entry.dirty || entry.saving || entry.conflict) return;
  entry.saving = true;
  const doc = entry.state.doc;
  report({ kind: "saving" });
  try {
    const r = await api.saveFile(pid, path, doc.toString(), entry.sha);
    entry.sha = r.sha;
    entry.dirty = entry.state.doc !== doc;
    report(entry.dirty ? { kind: "dirty" } : { kind: "saved", at: Date.now() });
    onSaved();
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      entry.conflict = true;
      report({ kind: "conflict" });
    } else {
      report({ kind: "error", message: errorMessage(e) });
    }
  } finally {
    entry.saving = false;
  }
}

/**
 * Save every dirty, non-conflicted file now (REST mode). Used before a
 * compile so the build sees the latest text. Returns paths that could not be saved.
 */
export async function flushAll(
  pid: string,
  cache: DocCache,
  report: (path: string, s: SaveState) => void,
  onSaved: () => void,
): Promise<string[]> {
  const failed: string[] = [];
  for (const [path, entry] of cache) {
    for (let i = 0; i < 100 && entry.saving; i++) await new Promise((r) => setTimeout(r, 100));
    if (entry.dirty && !entry.conflict) await saveEntry(pid, path, entry, (s) => report(path, s), onSaved);
    if (entry.dirty || entry.conflict) failed.push(path);
  }
  return failed;
}

export function RestEditor(
  props: CommonProps & {
    cache: DocCache;
    onSaveState: (path: string, s: SaveState) => void;
    onSaved: () => void;
  },
) {
  const { pid, path, readOnly, cache, jump, diagnostics, completion } = props;
  const host = useRef<HTMLDivElement>(null);
  const [view, setView] = useState<EditorView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<"ask" | "kept" | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const cb = useRef(props);
  cb.current = props;
  const selCb = useRef(props.onSelection);
  selCb.current = props.onSelection;

  const report = useCallback(
    (s: SaveState) => {
      cb.current.onSaveState(path, s);
      if (s.kind === "conflict") setConflict((c) => c ?? "ask");
      setFailed(s.kind === "error" ? s.message : null);
    },
    [path],
  );

  const save = useCallback(() => {
    const entry = cache.get(path);
    if (!entry) return;
    void saveEntry(pid, path, entry, report, () => cb.current.onSaved()).then(() => {
      // edits made while the request was in flight
      if (entry.dirty && !entry.conflict && !entry.saving) schedule();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cache, path, pid, report]);

  const schedule = useCallback(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(save, 1200);
  }, [save]);

  const makeExtensions = useCallback(
    () =>
      buildExtensions({
        path,
        readOnly,
        completion,
        onUpdate: [
          selectionListener(path, selCb),
          EditorView.updateListener.of((u) => {
            const entry = cache.get(path);
            if (!entry) return;
            entry.state = u.state;
            if (u.docChanged) {
              entry.dirty = true;
              if (!entry.conflict) {
                cb.current.onSaveState(path, { kind: "dirty" });
                schedule();
              }
            }
          }),
        ],
      }),
    [path, readOnly, completion, cache, schedule],
  );
  const makeState = useCallback((text: string) => EditorState.create({ doc: text, extensions: makeExtensions() }), [makeExtensions]);
  /** Reuse a cached state (keeps undo history) but bind it to this component's listeners. */
  const rebind = useCallback(
    (st: EditorState) => st.update({ effects: StateEffect.reconfigure.of(makeExtensions()) }).state,
    [makeExtensions],
  );

  // Load file, create the view.
  useEffect(() => {
    let cancelled = false;
    let v: EditorView | null = null;
    setLoadError(null);
    setView(null);
    (async () => {
      const cached = cache.get(path);
      let state: EditorState;
      if (cached && (cached.dirty || cached.conflict)) {
        state = rebind(cached.state); // keep unsaved work
        cached.state = state;
        if (cached.conflict) setConflict("kept");
      } else {
        let fc;
        try {
          fc = await api.fileContent(pid, path);
        } catch (e) {
          if (!cancelled) setLoadError(errorMessage(e));
          return;
        }
        if (cancelled) return;
        if (cached && cached.sha === fc.sha) {
          state = rebind(cached.state); // keeps undo history
          cached.state = state;
        } else {
          const entry: CacheEntry = { state: EditorState.create(), sha: fc.sha, dirty: false, conflict: false, saving: false };
          cache.set(path, entry);
          state = makeState(fc.text);
          entry.state = state;
        }
      }
      if (cancelled || !host.current) return;
      v = new EditorView({ state, parent: host.current });
      setView(v);
      const entry = cache.get(path)!;
      cb.current.onSaveState(path, entry.conflict ? { kind: "conflict" } : entry.dirty ? { kind: "dirty" } : { kind: "idle" });
      if (entry.dirty && !entry.conflict) schedule();
    })();
    return () => {
      cancelled = true;
      window.clearTimeout(timer.current);
      const entry = cache.get(path);
      if (v && entry) {
        entry.state = v.state;
        if (entry.dirty && !entry.conflict) void saveEntry(pid, path, entry, (s) => cb.current.onSaveState(path, s), () => cb.current.onSaved());
      }
      v?.destroy();
      cb.current.onSelection(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid, path, readOnly]);

  useViewEffects(view, path, jump, diagnostics);

  async function reloadDiscard() {
    setBusy(true);
    try {
      const fc = await api.fileContent(pid, path);
      const entry = cache.get(path)!;
      entry.sha = fc.sha;
      entry.conflict = false;
      entry.dirty = false;
      const st = makeState(fc.text);
      entry.state = st;
      view?.setState(st);
      setConflict(null);
      setFailed(null);
      cb.current.onSaveState(path, { kind: "idle" });
      cb.current.onSaved();
    } catch (e) {
      setFailed(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function overwrite() {
    setBusy(true);
    try {
      const fc = await api.fileContent(pid, path);
      const entry = cache.get(path)!;
      entry.sha = fc.sha; // explicit, user-confirmed overwrite of the newer server version
      entry.conflict = false;
      entry.dirty = true;
      setConflict(null);
      await saveEntry(pid, path, entry, report, () => cb.current.onSaved());
    } catch (e) {
      setFailed(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  if (loadError) return <ErrorBox error={`Could not open ${path}: ${loadError}`} />;
  return (
    <div className="editor-wrap">
      {conflict === "ask" && (
        <div className="editor-banner warn" role="alert">
          <strong>This file changed elsewhere.</strong>
          <span>
            Someone else (or an applied AI proposal / restore) saved a newer version. Your recent edits have <em>not</em> been saved.
          </span>
          <div className="row-actions">
            <button className="btn btn-sm" onClick={reloadDiscard} disabled={busy}>
              Reload (discard mine)
            </button>
            <button className="btn btn-sm" onClick={() => setConflict("kept")} disabled={busy}>
              Keep editing
            </button>
          </div>
        </div>
      )}
      {conflict === "kept" && (
        <div className="editor-banner warn compact" role="status">
          <span>Not saved — the server has a newer version. Autosave is paused for this file.</span>
          <div className="row-actions">
            <button className="btn btn-sm" onClick={reloadDiscard} disabled={busy}>
              Reload (discard mine)
            </button>
            <button className="btn btn-sm btn-danger" onClick={overwrite} disabled={busy}>
              Overwrite with mine
            </button>
          </div>
        </div>
      )}
      {failed && !conflict && (
        <div className="editor-banner bad compact" role="alert">
          <span>Save failed: {failed}</span>
          <button className="btn btn-sm" onClick={save}>
            Retry save
          </button>
        </div>
      )}
      {!view && <Loading text={`Opening ${path}…`} />}
      <div className="cm-host" ref={host} />
    </div>
  );
}

// --------------------------------------------------------------- collab ---

export function CollabEditor(props: CommonProps & { session: CollabSession }) {
  const { session, path, jump, diagnostics, completion } = props;
  const readOnly = props.readOnly || session.readonly;
  const host = useRef<HTMLDivElement>(null);
  const [view, setView] = useState<EditorView | null>(null);
  const [ytext, setYtext] = useState<Y.Text | null>(() => session.files.get(path) ?? null);
  const [synced, setSynced] = useState(session.synced);
  const selCb = useRef(props.onSelection);
  selCb.current = props.onSelection;

  useEffect(() => {
    const update = () => {
      setYtext(session.files.get(path) ?? null);
      setSynced(session.synced);
    };
    update();
    const a = session.onFilesChanged(update);
    const b = session.subscribe(update);
    session.setActiveFile(path);
    return () => {
      a();
      b();
    };
  }, [session, path]);

  useEffect(() => {
    if (!ytext || !host.current) return;
    const undoManager = new Y.UndoManager(ytext);
    const v = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: ytext.toString(),
        extensions: buildExtensions({
          path,
          readOnly,
          completion,
          collab: { extension: yCollab(ytext, session.provider.awareness, { undoManager }), undoKeymap: yUndoManagerKeymap },
          onUpdate: selectionListener(path, selCb),
        }),
      }),
    });
    setView(v);
    return () => {
      v.destroy();
      undoManager.destroy();
      setView(null);
      selCb.current(null);
    };
  }, [ytext, readOnly, path, session, completion]);

  useViewEffects(view, path, jump, diagnostics);

  return (
    <div className="editor-wrap">
      {!ytext &&
        (synced ? (
          <div className="state-msg">
            <span>
              <code>{path}</code> is not part of the shared document. It may have been deleted or renamed by a collaborator.
            </span>
          </div>
        ) : (
          <Loading text="Connecting to the collaboration server…" />
        ))}
      <div className="cm-host" ref={host} />
    </div>
  );
}
