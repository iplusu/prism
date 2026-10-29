import { EditorState } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { useEffect, useRef } from "react";
import { buildExtensions, type CompletionData } from "../editor/setup";

const NO_COMPLETION: { current: CompletionData } = { current: { labels: [], bibKeys: [] } };

/** Controlled-ish CodeMirror editor for a standalone text value (conflict resolution). */
export default function PlainEditor({
  path,
  value,
  onChange,
  readOnly = false,
  label,
}: {
  path: string;
  value: string;
  onChange?: (v: string) => void;
  readOnly?: boolean;
  label: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const cb = useRef(onChange);
  cb.current = onChange;

  useEffect(() => {
    if (!host.current) return;
    const v = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: value,
        extensions: [
          buildExtensions({
            path,
            readOnly,
            completion: NO_COMPLETION,
            onUpdate: EditorView.updateListener.of((u) => u.docChanged && cb.current?.(u.state.doc.toString())),
          }),
          EditorView.contentAttributes.of({ "aria-label": label }),
        ],
      }),
    });
    view.current = v;
    return () => {
      v.destroy();
      view.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, readOnly]);

  // External value replacement (e.g. "start from current").
  useEffect(() => {
    const v = view.current;
    if (v && v.state.doc.toString() !== value) v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } });
  }, [value]);

  return <div className="plain-editor" ref={host} />;
}
