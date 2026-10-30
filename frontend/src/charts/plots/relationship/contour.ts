import type { Column } from "../../../api/types";
import { makeFormatter } from "../../core/format";
import { maxOf, minOf } from "../../core/math";
import { sequentialRamp } from "../../core/palettes";
import type { Obj } from "../../core/series";
import { applyStyle } from "../../core/style";
import { resolveTheme } from "../../core/theme";
import { type ChannelSchema, definePlot, type ParamSchema, type PlotEntry } from "../../core/types";

const CHANNELS: ChannelSchema[] = [
  {
    name: "x",
    label: "X",
    required: true,
    multiple: false,
    accepts: ["quantitative"],
    aggregate: false,
  },
  {
    name: "y",
    label: "Y",
    required: true,
    multiple: false,
    accepts: ["quantitative"],
    aggregate: false,
  },
];

const PARAMS: ParamSchema[] = [
  {
    name: "grid",
    affectsData: false,
    type: "int",
    label: "Grid resolution",
    min: 8,
    max: 60,
    default: 28,
  },
  {
    name: "showPoints",
    affectsData: false,
    type: "boolean",
    label: "Show sampled points",
    default: false,
  },
];

// NOTE for the lead / ui-charts: this plot does NOT use the official
// `@echarts-x/custom-contour` series, even though it is listed as the mandated renderer for
// this plot. Its compiled output does `import { zrUtil } from 'echarts'` (the full kitchen-sink
// package, not `echarts/core`): importing it anywhere pulls in and self-registers every ECharts
// chart type, component and renderer, which (a) defeats the tree-shaken bundle CLAUDE.md §1/§2
// require and (b) was observed to break `charts/core/render.test.ts`'s "rejects unregistered
// component types" test, because components the test expects to be absent (e.g. polar) become
// registered as a side effect. `@echarts-x/custom-line-range` has the same `from 'echarts'`
// import and would have the same effect; `@echarts-x/custom-violin` and
// `@echarts-x/custom-bar-range` do not import `echarts` at all and are unaffected.
// Until upstream ships an `echarts/core`-compatible build, this renders the same 2-D density
// data (the server already reduces to a capped, representative sample) as a binned heatmap,
// using the heatmap series already registered for `correlation_heatmap`.
function quickBins(values: number[], n: number): { lo: number; width: number } {
  const finite = values.filter((v) => typeof v === "number" && Number.isFinite(v));
  const lo = minOf(finite, 0);
  const hi = maxOf(finite, lo + 1);
  return { lo, width: (hi - lo || 1) / n };
}

export const contour: PlotEntry = definePlot({
  key: "contour",
  label: "Contour plot",
  family: "relationship",
  description: "A binned density surface for two numeric fields.",
  encodingsSchema: CHANNELS,
  paramsSchema: PARAMS,
  styleSections: ["title", "axes", "tooltip", "palette"],
  defaults: {},
  render(spec, data, theme, ctx) {
    const xs = (data.columns.x ?? []) as number[];
    const ys = (data.columns.y ?? []) as number[];
    const n = Math.max(2, Math.round(Number(spec.params?.grid ?? 28)));
    const showPoints = spec.params?.showPoints === true;
    const bx = quickBins(xs, n);
    const by = quickBins(ys, n);
    const counts = new Array(n * n).fill(0);
    xs.forEach((x, i) => {
      const y = ys[i];
      if (typeof x !== "number" || typeof y !== "number") return;
      if (!Number.isFinite(x) || !Number.isFinite(y)) return;
      const ix = Math.min(n - 1, Math.max(0, Math.floor((x - bx.lo) / bx.width)));
      const iy = Math.min(n - 1, Math.max(0, Math.floor((y - by.lo) / by.width)));
      counts[iy * n + ix] += 1;
    });
    const fmt = makeFormatter("auto", ctx?.locale);
    const xLabels = Array.from({ length: n }, (_, i) => fmt(bx.lo + (i + 0.5) * bx.width));
    const yLabels = Array.from({ length: n }, (_, i) => fmt(by.lo + (i + 0.5) * by.width));
    const cells: number[][] = [];
    for (let iy = 0; iy < n; iy++) {
      for (let ix = 0; ix < n; ix++) {
        const c = counts[iy * n + ix] ?? 0;
        if (c > 0) cells.push([ix, iy, c]);
      }
    }
    const t = resolveTheme(theme);

    const option: Obj = {
      xAxis: {
        type: "category",
        data: xLabels,
        name: data.meta.fields.x,
        splitArea: { show: true },
      },
      yAxis: {
        type: "category",
        data: yLabels,
        name: data.meta.fields.y,
        splitArea: { show: true },
      },
      tooltip: { trigger: "item" },
      visualMap: {
        type: "continuous",
        min: 0,
        max: maxOf(counts, 1),
        calculable: false,
        orient: "vertical",
        right: 4,
        top: "middle",
        inRange: { color: sequentialRamp(t) },
        textStyle: { color: t.textMuted },
        text: ["Dense", "Sparse"],
      },
      series: [
        { type: "heatmap", name: "Density", data: cells },
        ...(showPoints
          ? [
              {
                type: "scatter",
                name: "Points",
                coordinateSystem: "cartesian2d" as const,
                data: xs.map((x, i) => [
                  Math.min(n - 1, Math.max(0, Math.floor((x - bx.lo) / bx.width))) + 0.5,
                  Math.min(n - 1, Math.max(0, Math.floor(((ys[i] as number) - by.lo) / by.width))) +
                    0.5,
                ]),
                symbolSize: 2,
                itemStyle: { opacity: 0.3, color: t.text },
                tooltip: { show: false },
              },
            ]
          : []),
      ],
    };
    return applyStyle(option, spec, theme, ctx, { defaultTrigger: "item" });
  },
  recommend(columns: Column[]) {
    return columns.filter((c) => c.semantic === "quantitative").length >= 2 ? 0.15 : 0.05;
  },
});
