import { autocompletion, closeBrackets, completionKeymap } from "@codemirror/autocomplete";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { sql } from "@codemirror/lang-sql";
import { bracketMatching } from "@codemirror/language";
import { Compartment, EditorState } from "@codemirror/state";
import { EditorView, keymap, placeholder as placeholderExt } from "@codemirror/view";
import { useEffect, useRef } from "react";
import type { Column } from "../../api/types";
import { columnCompletionSource } from "./formulaCompletion";

export interface FormulaEditorProps {
  value: string;
  columns: Column[];
  onChange: (value: string) => void;
  id?: string;
  label: string;
  placeholder?: string;
}

// Colors come from the design tokens so light and dark both work.
const theme = EditorView.theme({
  "&": {
    backgroundColor: "var(--viz-bg)",
    color: "var(--viz-text)",
    border: "1px solid var(--viz-border)",
    borderRadius: "6px",
    fontSize: "13px",
  },
  "&.cm-focused": { outline: "2px solid var(--viz-accent)", outlineOffset: "1px" },
  ".cm-content": { fontFamily: "var(--viz-font-mono)", caretColor: "var(--viz-text)", padding: "6px 0" },
  ".cm-cursor": { borderLeftColor: "var(--viz-text)" },
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground": {
    backgroundColor: "var(--viz-surface-2)",
  },
  ".cm-tooltip": {
    backgroundColor: "var(--viz-surface)",
    color: "var(--viz-text)",
    border: "1px solid var(--viz-border)",
  },
  ".cm-tooltip-autocomplete ul li[aria-selected]": {
    backgroundColor: "var(--viz-accent)",
    color: "var(--viz-accent-text)",
  },
  ".cm-completionDetail": { color: "var(--viz-text-muted)", marginLeft: "0.75em" },
  ".cm-placeholder": { color: "var(--viz-text-muted)" },
});

export default function FormulaEditorImpl({
  value,
  columns,
  onChange,
  id,
  label,
  placeholder,
}: FormulaEditorProps) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const completion = useRef(new Compartment());
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  // Latest value at mount time only; later prop changes are synced by the effect below.
  const initial = useRef(value);

  useEffect(() => {
    if (!host.current) return;
    const editor = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: initial.current,
        extensions: [
          history(),
          closeBrackets(),
          bracketMatching(),
          sql(),
          theme,
          placeholderExt(placeholder ?? ""),
          completion.current.of(autocompletion({ override: [columnCompletionSource([])] })),
          keymap.of([...completionKeymap, ...defaultKeymap, ...historyKeymap]),
          // Tab moves focus on (keyboard users must be able to leave the editor).
          EditorView.contentAttributes.of({ "aria-label": label, ...(id ? { id } : {}) }),
          EditorView.updateListener.of((update) => {
            if (update.docChanged) onChangeRef.current(update.state.doc.toString());
          }),
        ],
      }),
    });
    view.current = editor;
    return () => {
      editor.destroy();
      view.current = null;
    };
  }, [id, label, placeholder]);

  useEffect(() => {
    view.current?.dispatch({
      effects: completion.current.reconfigure(
        autocompletion({ override: [columnCompletionSource(columns)] }),
      ),
    });
  }, [columns]);

  useEffect(() => {
    const editor = view.current;
    if (editor && editor.state.doc.toString() !== value) {
      editor.dispatch({ changes: { from: 0, to: editor.state.doc.length, insert: value } });
    }
  }, [value]);

  return <div ref={host} />;
}
