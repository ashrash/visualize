import type { ChartSpec } from "../../../api/types";
import { errorBars } from "../../core/primitives/errorBars";
import { applyStyle } from "../../core/style";
import type { ChartTheme, RenderContext } from "../../core/types";
import { colorsFor } from "../multivariate/stat";

type Obj = Record<string, unknown>;

/** One row of an interval plot (forest, coefficient, marginal effects). */
export interface IntervalRow {
  label: string;
  estimate: number | null;
  lo: number | null;
  hi: number | null;
  /** Tooltip text (HTML). */
  tooltip: string;
  /** Pooled estimates draw as a diamond spanning their interval. */
  pooled?: boolean;
  /** Marker size in px (forest: by weight). */
  size?: number;
  /** Index into the palette (subgroups). */
  colorIndex?: number;
}

interface RenderApi {
  value(dim: number): number;
  coord(point: number[]): number[];
  size(point: number[]): number[];
}

/**
 * A horizontal interval plot: one category row per estimate (first row at the top), a point
 * with an error bar (the shared primitive), diamonds for pooled rows, and a dashed null line.
 */
export function intervalOption(
  spec: ChartSpec,
  theme: ChartTheme,
  ctx: RenderContext | undefined,
  rows: IntervalRow[],
  o: { valueName: string; nullValue: number | null; log?: boolean },
): Obj {
  // Category axes run bottom-up: reverse so the first row sits at the top.
  const ordered = [...rows].reverse();
  const colors = colorsFor(spec, theme, Math.max(1, ...rows.map((r) => (r.colorIndex ?? 0) + 1)));
  const valid = (v: number | null) => v !== null && (!o.log || v > 0);
  const points = ordered
    .map((r, i) => ({ r, i }))
    .filter(({ r }) => !r.pooled && valid(r.estimate));
  const pooled = ordered.map((r, i) => ({ r, i })).filter(({ r }) => r.pooled);
  const fill = (index: number | undefined) =>
    index === undefined || index < 0 ? theme.textMuted : colors[index % colors.length];
  const whiskers = errorBars({
    x: points.map(({ i }) => i),
    lo: points.map(({ r }) => (valid(r.lo) ? r.lo : null)),
    hi: points.map(({ r }) => (valid(r.hi) ? r.hi : null)),
    orientation: "h",
    color: theme.text,
    capWidth: 3,
    theme,
  });
  // Rows are [rowIndex, lo, hi]; wrap them so applyStyle's log-axis filter (which drops array
  // items whose first entry is <= 0, i.e. row 0 here) leaves them alone.
  whiskers.data = (whiskers.data as unknown[][]).map((row) => ({ value: row }));
  const series: Obj[] = [
    {
      type: "scatter",
      // Unnamed: colors mean subgroups, which the row labels already say; no legend.
      symbol: "rect",
      data: points.map(({ r, i }) => ({
        value: [r.estimate, i],
        symbolSize: r.size ?? 8,
        itemStyle: { color: fill(r.colorIndex ?? 0) },
        tooltip: r.tooltip,
      })),
      z: 4,
      tooltip: { formatter: (p: { data: { tooltip: string } }) => p.data.tooltip },
      markLine:
        o.nullValue !== null && spec.params?.nullLine !== false
          ? {
              silent: true,
              symbol: "none",
              lineStyle: { type: "dashed", color: theme.textMuted },
              label: { show: false },
              data: [{ xAxis: o.nullValue }],
            }
          : undefined,
    },
    whiskers,
  ];
  if (pooled.length) {
    series.push({
      type: "custom",
      z: 5,
      data: pooled.map(({ r, i }) => ({
        value: [i, r.lo, r.estimate, r.hi],
        tooltip: r.tooltip,
      })),
      encode: { y: 0, x: [1, 3] },
      tooltip: { formatter: (p: { data: { tooltip: string } }) => p.data.tooltip },
      renderItem(params: { dataIndex: number }, api: RenderApi) {
        const row = api.value(0);
        const half = (api.size([0, 1])[1] ?? 0) * 0.3;
        const [lx = 0, ly = 0] = api.coord([api.value(1), row]);
        const [ex = 0] = api.coord([api.value(2), row]);
        const [hx = 0] = api.coord([api.value(3), row]);
        const color = pooled[params.dataIndex]?.r.colorIndex;
        return {
          type: "polygon",
          shape: {
            points: [
              [lx, ly],
              [ex, ly - half],
              [hx, ly],
              [ex, ly + half],
            ],
          },
          style: { fill: fill(color), stroke: theme.text, lineWidth: 1 },
        };
      },
    });
  }
  const option = {
    xAxis: { type: o.log ? "log" : "value", name: o.valueName, scale: true },
    yAxis: {
      type: "category",
      data: ordered.map((r) => r.label),
      axisTick: { show: false },
      axisLabel: { interval: 0 },
    },
    tooltip: { trigger: "item" },
    series,
  };
  return detachAxisLine(
    applyStyle(option, spec, theme, ctx, { orientable: false, defaultTrigger: "item" }),
    "yAxis",
  );
}

/**
 * Draw an axis at the edge of the plot instead of through the other axis' zero, so a vertical
 * line at 0 is only ever the dashed null line (applyStyle rebuilds axisLine, so this runs after).
 */
export function detachAxisLine(option: Obj, key: "xAxis" | "yAxis"): Obj {
  const axis = option[key];
  if (!axis || Array.isArray(axis)) return option;
  const a = axis as Obj;
  return { ...option, [key]: { ...a, axisLine: { ...(a.axisLine as Obj), onZero: false } } };
}
