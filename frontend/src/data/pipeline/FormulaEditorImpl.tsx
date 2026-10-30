import { autocompletion, closeBrackets, completionKeymap } from "@codemirror/autocomplete";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { sql } from "@codemirror/lang-sql";
import { bracketMatching } from "@codemirror/language";
import { Annotation, Compartment, EditorState, StateEffect, StateField } from "@codemirror/state";
import {
  Decoration,
  type DecorationSet,
  EditorView,
  keymap,
  placeholder as placeholderExt,
} from "@codemirror/view";
import { useEffect, useRef } from "react";
import type { Column, FormulaFunction } from "../../api/types";
import { columnCompletionSource } from "./formulaCompletion";

export interface FormulaEditorProps {
  value: string;
  columns: Column[];
  /** Server-provided function list (transform), offered next to the column names. */
  functions?: FormulaFunction[];
  onChange: (value: string) => void;
  id?: string;
  label: string;
  /** id of the element describing the field (hint text), for aria-describedby. */
  describedBy?: string;
  placeholder?: string;
  /** Character range of a formula error to underline. */
  error?: { start: number; end: number; message: string } | null;
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
  ".cm-content": {
    fontFamily: "var(--viz-font-mono)",
    caretColor: "var(--viz-text)",
    padding: "6px 0",
  },
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
  ".cm-formula-error": {
    textDecoration: "underline wavy var(--viz-danger)",
    textUnderlineOffset: "3px",
  },
});

/** Marks a transaction as "the host changed the text"; those must not be reported as edits. */
export const External = Annotation.define<boolean>();

const setError = StateEffect.define<{ from: number; to: number; message: string } | null>();
const errorField = StateField.define<DecorationSet>({
  create: () => Decoration.none,
  update(decorations, tr) {
    for (const effect of tr.effects) {
      if (effect.is(setError)) {
        const e = effect.value;
        if (!e || e.from >= e.to) return Decoration.none;
        return Decoration.set([
          Decoration.mark({
            class: "cm-formula-error",
            attributes: { title: e.message },
          }).range(e.from, e.to),
        ]);
      }
    }
    // A user edit makes the old error position meaningless until the next check.
    return tr.docChanged && !tr.annotation(External) ? Decoration.none : decorations;
  },
  provide: (field) => EditorView.decorations.from(field),
});

const attributes = (label: string, id?: string, describedBy?: string) =>
  EditorView.contentAttributes.of({
    "aria-label": label,
    ...(id ? { id } : {}),
    ...(describedBy ? { "aria-describedby": describedBy } : {}),
  });

export default function FormulaEditorImpl({
  value,
  columns,
  functions,
  onChange,
  id,
  label,
  describedBy,
  placeholder,
  error,
}: FormulaEditorProps) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const completion = useRef(new Compartment());
  const attrs = useRef(new Compartment());
  const hint = useRef(new Compartment());
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  // The text at mount only; later prop changes reach the editor through the sync effect.
  const initial = useRef({ value, columns, functions, label, id, describedBy, placeholder });

  // Create the editor once. Everything that can change later lives in a compartment.
  useEffect(() => {
    if (!host.current) return;
    const init = initial.current;
    const editor = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: init.value,
        extensions: [
          history(),
          closeBrackets(),
          bracketMatching(),
          sql(),
          theme,
          errorField,
          hint.current.of(placeholderExt(init.placeholder ?? "")),
          completion.current.of(
            autocompletion({ override: [columnCompletionSource(init.columns, init.functions)] }),
          ),
          keymap.of([...completionKeymap, ...defaultKeymap, ...historyKeymap]),
          attrs.current.of(attributes(init.label, init.id, init.describedBy)),
          EditorView.updateListener.of((update) => {
            // Text the host pushed in (undo of the project, reload) is not a user edit.
            const external = update.transactions.some((tr) => tr.annotation(External));
            if (update.docChanged && !external) onChangeRef.current(update.state.doc.toString());
          }),
        ],
      }),
    });
    view.current = editor;
    return () => {
      editor.destroy();
      view.current = null;
    };
  }, []);

  useEffect(() => {
    view.current?.dispatch({
      effects: [
        completion.current.reconfigure(
          autocompletion({ override: [columnCompletionSource(columns, functions)] }),
        ),
        attrs.current.reconfigure(attributes(label, id, describedBy)),
        hint.current.reconfigure(placeholderExt(placeholder ?? "")),
      ],
    });
  }, [columns, functions, label, id, describedBy, placeholder]);

  useEffect(() => {
    const editor = view.current;
    if (editor && editor.state.doc.toString() !== value) {
      editor.dispatch({
        changes: { from: 0, to: editor.state.doc.length, insert: value },
        annotations: External.of(true),
      });
    }
  }, [value]);

  useEffect(() => {
    view.current?.dispatch({
      effects: setError.of(
        error ? { from: error.start, to: error.end, message: error.message } : null,
      ),
    });
  }, [error]);

  return <div ref={host} />;
}
