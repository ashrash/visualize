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
          type: "boolean",
          label: "Extra",
          default: false,
          showIf: { param: "mode", equals: "b" },
        },
        {
          name: "grouped",
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
