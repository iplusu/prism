import { completionStatus } from "@codemirror/autocomplete";
import { Prec, StateEffect, StateField, type Extension } from "@codemirror/state";
import { Decoration, EditorView, keymap, ViewPlugin, WidgetType, type DecorationSet } from "@codemirror/view";
import { api } from "../api";

/** Inline AI completion settings, owned by the workspace (off by default; see the toggle in the editor tabs). */
export interface AiCompleteState {
  pid: string;
  on: boolean;
  /** Called with the failure message, or null after a success. */
  report: (error: string | null) => void;
}

const DEBOUNCE_MS = 700;
const MAX_PREFIX = 3000;
const MAX_SUFFIX = 1000;

const setGhost = StateEffect.define<{ pos: number; text: string } | null>();

class GhostWidget extends WidgetType {
  constructor(readonly text: string) {
    super();
  }
  eq(o: GhostWidget) {
    return o.text === this.text;
  }
  toDOM() {
    const s = document.createElement("span");
    s.className = "cm-ghost";
    s.textContent = this.text;
    s.setAttribute("aria-hidden", "true");
    return s;
  }
}

const ghostField = StateField.define<{ pos: number; text: string } | null>({
  create: () => null,
  update(v, tr) {
    for (const e of tr.effects) if (e.is(setGhost)) return e.value;
    // any edit or cursor move discards the suggestion
    return tr.docChanged || tr.selection ? null : v;
  },
  provide: (f) =>
    EditorView.decorations.from(f, (g): DecorationSet =>
      g ? Decoration.set([Decoration.widget({ widget: new GhostWidget(g.text), side: 1 }).range(g.pos)]) : Decoration.none,
    ),
});

/**
 * Ghost-text completion: after a pause in typing at the end of a line, asks the
 * server for a short continuation and shows it greyed out. Tab accepts, Esc or
 * any other key discards. Only the user's own typing triggers a request, never
 * remote collaborator edits.
 */
export function aiGhostText(ai: { current: AiCompleteState }, path: string): Extension {
  const plugin = ViewPlugin.fromClass(
    class {
      timer: number | undefined;
      abort: AbortController | null = null;
      constructor(readonly view: EditorView) {}
      update(u: import("@codemirror/view").ViewUpdate) {
        if (!u.docChanged && !u.selectionSet) return;
        this.cancel();
        const typed = u.transactions.some((t) => t.isUserEvent("input.type") || t.isUserEvent("input.paste"));
        if (!typed || !ai.current.on) return;
        this.timer = window.setTimeout(() => void this.request(), DEBOUNCE_MS);
      }
      cancel() {
        window.clearTimeout(this.timer);
        this.abort?.abort();
        this.abort = null;
      }
      async request() {
        const v = this.view;
        const st = v.state;
        const sel = st.selection.main;
        if (!ai.current.on || !sel.empty || !v.hasFocus || completionStatus(st)) return;
        const line = st.doc.lineAt(sel.head);
        if (st.sliceDoc(sel.head, line.to).trim() !== "") return; // only at the end of a line
        if (st.sliceDoc(Math.max(line.from, sel.head - 3), sel.head).trim().length < 2) return;
        const doc = st.doc;
        const pos = sel.head;
        const ctl = (this.abort = new AbortController());
        try {
          const r = await api.complete(
            ai.current.pid,
            path,
            st.sliceDoc(Math.max(0, pos - MAX_PREFIX), pos),
            st.sliceDoc(pos, Math.min(st.doc.length, pos + MAX_SUFFIX)),
            ctl.signal,
          );
          ai.current.report(null);
          if (ctl.signal.aborted || v.state.doc !== doc || v.state.selection.main.head !== pos || !r.completion.trim()) return;
          v.dispatch({ effects: setGhost.of({ pos, text: r.completion }) });
        } catch (e) {
          if (e instanceof DOMException && e.name === "AbortError") return;
          ai.current.report(e instanceof Error ? e.message : "completion failed");
        }
      }
      destroy() {
        this.cancel();
      }
    },
  );
  const accept = (view: EditorView): boolean => {
    const g = view.state.field(ghostField, false);
    if (!g) return false;
    view.dispatch({
      changes: { from: g.pos, insert: g.text },
      selection: { anchor: g.pos + g.text.length },
      userEvent: "input.complete",
    });
    return true;
  };
  const dismiss = (view: EditorView): boolean => {
    if (!view.state.field(ghostField, false)) return false;
    view.dispatch({ effects: setGhost.of(null) });
    return true;
  };
  return [ghostField, plugin, Prec.highest(keymap.of([{ key: "Tab", run: accept }, { key: "Escape", run: dismiss }]))];
}
