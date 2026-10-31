// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import { makeData, makeSpec } from "./testing";

// A histogram-like plot: `bins` changes the server data, `color` does not.
vi.mock("./registry", () => ({
  getPlot: (key: string) =>
    key === "histogram"
      ? { paramsSchema: [{ name: "bins", affectsData: true }, { name: "color" }] }
      : key === "bar"
        ? { paramsSchema: [{ name: "barWidth" }] }
        : undefined,
}));

const postArrow = vi.fn();
const postJson = vi.fn();
vi.mock("../../api/httpArrow", () => ({
  postArrowOrJson: (...a: unknown[]) => postArrow(...a),
}));
// The Arrow table is faked as the ChartData itself; toColumns just reads it back.
vi.mock("../../api/arrow", () => ({
  toColumns: (t: { columns: unknown; kinds: unknown; rowCount: number }) => ({
    columns: t.columns,
    kinds: t.kinds,
    rowCount: t.rowCount,
  }),
}));
const arrow = (data: ReturnType<typeof makeData>) => ({
  kind: "arrow",
  table: data,
  meta: data.meta,
});
vi.mock("../../api/http", () => ({ postJson: (...a: unknown[]) => postJson(...a) }));

import { buildRequest, dataIdentity, fetchOverlays, useChartData, usedPipelines } from "./data";

beforeEach(() => {
  postArrow.mockReset();
  postJson.mockReset();
});

const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>
);

describe("dataIdentity (S2)", () => {
  it("ignores cosmetic params, so dragging bar width does not re-query", () => {
    const a = dataIdentity(makeSpec({ params: { barWidth: 40 } }));
    const b = dataIdentity(makeSpec({ params: { barWidth: 90 } }));
    expect(a).toEqual(b);
  });

  it("keys on params the plot marks affectsData, and on error bars", () => {
    const base = { plotType: "histogram" } as const;
    const bins10 = dataIdentity(makeSpec({ ...base, params: { bins: 10, color: "#111" } }));
    const bins20 = dataIdentity(makeSpec({ ...base, params: { bins: 20, color: "#111" } }));
    const recolor = dataIdentity(makeSpec({ ...base, params: { bins: 10, color: "#999" } }));
    expect(bins10).not.toEqual(bins20);
    expect(bins10).toEqual(recolor);
    expect(bins10.params).toEqual({ bins: 10 });
    const withBars = dataIdentity(
      makeSpec({
        style: {
          orientation: "vertical",
          stack: "none",
          showLabels: false,
          titlePosition: "top",
          errorBars: { kind: "sd", level: 0.95 },
        },
      }),
    );
    expect(withBars.errorBars).toEqual({ kind: "sd", level: 0.95 });
    expect(dataIdentity(makeSpec()).errorBars).toBeNull();
  });

  it("keeps every param for a plot with no registry entry", () => {
    const id = dataIdentity(makeSpec({ plotType: "sankey", params: { depth: 3 } }));
    expect(id.params).toEqual({ depth: 3 });
  });
});

describe("buildRequest slot", () => {
  it("defaults the cancellation slot to the chart id, and lets a widget override it", () => {
    const spec = makeSpec({ id: "chart-1" });
    expect(buildRequest(spec, [], {}, []).slot).toBe("chart-1");
    expect(buildRequest(spec, [], { slot: "widget-9" }, []).slot).toBe("widget-9");
  });
});

describe("usedPipelines", () => {
  const p = (
    id: string,
    source: { kind: "dataset" | "pipeline"; id: string },
    steps: unknown[] = [],
  ) => ({ id, name: id, source, steps }) as never;

  it("returns only the pipelines the source depends on, transitively", () => {
    const pipelines = [
      p("a", { kind: "pipeline", id: "b" }),
      p("b", { kind: "dataset", id: "d" }, [
        { op: "join", right: { kind: "pipeline", id: "c" }, on: [] },
      ]),
      p("c", { kind: "dataset", id: "d" }),
      p("unrelated", { kind: "dataset", id: "d" }),
    ];
    const used = usedPipelines({ kind: "pipeline", id: "a" }, pipelines)
      .map((x) => x.id)
      .sort();
    expect(used).toEqual(["a", "b", "c"]);
    expect(usedPipelines({ kind: "dataset", id: "d" }, pipelines)).toEqual([]);
  });

  it("survives cycles", () => {
    const pipelines = [
      p("a", { kind: "pipeline", id: "b" }),
      p("b", { kind: "pipeline", id: "a" }),
    ];
    expect(usedPipelines({ kind: "pipeline", id: "a" }, pipelines)).toHaveLength(2);
  });
});

describe("useChartData", () => {
  it("retries a superseded (409) request instead of sticking on Loading (S9)", async () => {
    postArrow
      .mockRejectedValueOnce(new ApiError("superseded", 409))
      .mockResolvedValueOnce(arrow(makeData({ x: ["a"], y0: [1] })));
    const { result } = renderHook(() => useChartData(makeSpec()), { wrapper });
    await waitFor(() => expect(result.current.data?.rowCount).toBe(1), { timeout: 3000 });
    expect(postArrow).toHaveBeenCalledTimes(2);
  });

  it("returns a stat-transform (JSON) response as an empty table carrying `stat`, with no plot flag", async () => {
    const meta = makeData({ x: [] }).meta;
    postArrow.mockResolvedValue({
      kind: "json",
      body: { result: { values: { q1: 1 }, method: "m", n: 3, sampled: false }, meta },
    });
    const { result } = renderHook(() => useChartData(makeSpec({ plotType: "box" })), { wrapper });
    await waitFor(() => expect(result.current.data?.stat?.values).toEqual({ q1: 1 }));
    expect(result.current.data?.rowCount).toBe(0);
    expect(result.current.data?.meta).toBe(meta);
  });

  it("does not retry real errors (422 shows its message)", async () => {
    postArrow.mockRejectedValue(new ApiError("bad spec", 422));
    const { result } = renderHook(() => useChartData(makeSpec()), { wrapper });
    await waitFor(() => expect(result.current.error?.message).toBe("bad spec"));
    expect(postArrow).toHaveBeenCalledTimes(1);
  });

  it("sends the viewport with the zoom range and only the used pipelines", async () => {
    postArrow.mockResolvedValue(arrow(makeData({ x: ["a"], y0: [1] })));
    const spec = makeSpec({ source: { kind: "pipeline", id: "a" } });
    const pipelines = [
      { id: "a", name: "a", source: { kind: "dataset", id: "d" }, steps: [] },
      { id: "z", name: "z", source: { kind: "dataset", id: "d" }, steps: [] },
    ] as never;
    renderHook(
      () =>
        useChartData(spec, [], true, {
          xRange: [1, 2],
          size: () => ({ widthPx: 640, heightPx: 300 }),
          pipelines,
        }),
      { wrapper },
    );
    await waitFor(() => expect(postArrow).toHaveBeenCalled());
    const [path, body] = postArrow.mock.calls[0] as [
      string,
      { viewport: unknown; pipelines: { id: string }[] },
    ];
    expect(path).toBe("/api/charts/data");
    expect(body.viewport).toEqual({ widthPx: 640, heightPx: 300, xRange: [1, 2] });
    expect(body.pipelines.map((x) => x.id)).toEqual(["a"]);
  });
});

describe("fetchOverlays (nit)", () => {
  it("treats only 501 as 'no overlays'", async () => {
    postJson.mockRejectedValueOnce(new ApiError("not implemented", 501));
    expect(await fetchOverlays({ spec: makeSpec() })).toEqual([]);
    postJson.mockRejectedValueOnce(new ApiError("not found", 404));
    await expect(fetchOverlays({ spec: makeSpec() })).rejects.toThrow("not found");
  });
});
