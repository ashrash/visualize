import type { Completion, CompletionContext, CompletionResult } from "@codemirror/autocomplete";
import type { Column } from "../../api/types";

/** DuckDB identifiers that need no quoting. */
const PLAIN_IDENTIFIER = /^[A-Za-z_][A-Za-z0-9_]*$/;

/** How a column is written in a formula: bare when safe, otherwise "double quoted". */
export function quoteColumn(name: string): string {
  return PLAIN_IDENTIFIER.test(name) ? name : `"${name.replaceAll('"', '""')}"`;
}

export function columnCompletions(columns: Column[]): Completion[] {
  return columns.map((column) => ({
    label: column.name,
    apply: quoteColumn(column.name),
    type: "variable",
    detail: column.type.toLowerCase(),
    boost: 1,
  }));
}

/**
 * Completion source offering the dataset's columns. Matches a word or the start of a quoted
 * identifier, so `"Unit pr` also completes.
 */
export function columnCompletionSource(columns: Column[]) {
  const options = columnCompletions(columns);
  return (context: CompletionContext): CompletionResult | null => {
    const quoted = context.matchBefore(/"[^"]*/);
    const word = quoted ?? context.matchBefore(/[A-Za-z_][A-Za-z0-9_]*/);
    if (!word && !context.explicit) return null;
    const from = word?.from ?? context.pos;
    return {
      from,
      options: quoted ? options.map((o) => ({ ...o, label: `"${o.label}` })) : options,
      validFor: quoted ? /^"[^"]*$/ : /^[A-Za-z0-9_]*$/,
    };
  };
}
