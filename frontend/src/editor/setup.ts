import {
  autocompletion,
  closeBrackets,
  closeBracketsKeymap,
  completionKeymap,
  type CompletionContext,
  type CompletionResult,
} from "@codemirror/autocomplete";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { bracketMatching, indentOnInput, syntaxHighlighting } from "@codemirror/language";
import { lintGutter, lintKeymap, setDiagnostics, type Diagnostic as CmDiagnostic } from "@codemirror/lint";
import { highlightSelectionMatches, search, searchKeymap } from "@codemirror/search";
import { EditorState, type Extension, type Text } from "@codemirror/state";
import {
  crosshairCursor,
  drawSelection,
  dropCursor,
  EditorView,
  highlightActiveLine,
  highlightActiveLineGutter,
  highlightSpecialChars,
  keymap,
  lineNumbers,
  rectangularSelection,
  type KeyBinding,
} from "@codemirror/view";
import type { Diagnostic } from "../types";
import { languageFor, prismHighlight } from "./languages";

/** Labels and bibliography keys used for \ref / \cite completion. Mutable, owned by the workspace. */
export interface CompletionData {
  labels: string[];
  bibKeys: { key: string; title: string }[];
}

function latexCompletions(data: { current: CompletionData }) {
  return (ctx: CompletionContext): CompletionResult | null => {
    const m = ctx.matchBefore(/\\(?:[a-zA-Z]*ref|eqref|cite[a-zA-Z]*)\*?\{[^}]*$/);
    if (!m) return null;
    const brace = m.text.indexOf("{");
    const cmd = m.text.slice(1, brace);
    // complete the current comma-separated key
    const inner = m.text.slice(brace + 1);
    const lastComma = inner.lastIndexOf(",");
    const from = m.from + brace + 1 + (lastComma >= 0 ? lastComma + 1 : 0);
    const options = cmd.startsWith("cite")
      ? data.current.bibKeys.map((b) => ({ label: b.key, detail: b.title.slice(0, 60), type: "constant" }))
      : data.current.labels.map((l) => ({ label: l, type: "variable" }));
    return { from: from + (ctx.state.sliceDoc(from, ctx.pos).match(/^\s*/)?.[0].length ?? 0), options, validFor: /^[^,}\s]*$/ };
  };
}

const editorTheme = EditorView.theme({
  "&": { height: "100%", fontSize: "var(--editor-font-size)", backgroundColor: "var(--bg)", color: "var(--fg)" },
  ".cm-scroller": { fontFamily: "var(--mono)", lineHeight: "1.55" },
  ".cm-content": { caretColor: "var(--accent)", padding: "8px 0" },
  ".cm-gutters": { backgroundColor: "var(--bg-subtle)", color: "var(--fg-muted)", borderRight: "1px solid var(--border)" },
  ".cm-activeLine": { backgroundColor: "var(--active-line)" },
  ".cm-activeLineGutter": { backgroundColor: "var(--active-line)", color: "var(--fg)" },
  "&.cm-focused .cm-cursor": { borderLeftColor: "var(--accent)" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": { backgroundColor: "var(--selection) !important" },
  ".cm-searchMatch": { backgroundColor: "var(--search-match)", outline: "1px solid var(--warn)" },
  ".cm-searchMatch.cm-searchMatch-selected": { backgroundColor: "var(--search-match-sel)" },
  ".cm-selectionMatch": { backgroundColor: "var(--selection-match)" },
  ".cm-panels": { backgroundColor: "var(--bg-subtle)", color: "var(--fg)" },
  ".cm-panels.cm-panels-top": { borderBottom: "1px solid var(--border)" },
  ".cm-panel.cm-search": { padding: "6px 8px", fontFamily: "var(--sans)", fontSize: "13px" },
  ".cm-panel.cm-search input, .cm-panel.cm-search button": { fontFamily: "var(--sans)", fontSize: "13px" },
  ".cm-textfield": { border: "1px solid var(--border-strong)", borderRadius: "4px", background: "var(--bg)", color: "var(--fg)", padding: "2px 6px" },
  ".cm-button": { backgroundImage: "none", background: "var(--bg)", border: "1px solid var(--border-strong)", borderRadius: "4px", color: "var(--fg)" },
  ".cm-tooltip": { backgroundColor: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--fg)" },
  ".cm-tooltip-autocomplete > ul > li[aria-selected]": { backgroundColor: "var(--accent)", color: "var(--accent-fg)" },
  ".cm-foldPlaceholder": { backgroundColor: "var(--bg-subtle)", border: "1px solid var(--border)" },
  ".cm-ySelectionInfo": { fontFamily: "var(--sans)", opacity: "1", padding: "1px 4px", borderRadius: "3px" },
});

export interface ExtensionOptions {
  path: string;
  readOnly: boolean;
  /** When collaborating, undo comes from Yjs instead of CodeMirror history. */
  collab?: { extension: Extension; undoKeymap: readonly KeyBinding[] };
  completion: { current: CompletionData };
  onUpdate?: Extension;
}

export function buildExtensions(o: ExtensionOptions): Extension[] {
  return [
    lineNumbers(),
    highlightActiveLineGutter(),
    highlightSpecialChars(),
    o.collab ? [] : history(),
    drawSelection(),
    dropCursor(),
    EditorState.allowMultipleSelections.of(true),
    indentOnInput(),
    syntaxHighlighting(prismHighlight),
    bracketMatching(),
    closeBrackets(),
    autocompletion({ override: [latexCompletions(o.completion)], activateOnTyping: true }),
    rectangularSelection(),
    crosshairCursor(),
    highlightActiveLine(),
    highlightSelectionMatches(),
    search({ top: true }),
    lintGutter(),
    EditorView.lineWrapping,
    EditorState.readOnly.of(o.readOnly),
    EditorView.editable.of(!o.readOnly),
    EditorView.contentAttributes.of({ "aria-label": `Editor: ${o.path}${o.readOnly ? " (read-only)" : ""}` }),
    keymap.of([
      ...closeBracketsKeymap,
      ...defaultKeymap,
      ...searchKeymap,
      ...(o.collab ? o.collab.undoKeymap : historyKeymap),
      ...completionKeymap,
      ...lintKeymap,
      indentWithTab,
    ]),
    languageFor(o.path),
    editorTheme,
    o.collab ? o.collab.extension : [],
    o.onUpdate ?? [],
  ];
}

/** Convert build diagnostics for one file into CodeMirror lint diagnostics. */
export function toCmDiagnostics(doc: Text, diags: Diagnostic[]): CmDiagnostic[] {
  const out: CmDiagnostic[] = [];
  for (const d of diags) {
    const ln = d.line && d.line >= 1 && d.line <= doc.lines ? doc.line(d.line) : doc.line(1);
    out.push({
      from: ln.from,
      to: Math.max(ln.from, ln.to),
      severity: d.severity === "error" ? "error" : d.severity === "warning" ? "warning" : "info",
      message: d.line ? d.message : `${d.message} (no line reported)`,
      source: "LaTeX build",
    });
  }
  return out;
}

export { setDiagnostics };
