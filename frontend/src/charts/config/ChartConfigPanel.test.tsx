// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ChartSpec, Column } from "../../api/types";
import { getPlot } from "../core/registry";
import { makeSpec } from "../core/testing";
import { definePlot } from "../core/types";
import { ChartConfigPanel } from "./ChartConfigPanel";
import { isVisible } from "./ParamsTab";
import {
  applyPlotDefaults,
  applyTemplate,
  createChartSpec,
  setChannel,
  templateStyle,
} from "./spec";

afterEach(cleanup);

const columns: Column[] = [
  { name: "region", type: "VARCHAR", semantic: "nominal", nullCount: 0, approxDistinct: 4 },
  { name: "sales", type: "DOUBLE", semantic: "quantitative", nullCount: 0, approxDistinct: 100 },
  { name: "day", type: "DATE", semantic: "temporal", nullCount: 0, approxDistinct: 30 },
] as Column[];

function setup(spec: ChartSpec = makeSpec({ encodings: { x: [{ field: "region" }] } })) {
  const onChange = vi.fn();
  render(<ChartConfigPanel spec={spec} columns={columns} onChange={onChange} />);
  return onChange;
}

const call = (fn: ReturnType<typeof vi.fn>, i: number) => fn.mock.calls[i]?.[0] as ChartSpec;
const tab = (name: string) => screen.getByRole("tab", { name });

describe("ChartConfigPanel", () => {
  it("shows the plot's channels, offering only columns the channel accepts", () => {
    setup();
    const y = screen.getByLabelText("Value 1") as HTMLSelectElement;
    const options = [...y.options].map((o) => o.value);
    expect(options).toContain("sales");
    expect(options).not.toContain("region"); // y accepts quantitative columns only
    const x = screen.getByLabelText("Category") as HTMLSelectElement;
    expect([...x.options].map((o) => o.value)).toEqual(expect.arrayContaining(["region", "day"]));
  });

  it("writes a picked field with a default aggregate into the spec", () => {
    const onChange = setup();
    fireEvent.change(screen.getByLabelText("Value 1"), { target: { value: "sales" } });
    const spec = onChange.mock.calls[0]?.[0] as ChartSpec;
    expect(spec.encodings?.y).toEqual([{ field: "sales", agg: "sum" }]);
  });

  it("offers time units for a temporal x column", () => {
    setup(makeSpec({ encodings: { x: [{ field: "day" }] } }));
    expect(screen.getByLabelText("Category: group dates by")).toBeTruthy();
  });

  it("lets multiple-channel rows be added", () => {
    const onChange = setup();
    fireEvent.click(screen.getByRole("button", { name: "Add Value" }));
    expect(call(onChange, 0).encodings?.y).toEqual([{ agg: "count" }]);
  });

  it("edits drill-down levels for plots that can drill, offering only category columns", () => {
    const onChange = setup();
    fireEvent.click(screen.getByRole("button", { name: "Add drill-down level" }));
    expect(call(onChange, 0).interactions?.drillHierarchy).toEqual(["region"]);
    // Existing levels stay editable and removable.
    cleanup();
    const next = vi.fn();
    render(
      <ChartConfigPanel
        spec={makeSpec({
          encodings: { x: [{ field: "region" }] },
          interactions: {
            zoom: true,
            brush: false,
            clickFilter: true,
            drillHierarchy: ["region", "day"],
          },
        })}
        columns={[
          ...columns,
          { name: "city", type: "VARCHAR", semantic: "nominal", nullCount: 0, approxDistinct: 9 },
        ]}
        onChange={next}
      />,
    );
    const level = screen.getByLabelText("Drill-down level 1") as HTMLSelectElement;
    expect([...level.options].map((o) => o.value)).not.toContain("sales");
    fireEvent.change(level, { target: { value: "city" } });
    expect(call(next, 0).interactions?.drillHierarchy).toEqual(["city", "day"]);
    fireEvent.click(screen.getByRole("button", { name: "Remove drill-down level 2" }));
    expect(call(next, 1).interactions?.drillHierarchy).toEqual(["region"]);
  });

  it("hides drill-down for plots that cannot drill, and offers brushing on axis plots", () => {
    setup(makeSpec({ plotType: "histogram" }));
    expect(screen.queryByRole("button", { name: "Add drill-down level" })).toBeNull();
    cleanup();
    const onChange = setup();
    fireEvent.click(screen.getByLabelText("Select ranges by dragging on the chart"));
    expect(call(onChange, 0).interactions?.brush).toBe(true);
  });

  it("generates Chart-tab controls from paramsSchema and hides showIf-gated ones", () => {
    const onChange = setup();
    fireEvent.click(tab("Chart"));
    expect(screen.getByLabelText("Bar width (%)")).toBeTruthy();
    expect(screen.queryByLabelText("Gap between grouped bars (%)")).toBeNull(); // needs a color channel
    fireEvent.change(screen.getByLabelText("Bar width (%)"), { target: { value: "80" } });
    expect(call(onChange, 0).params).toMatchObject({ barWidth: 80 });
  });

  it("shows only the style sections the plot declares", () => {
    setup(makeSpec({ plotType: "pie" }));
    fireEvent.click(tab("Style"));
    expect(screen.getByText("Title and text")).toBeTruthy();
    expect(screen.queryByText("Axes")).toBeNull();
    expect(screen.queryByText("Reference lines")).toBeNull();
  });

  it("edits axis settings: log scale, title and a second y axis", () => {
    const onChange = setup();
    fireEvent.click(tab("Style"));
    fireEvent.change(screen.getByLabelText("Y axis: scale"), { target: { value: "log" } });
    expect(call(onChange, 0).style?.yAxes?.[0]).toMatchObject({
      scale: "log",
    });
    fireEvent.click(screen.getByRole("button", { name: "+ Add y axis" }));
    expect(call(onChange, 1).style?.yAxes).toHaveLength(2);
  });

  it("adds a reference line and a trendline to the style", () => {
    const onChange = setup(makeSpec({ plotType: "line", encodings: { x: [{ field: "day" }] } }));
    fireEvent.click(tab("Style"));
    fireEvent.click(screen.getByRole("button", { name: "+ Add reference line" }));
    expect(call(onChange, 0).style?.refLines).toEqual([
      { axis: "y", value: 0, lineStyle: "dashed" },
    ]);
    fireEvent.click(screen.getByRole("button", { name: "+ Add trendline" }));
    expect(call(onChange, 1).style?.trendlines?.[0]).toMatchObject({
      kind: "linear",
    });
  });

  it("switches plot type, applying defaults and dropping unknown channels", () => {
    const onChange = setup(
      makeSpec({ encodings: { x: [{ field: "region" }], color: [{ field: "region" }] } }),
    );
    fireEvent.change(screen.getByLabelText("Chart type"), { target: { value: "pie" } });
    const next = onChange.mock.calls[0]?.[0] as ChartSpec;
    expect(next.plotType).toBe("pie");
    expect(next.encodings).toEqual({ x: [{ field: "region" }] });
    expect(next.params).toMatchObject({ maxSlices: 12 });
  });

  it("tabs are keyboard operable with arrow keys", () => {
    setup();
    fireEvent.keyDown(tab("Data"), { key: "ArrowRight" });
    expect(tab("Chart").getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(tab("Chart"));
    expect(within(screen.getByRole("tabpanel")).getByLabelText("Bar width (%)")).toBeTruthy();
  });

  it("offers templates and applies them", () => {
    const onChange = vi.fn();
    const style = {
      orientation: "vertical",
      stack: "none",
      showLabels: true,
      palette: "okabe-ito",
    } as const;
    render(
      <ChartConfigPanel
        spec={makeSpec({
          style: { orientation: "vertical", stack: "none", showLabels: false, title: "Mine" },
        })}
        columns={columns}
        onChange={onChange}
        templates={[{ id: "t1", name: "Brand", style }]}
      />,
    );
    fireEvent.click(tab("Style"));
    fireEvent.change(screen.getByLabelText("Style template"), { target: { value: "t1" } });
    const next = onChange.mock.calls[0]?.[0] as ChartSpec;
    expect(next.style).toMatchObject({
      palette: "okabe-ito",
      showLabels: true,
      title: "Mine",
      templateId: "t1",
    });
  });
});

describe("plot-type switch (S6)", () => {
  it("resets style the target plot has no section for", () => {
    const stale = makeSpec({
      plotType: "bar",
      style: {
        orientation: "horizontal",
        stack: "percent",
        showLabels: true,
        errorBars: { kind: "sd", level: 0.95 },
        trendlines: [{ kind: "linear", series: 0, degree: 2, showEquation: false, showR2: false }],
        refLines: [{ axis: "y", value: 1, lineStyle: "dashed" }],
        series: [{ index: 0, yAxis: 1 }],
        palette: "viridis",
      },
    });
    const pie = getPlot("pie");
    if (!pie) throw new Error("pie missing");
    const next = applyPlotDefaults(stale, pie);
    expect(next.style).toMatchObject({
      orientation: "vertical",
      stack: "none",
      errorBars: null,
      trendlines: [],
      refLines: [],
      series: [],
      // Settings the pie does support carry over.
      palette: "viridis",
      showLabels: true,
    });
  });

  it("resets value labels only when the target plot has no labels section (N1)", () => {
    const pie = getPlot("pie");
    const bar = getPlot("bar");
    if (!pie || !bar) throw new Error("missing");
    const asPie = applyPlotDefaults(makeSpec(), pie);
    expect(asPie.style?.showLabels).toBe(true);
    // A plot with the labels section keeps the user's choice.
    expect(applyPlotDefaults(asPie, bar).style?.showLabels).toBe(true);
    // A plot without it cannot show or hide them, so they are cleared.
    const noLabels = { ...bar, styleSections: bar.styleSections.filter((s) => s !== "labels") };
    expect(applyPlotDefaults(asPie, noLabels).style?.showLabels).toBe(false);
  });

  it("keeps settings the target plot supports", () => {
    const area = getPlot("area");
    if (!area) throw new Error("area missing");
    const next = applyPlotDefaults(
      makeSpec({
        style: {
          orientation: "vertical",
          stack: "stack",
          showLabels: false,
          refLines: [{ axis: "y", value: 1, lineStyle: "dashed" }],
        },
      }),
      area,
    );
    expect(next.style).toMatchObject({ stack: "stack", refLines: [{ axis: "y", value: 1 }] });
    expect(next.style?.trendlines).toEqual([]); // area has no trendline section
  });
});

describe("params UI (nits)", () => {
  it("shows the grouped-bar gap for a color group or for several measures", () => {
    const bar = getPlot("bar");
    const gap = bar?.paramsSchema.find((p) => p.name === "barGap");
    if (!bar || !gap) throw new Error("bar missing");
    expect(isVisible(makeSpec(), bar, gap)).toBe(false);
    expect(isVisible(makeSpec({ encodings: { color: [{ field: "r" }] } }), bar, gap)).toBe(true);
    expect(
      isVisible(
        makeSpec({
          encodings: {
            y: [
              { field: "a", agg: "sum" },
              { field: "b", agg: "sum" },
            ],
          },
        }),
        bar,
        gap,
      ),
    ).toBe(true);
    expect(isVisible(makeSpec({ encodings: { y: [{ field: "a", agg: "sum" }] } }), bar, gap)).toBe(
      false,
    );
  });

  it("clamps a typed number to the param's range when the box loses focus", () => {
    const onChange = setup(makeSpec({ encodings: { x: [{ field: "region" }] } }));
    fireEvent.click(tab("Chart"));
    const box = screen.getByLabelText("Bar width (%)") as HTMLInputElement;
    fireEvent.change(box, { target: { value: "5" } }); // below the minimum of 10: held in the box
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.blur(box);
    expect(call(onChange, 0).params).toMatchObject({ barWidth: 10 });
    expect(box.value).toBe("10");
    fireEvent.change(box, { target: { value: "500" } });
    fireEvent.blur(box);
    expect(call(onChange, onChange.mock.calls.length - 1).params).toMatchObject({ barWidth: 100 });
  });

  it("Home and End move between tabs", () => {
    setup();
    fireEvent.keyDown(tab("Data"), { key: "End" });
    expect(tab("Style").getAttribute("aria-selected")).toBe("true");
    fireEvent.keyDown(tab("Style"), { key: "Home" });
    expect(tab("Data").getAttribute("aria-selected")).toBe("true");
  });
});

describe("spec helpers", () => {
  it("setChannel adds and removes a channel", () => {
    const spec = setChannel(makeSpec(), "x", [{ field: "a" }]);
    expect(spec.encodings).toEqual({ x: [{ field: "a" }] });
    expect(setChannel(spec, "x", []).encodings).toEqual({});
  });

  it("applyPlotDefaults merges defaults and createChartSpec builds a full spec", () => {
    const stacked = getPlot("stacked_bar");
    if (!stacked) throw new Error("missing");
    const spec = createChartSpec(stacked, { id: "n1", source: { kind: "dataset", id: "d" } });
    expect(spec).toMatchObject({ id: "n1", plotType: "stacked_bar", name: "Stacked bar chart" });
    expect(spec.params).toMatchObject({ barWidth: 60 });
    expect(applyPlotDefaults(makeSpec(), stacked).plotType).toBe("stacked_bar");
  });

  it("templates keep the look and leave chart-specific parts alone", () => {
    const styled = templateStyle({
      orientation: "horizontal",
      stack: "none",
      showLabels: true,
      title: "T",
      palette: "viridis",
      refLines: [{ axis: "y", value: 1, lineStyle: "dashed" }],
      xAxis: { grid: true, title: "Region", min: 0 },
    });
    expect(styled.title).toBeUndefined();
    expect(styled.refLines).toBeUndefined();
    expect(styled.palette).toBe("viridis");
    expect(styled.xAxis).toMatchObject({ title: null, min: null });
    const applied = applyTemplate(
      makeSpec({
        style: {
          orientation: "vertical",
          stack: "none",
          showLabels: false,
          title: "Mine",
          xAxis: { grid: true, title: "Area" },
        },
      }),
      styled,
      "t",
    );
    expect(applied.style).toMatchObject({
      title: "Mine",
      palette: "viridis",
      orientation: "horizontal",
    });
    expect(applied.style?.xAxis?.title).toBe("Area");
  });

  it("evaluates showIf against params and bound channels", () => {
    const entry = definePlot({
      ...(getPlot("bar") as NonNullable<ReturnType<typeof getPlot>>),
      paramsSchema: [
        {
          name: "mode",
          affectsData: false,
          type: "select",
          label: "Mode",
          default: "a",
          options: [
            { value: "a", label: "A" },
            { value: "b", label: "B" },
          ],
        },
        {
          name: "extra",
          affectsData: false,
          type: "boolean",
          label: "Extra",
          default: false,
          showIf: { param: "mode", equals: "b" },
        },
        {
          name: "grouped",
          affectsData: false,
          type: "boolean",
          label: "G",
          default: false,
          showIf: { param: "color", equals: true },
        },
      ],
    });
    const [, extra, grouped] = entry.paramsSchema;
    if (!extra || !grouped) throw new Error("schema");
    expect(isVisible(makeSpec(), entry, extra)).toBe(false);
    expect(isVisible(makeSpec({ params: { mode: "b" } }), entry, extra)).toBe(true);
    expect(isVisible(makeSpec(), entry, grouped)).toBe(false);
    expect(isVisible(makeSpec({ encodings: { color: [{ field: "r" }] } }), entry, grouped)).toBe(
      true,
    );
  });
});
