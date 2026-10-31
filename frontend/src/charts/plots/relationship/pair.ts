import type { Column } from "../../../api/types";
import { maxOf, minOf } from "../../core/math";
import type { Obj } from "../../core/series";
import { applyStyle } from "../../core/style";
import {
  type ChannelSchema,
  type ChartData,
  definePlot,
  type ParamSchema,
  type PlotEntry,
} from "../../core/types";

const CHANNELS: ChannelSchema[] = [
  {
    name: "dimensions",
    label: "Fields",
    required: true,
    multiple: true,
    accepts: ["quantitative"],
    aggregate: false,
  },
  {
    name: "color",
    label: "Color by",
    required: false,
    multiple: false,
    accepts: ["nominal", "ordinal"],
    aggregate: false,
  },
];

const PARAMS: ParamSchema[] = [
  {
    name: "maxPoints",
    affectsData: true,
    type: "int",
    label: "Max sampled rows",
    min: 50,
    max: 20000,
    default: 2000,
  },
];

/** A quick client-side equal-width histogram for the diagonal cells (the sample is already
 * capped server-side, so this is cheap and only needs to look right, not be exact). */
function quickHistogram(values: number[], bins: number): { edges: string[]; counts: number[] } {
  const finite = values.filter((v) => typeof v === "number" && Number.isFinite(v));
  const lo = minOf(finite, 0);
  const hi = maxOf(finite, lo + 1);
  const width = (hi - lo || 1) / bins;
  const counts = new Array(bins).fill(0);
  for (const v of finite) {
    const index = Math.min(bins - 1, Math.max(0, Math.floor((v - lo) / width)));
    counts[index] += 1;
  }
  const edges = Array.from({ length: bins }, (_, i) => (lo + (i + 0.5) * width).toPrecision(3));
  return { edges, counts };
}

function dimensionKeys(data: ChartData): string[] {
  return Object.keys(data.meta.fields)
    .filter((k) => /^dimension\d+$/.test(k))
    .sort((a, b) => Number(a.slice(9)) - Number(b.slice(9)));
}

export const pair: PlotEntry = definePlot({
  key: "pair",
  label: "Pair plot",
  family: "relationship",
  description: "A grid of scatter plots for every pair of fields, with histograms on the diagonal.",
  encodingsSchema: CHANNELS,
  paramsSchema: PARAMS,
  styleSections: ["title", "legend", "tooltip", "palette"],
  defaults: {},
  render(spec, data, theme, ctx) {
    const dims = dimensionKeys(data);
    const n = Math.max(1, dims.length);
    const labels = dims.map((d) => data.meta.fields[d] ?? d);
    const colorCol = data.columns.color as unknown[] | undefined;
    const margin = 3;
    const cell = (100 - margin * (n + 1)) / n;

    const grids: Obj[] = [];
    const xAxes: Obj[] = [];
    const yAxes: Obj[] = [];
    const series: Obj[] = [];

    for (let row = 0; row < n; row++) {
      for (let col = 0; col < n; col++) {
        const idx = row * n + col;
        const left = margin + col * (cell + margin);
        const top = margin + row * (cell + margin);
        grids.push({ left: `${left}%`, top: `${top}%`, width: `${cell}%`, height: `${cell}%` });
        const xs = (data.columns[dims[col] as string] ?? []) as number[];
        const ys = (data.columns[dims[row] as string] ?? []) as number[];

        if (row === col) {
          const { edges, counts } = quickHistogram(xs, 12);
          xAxes.push({
            gridIndex: idx,
            type: "category",
            data: edges,
            show: row === n - 1,
            name: row === n - 1 ? labels[col] : undefined,
            nameLocation: "middle",
            nameGap: 24,
            axisLabel: { show: false },
            axisTick: { show: false },
          });
          yAxes.push({
            gridIndex: idx,
            type: "value",
            show: col === 0,
            name: col === 0 ? labels[row] : undefined,
            nameLocation: "middle",
            nameGap: 36,
            axisLabel: { show: false },
            splitLine: { show: false },
          });
          series.push({
            type: "bar",
            xAxisIndex: idx,
            yAxisIndex: idx,
            data: counts,
            barCategoryGap: "0%",
            silent: true,
            tooltip: { show: false },
          });
          continue;
        }

        xAxes.push({
          gridIndex: idx,
          type: "value",
          show: row === n - 1,
          name: row === n - 1 ? labels[col] : undefined,
          nameLocation: "middle",
          nameGap: 24,
          axisLabel: { show: false },
          splitLine: { show: false },
        });
        yAxes.push({
          gridIndex: idx,
          type: "value",
          show: col === 0,
          name: col === 0 ? labels[row] : undefined,
          nameLocation: "middle",
          nameGap: 36,
          axisLabel: { show: false },
          splitLine: { show: false },
        });
        if (!colorCol) {
          series.push({
            type: "scatter",
            xAxisIndex: idx,
            yAxisIndex: idx,
            data: xs.map((x, i) => [x, ys[i]]),
            symbolSize: 4,
          });
        } else {
          const byColor = new Map<unknown, [number, number][]>();
          colorCol.forEach((c, i) => {
            const pts = byColor.get(c);
            if (pts) pts.push([xs[i] as number, ys[i] as number]);
            else byColor.set(c, [[xs[i] as number, ys[i] as number]]);
          });
          for (const [c, pts] of byColor) {
            series.push({
              type: "scatter",
              name: String(c),
              xAxisIndex: idx,
              yAxisIndex: idx,
              data: pts,
              symbolSize: 4,
            });
          }
        }
      }
    }

    const option: Obj = { grid: grids, xAxis: xAxes, yAxis: yAxes, series };
    return applyStyle(option, spec, theme, ctx, { defaultTrigger: "item" });
  },
  recommend(columns: Column[]) {
    return columns.filter((c) => c.semantic === "quantitative").length >= 3 ? 0.35 : 0.1;
  },
});
