import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Column, Pipeline } from "../../api/types";
import { PipelineEditor } from "./PipelineEditor";
import { createPipeline, createStep } from "./stepReducer";

const columns: Column[] = [
  { name: "region", type: "VARCHAR", semantic: "nominal", nullCount: 0, approxDistinct: 4 },
  { name: "sales", type: "DOUBLE", semantic: "quantitative", nullCount: 0, approxDistinct: 100 },
];

function pipelineWith(...ops: Parameters<typeof createStep>[0][]): Pipeline {
  return {
    ...createPipeline({ id: "d", name: "D" }),
    steps: ops.map((op) => createStep(op, columns)),
  };
}

afterEach(cleanup);

describe("PipelineEditor", () => {
  it("lists steps and dispatches toggle, move and delete", () => {
    const dispatch = vi.fn();
    const pipeline = pipelineWith("sort", "dedupe");
    const [first, second] = pipeline.steps ?? [];
    render(
      <PipelineEditor
        pipeline={pipeline}
        sourceColumns={columns}
        datasets={[]}
        dispatch={dispatch}
      />,
    );

    fireEvent.click(screen.getByLabelText(/Enable step 1/));
    expect(dispatch).toHaveBeenLastCalledWith({ type: "toggle", id: first?.id });

    fireEvent.click(screen.getByLabelText("Move step 2 up"));
    expect(dispatch).toHaveBeenLastCalledWith({ type: "move", id: second?.id, to: 0 });

    fireEvent.click(screen.getByLabelText("Delete step 1"));
    expect(dispatch).toHaveBeenLastCalledWith({ type: "remove", id: first?.id });
  });

  it("disables moving the first step up and the last step down", () => {
    render(
      <PipelineEditor
        pipeline={pipelineWith("sort", "dedupe")}
        sourceColumns={columns}
        datasets={[]}
        dispatch={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("Move step 1 up")).toHaveProperty("disabled", true);
    expect(screen.getByLabelText("Move step 2 down")).toHaveProperty("disabled", true);
  });

  it("edits the selected step through its form", () => {
    const dispatch = vi.fn();
    render(
      <PipelineEditor
        pipeline={pipelineWith("dedupe")}
        sourceColumns={columns}
        datasets={[]}
        dispatch={dispatch}
      />,
    );
    fireEvent.change(screen.getByLabelText("Keep"), { target: { value: "last" } });
    expect(dispatch).toHaveBeenCalledWith({
      type: "replace",
      step: expect.objectContaining({ op: "dedupe", keep: "last" }),
    });
  });

  it("shows the preview placeholder", () => {
    render(
      <PipelineEditor
        pipeline={pipelineWith("sort")}
        sourceColumns={columns}
        datasets={[]}
        dispatch={vi.fn()}
      />,
    );
    expect(screen.getByRole("note").textContent).toMatch(/Preview/);
  });
});
