import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render as rtlRender, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
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

const render = (ui: ReactElement) =>
  rtlRender(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      {ui}
    </QueryClientProvider>,
  );

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
});

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
    expect(screen.getByLabelText("Move step 1 up").getAttribute("aria-disabled")).toBe("true");
    expect(screen.getByLabelText("Move step 2 down").getAttribute("aria-disabled")).toBe("true");
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

  it("shows server status, errors and formula ranges per step from the inspect endpoint", async () => {
    const pipeline = pipelineWith("sort", "dedupe");
    const [a, b] = pipeline.steps ?? [];
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        (typeof input === "string" ? input : "url" in input ? input.url : input.href).includes(
          "/inspect",
        )
          ? json({
              ok: false,
              sourceColumns: [],
              columns: [],
              sql: null,
              steps: [
                {
                  stepId: a?.id,
                  status: "error",
                  columns: [],
                  materialized: "none",
                  error: { message: "boom", field: "expression", start: 4, end: 9 },
                },
                {
                  stepId: b?.id,
                  status: "blocked",
                  columns: [],
                  materialized: "none",
                  error: null,
                },
              ],
            })
          : json({ detail: "not mocked" }, 500),
      ),
    );
    render(
      <PipelineEditor
        pipeline={pipeline}
        sourceColumns={columns}
        datasets={[]}
        dispatch={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByText("boom (characters 5-9)")).toBeTruthy());
    expect(screen.getByText("Blocked by an earlier step")).toBeTruthy();
  });
});
