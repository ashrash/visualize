import { lazy, Suspense } from "react";
import { Input } from "../../components/ui/input";
import type { FormulaEditorProps } from "./FormulaEditorImpl";

// CodeMirror is ~300 kB; it loads only when a formula field first renders.
const Impl = lazy(() => import("./FormulaEditorImpl"));

export function FormulaEditor(props: FormulaEditorProps) {
  return (
    <Suspense
      fallback={
        <Input
          id={props.id}
          aria-label={props.label}
          aria-describedby={props.describedBy}
          value={props.value}
          onChange={(event) => props.onChange(event.target.value)}
          placeholder={props.placeholder}
          className="font-mono"
        />
      }
    >
      <Impl {...props} />
    </Suspense>
  );
}
