import { EditorView } from "@codemirror/view";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import FormulaEditorImpl from "./FormulaEditorImpl";

afterEach(cleanup);

const view = (container: HTMLElement): EditorView => {
  const dom = container.querySelector<HTMLElement>(".cm-editor");
  const found = dom ? EditorView.findFromDOM(dom) : null;
  if (!found) throw new Error("editor not mounted");
  return found;
};

const base = { columns: [], label: "Formula" };

describe("FormulaEditorImpl", () => {
  it("does not report host-driven text changes as edits", () => {
    const onChange = vi.fn();
    const { container, rerender } = render(
      <FormulaEditorImpl {...base} value="a + b" onChange={onChange} />,
    );
    rerender(<FormulaEditorImpl {...base} value="a * b" onChange={onChange} />);
    expect(view(container).state.doc.toString()).toBe("a * b");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("reports user edits", () => {
    const onChange = vi.fn();
    const { container } = render(<FormulaEditorImpl {...base} value="a" onChange={onChange} />);
    view(container).dispatch({ changes: { from: 1, insert: "b" } });
    expect(onChange).toHaveBeenCalledWith("ab");
  });

  it("keeps the current text when only labels change", () => {
    const onChange = vi.fn();
    const { container, rerender } = render(
      <FormulaEditorImpl {...base} value="a" onChange={onChange} />,
    );
    rerender(<FormulaEditorImpl {...base} value="a" label="Other" onChange={onChange} />);
    expect(view(container).state.doc.toString()).toBe("a");
    expect(container.querySelector(".cm-content")?.getAttribute("aria-label")).toBe("Other");
  });

  it("describes and labels the editable area", () => {
    const { container } = render(
      <FormulaEditorImpl {...base} value="" describedBy="hint-1" onChange={vi.fn()} />,
    );
    const content = container.querySelector(".cm-content");
    expect(content?.getAttribute("aria-label")).toBe("Formula");
    expect(content?.getAttribute("aria-describedby")).toBe("hint-1");
  });
});
