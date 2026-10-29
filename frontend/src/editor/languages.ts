import { HighlightStyle, StreamLanguage, type StreamParser } from "@codemirror/language";
import { stex } from "@codemirror/legacy-modes/mode/stex";
import type { Extension } from "@codemirror/state";
import { tags as t } from "@lezer/highlight";

export const latexLanguage = StreamLanguage.define(stex);

interface BibState {
  depth: number; // brace depth inside an entry
  inEntry: boolean;
  quote: boolean;
}

/** Minimal BibTeX tokenizer: @type{key, field = {value} | "value" | 123, ...}. */
const bibParser: StreamParser<BibState> = {
  name: "bibtex",
  startState: () => ({ depth: 0, inEntry: false, quote: false }),
  token(stream, state) {
    if (state.quote) {
      while (!stream.eol()) {
        const ch = stream.next();
        if (ch === "\\") stream.next();
        else if (ch === '"') {
          state.quote = false;
          break;
        }
      }
      return "string";
    }
    if (stream.eatSpace()) return null;
    if (state.depth === 0) {
      if (stream.match(/^%.*/)) return "comment";
      if (stream.match(/^@[A-Za-z]+/)) {
        state.inEntry = true;
        return "keyword";
      }
      if (stream.peek() === "{" && state.inEntry) {
        stream.next();
        state.depth = 1;
        // entry key follows directly
        return "bracket";
      }
      stream.next();
      return "comment"; // text outside entries is ignored by BibTeX
    }
    // inside an entry
    if (state.depth === 1) {
      if (stream.match(/^[^\s,={}"#]+(?=\s*,)/)) return "labelName";
      if (stream.match(/^[A-Za-z][\w-]*(?=\s*=)/)) return "propertyName";
      if (stream.match(/^\d+/)) return "number";
      if (stream.eat("=") || stream.eat("#")) return "operator";
      if (stream.eat(",")) return "punctuation";
      if (stream.eat('"')) {
        state.quote = true;
        return "string";
      }
      if (stream.eat("{")) {
        state.depth++;
        return "bracket";
      }
      if (stream.eat("}")) {
        state.depth = 0;
        state.inEntry = false;
        return "bracket";
      }
      if (stream.match(/^[A-Za-z][\w-]*/)) return "variableName"; // @string macro
      stream.next();
      return null;
    }
    // depth >= 2: braced value
    const ch = stream.next();
    if (ch === "{") state.depth++;
    else if (ch === "}") state.depth--;
    else if (ch === "\\") stream.match(/^[A-Za-z]+|^./);
    return state.depth >= 2 || ch === "}" ? "string" : "bracket";
  },
  languageData: { commentTokens: { line: "%" } },
};

export const bibtexLanguage = StreamLanguage.define(bibParser);

export function languageFor(path: string): Extension {
  const p = path.toLowerCase();
  if (p.endsWith(".bib")) return bibtexLanguage;
  if (/\.(tex|sty|cls|ltx|bbl|def|cfg|tikz)$/.test(p)) return latexLanguage;
  return [];
}

// Colors come from CSS variables so light/dark themes switch without
// rebuilding editor state.
export const prismHighlight = HighlightStyle.define([
  { tag: [t.tagName, t.keyword], color: "var(--syn-command)" },
  { tag: [t.atom, t.labelName], color: "var(--syn-atom)" },
  { tag: [t.special(t.variableName), t.variableName], color: "var(--syn-math)" },
  { tag: [t.comment, t.lineComment], color: "var(--syn-comment)", fontStyle: "italic" },
  { tag: [t.number], color: "var(--syn-number)" },
  { tag: [t.string], color: "var(--syn-string)" },
  { tag: [t.propertyName], color: "var(--syn-property)" },
  { tag: [t.bracket, t.punctuation, t.operator], color: "var(--syn-bracket)" },
  { tag: [t.invalid], color: "var(--danger)", textDecoration: "underline wavy" },
  { tag: [t.heading], fontWeight: "600" },
]);
