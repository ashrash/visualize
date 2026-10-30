import { CompletionContext } from "@codemirror/autocomplete";
import { EditorState } from "@codemirror/state";
import { describe, expect, it } from "vitest";
import type { Column } from "../../api/types";
import { columnCompletionSource, quoteColumn } from "./formulaCompletion";

const col = (name: string): Column => ({
  name,
  type: "DOUBLE",
  semantic: "quantitative",
  nullCount: 0,
  approxDistinct: 1,
});
const columns = [col("units"), col("unit price")];

function complete(doc: string, explicit = false) {
  const state = EditorState.create({ doc });
  const context = new CompletionContext(state, doc.length, explicit);
  return columnCompletionSource(columns)(context);
}

describe("formula column completion", () => {
  it("quotes only names that need it", () => {
    expect(quoteColumn("units")).toBe("units");
    expect(quoteColumn("unit price")).toBe('"unit price"');
    expect(quoteColumn('a"b')).toBe('"a""b"');
  });

  it("offers all columns while typing a word, with quoted apply text", () => {
    const result = complete("units * un");
    expect(result?.from).toBe(8);
    expect(result?.options.map((o) => o.apply)).toEqual(["units", '"unit price"']);
  });

  it("completes inside an open quote", () => {
    const result = complete('"unit');
    expect(result?.from).toBe(0);
    expect(result?.options.map((o) => o.label)).toEqual(['"units', '"unit price']);
  });

  it("stays quiet with nothing typed unless explicitly requested", () => {
    expect(complete("units * ")).toBeNull();
    expect(complete("units * ", true)?.options).toHaveLength(2);
  });
});
