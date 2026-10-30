// @vitest-environment node
import { describe, expect, it } from "vitest";
import { echarts } from "./echarts";
import { listPlots } from "./registry";
import { makeData, makeSpec, makeTheme } from "./testing";

// A smoke test for EVERY registered plot, including the ones Wave 2 agents add: render with a
// tiny fixture and draw it through real ECharts (SVG, no DOM). It checks that a plot survives
// minimal data (no throw, no NaN crash) and keeps accessibility on. A plot that cannot render
// one row should return an empty-but-valid option, not throw.

const oneRow = () =>
  makeData(
    { x: ["a"], y0: [1], y: [1], color: ["g"], count: [1] },
    { x: "x", y0: "Value", y: "Value" },
  );

describe("every registered plot renders a one-row fixture", () => {
  for (const entry of listPlots()) {
    it(entry.key, () => {
      const spec = makeSpec({ plotType: entry.key, params: { ...entry.defaults.params } });
      const option = entry.render(spec, oneRow(), makeTheme()) as Record<string, unknown>;
      expect(option).toBeTypeOf("object");
      expect((option.aria as { enabled?: boolean } | undefined)?.enabled).toBe(true);

      const chart = echarts.init(null, undefined, {
        renderer: "svg",
        ssr: true,
        width: 400,
        height: 300,
      });
      try {
        chart.setOption({ animation: false, ...option });
        expect(chart.renderToSVGString()).toContain("<svg");
      } finally {
        chart.dispose();
      }
    });
  }
});
