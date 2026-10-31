import type { ChartSpec, ChartStyle } from "../../api/types";
import { formatDateAuto, makeFormatter } from "./format";
import type { AxisStyle, ChartData, RenderContext } from "./types";

export type Obj = Record<string, unknown>;

/** A style with every optional piece filled in, so renderers never branch on `undefined`. */
export interface ResolvedStyle extends ChartStyle {
  xAxis: AxisStyle;
  yAxes: AxisStyle[];
  series: NonNullable<ChartStyle["series"]>;
  trendlines: NonNullable<ChartStyle["trendlines"]>;
  annotations: NonNullable<ChartStyle["annotations"]>;
  refLines: NonNullable<ChartStyle["refLines"]>;
  legend: NonNullable<ChartStyle["legend"]>;
  tooltip: NonNullable<ChartStyle["tooltip"]>;
}

export function resolveStyle(spec: ChartSpec): ResolvedStyle {
  const style = spec.style;
  const yAxes = style?.yAxes && style.yAxes.length > 0 ? style.yAxes : [{ grid: true }];
  return {
    orientation: "vertical",
    stack: "none",
    showLabels: false,
    ...style,
    xAxis: style?.xAxis ?? { grid: true },
    yAxes: yAxes as AxisStyle[],
    series: style?.series ?? [],
    trendlines: style?.trendlines ?? [],
    annotations: style?.annotations ?? [],
    refLines: style?.refLines ?? [],
    legend: style?.legend ?? { show: true, position: "top" },
    tooltip: style?.tooltip ?? { show: true },
  };
}

/** A plot-specific setting from `spec.params`, typed by the fallback. */
type Widen<T> = T extends string ? string : T extends number ? number : boolean;

export function param<T extends string | number | boolean>(
  spec: ChartSpec,
  name: string,
  fallback: T,
): Widen<T> {
  const value = spec.params?.[name];
  return (typeof value === typeof fallback ? value : fallback) as Widen<T>;
}

export function isHorizontal(spec: ChartSpec): boolean {
  return spec.style?.orientation === "horizontal";
}

export type XAxisType = "category" | "value" | "log" | "time";

/** Pick the x axis type: an explicit scale wins, otherwise the plot's default for the column. */
export function xAxisType(
  kind: string | undefined,
  scale: AxisStyle["scale"],
  continuousByDefault: boolean,
  values: unknown[],
): XAxisType {
  const numeric = values.every((v) => v === null || typeof v === "number");
  if (scale === "category") return "category";
  if (scale === "time") return numeric ? "time" : "category";
  if (scale === "log") return numeric ? "log" : "category";
  if (scale === "linear") return numeric ? "value" : "category";
  if (!continuousByDefault || !numeric) return "category";
  return kind === "temporal" ? "time" : kind === "number" ? "value" : "category";
}

/** Category label for a raw x value; temporal values arrive as epoch ms. */
export function categoryLabel(
  value: unknown,
  kind: string | undefined,
  format?: string | null,
  locale?: string,
) {
  if (value === null || value === undefined || value === "") return "(blank)";
  if (format && format !== "auto") return makeFormatter(format, locale)(value) || "(blank)";
  if (kind === "temporal") return formatDateAuto(value) || String(value);
  return typeof value === "number" ? makeFormatter("auto", locale)(value) : String(value);
}

export interface CartesianSeries {
  name: string;
  /** Index into the y channel. */
  yIndex: number;
  /** Set when the series comes from a color group (per-series color overrides do not apply). */
  group?: string;
  values: (number | null)[];
  /** Raw (unformatted) low/high error bounds, when chart data carries them. */
  lo?: (number | null)[];
  hi?: (number | null)[];
}

export interface CartesianData {
  /** Raw x values in order. */
  xs: unknown[];
  xKind: string | undefined;
  xLabel: string | undefined;
  series: CartesianSeries[];
}

const yColumns = (data: ChartData) =>
  Object.keys(data.columns)
    .filter((key) => /^y\d+$/.test(key))
    .sort((a, b) => Number(a.slice(1)) - Number(b.slice(1)));

const num = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

/**
 * Pivot chart data (columns `x`, `y0..yN`, optional `color`) into aligned series.
 * With a color column each (group, measure) becomes a series; x values are de-duplicated.
 * Error bounds come from `y<i>_lo` / `y<i>_hi` columns when the server supplies them.
 */
export function cartesianSeries(data: ChartData): CartesianData {
  const { columns, kinds, meta } = data;
  const xRaw = columns.x ?? [];
  const keys = yColumns(data);
  const colors = columns.color;
  const labelOf = (key: string, index: number) => meta.fields[key] ?? `Series ${index + 1}`;

  if (!colors) {
    return {
      xs: xRaw,
      xKind: kinds.x,
      xLabel: meta.fields.x,
      series: keys.map((key, i) => {
        const lo = columns[`${key}_lo`];
        const hi = columns[`${key}_hi`];
        return {
          name: labelOf(key, i),
          yIndex: i,
          values: (columns[key] ?? []).map(num),
          lo: lo?.map(num),
          hi: hi?.map(num),
        };
      }),
    };
  }

  const xs: unknown[] = [];
  const xIndex = new Map<unknown, number>();
  xRaw.forEach((x) => {
    if (!xIndex.has(x)) {
      xIndex.set(x, xs.length);
      xs.push(x);
    }
  });
  // Rows per color group, in first-seen order: one pass instead of one scan per group.
  const rowsByGroup = new Map<unknown, number[]>();
  colors.forEach((c, row) => {
    const rows = rowsByGroup.get(c);
    if (rows) rows.push(row);
    else rowsByGroup.set(c, [row]);
  });
  const series: CartesianSeries[] = [];
  for (const [group, rows] of rowsByGroup) {
    const groupName = categoryLabel(group, kinds.color);
    keys.forEach((key, i) => {
      // Values and, when the server sends them, error bounds, pivoted onto the shared x list.
      const pivot = (name: string): (number | null)[] | undefined => {
        const source = columns[name];
        if (!source) return undefined;
        const out: (number | null)[] = new Array(xs.length).fill(null);
        for (const row of rows) out[xIndex.get(xRaw[row]) as number] = num(source[row]);
        return out;
      };
      series.push({
        name: keys.length > 1 ? `${groupName} · ${labelOf(key, i)}` : groupName,
        yIndex: i,
        group: groupName,
        values: pivot(key) ?? [],
        lo: pivot(`${key}_lo`),
        hi: pivot(`${key}_hi`),
      });
    });
  }
  return { xs, xKind: kinds.x, xLabel: meta.fields.x, series };
}

/** Series data for the chosen x axis: aligned values for categories, [x, y] pairs otherwise. */
export function seriesData(xs: unknown[], values: (number | null)[], type: XAxisType): unknown[] {
  if (type === "category") return values;
  return xs.map((x, i) => [x, values[i] ?? null]);
}

/** Extent of the numeric x values (for detail-on-demand zoom). */
export function numericExtent(xs: unknown[]): [number, number] | null {
  let lo = Number.POSITIVE_INFINITY;
  let hi = Number.NEGATIVE_INFINITY;
  for (const x of xs) {
    if (typeof x === "number") {
      if (x < lo) lo = x;
      if (x > hi) hi = x;
    }
  }
  return lo <= hi ? [lo, hi] : null;
}

/**
 * dataZoom components for a cartesian chart. Value/time axes carry the visible range so a
 * zoomed re-query (which returns fewer rows) keeps the zoom and the full axis extent.
 */
export function dataZoomOption(
  spec: ChartSpec,
  o: { type: XAxisType; horizontal: boolean; count: number; slider?: boolean },
  ctx?: RenderContext,
): Obj[] | undefined {
  if (spec.interactions?.zoom === false) return undefined;
  const target = o.horizontal ? { yAxisIndex: 0 } : { xAxisIndex: 0 };
  const range = ctx?.viewport?.xRange;
  const continuous = o.type === "value" || o.type === "time" || o.type === "log";
  const view: Obj = continuous && range ? { startValue: range[0], endValue: range[1] } : {};
  // A slider helps on dense series; sparse bar charts rarely need one.
  const slider = !o.horizontal && (o.slider ?? o.count > 20);
  return [
    {
      type: "inside",
      ...target,
      ...view,
      filterMode: "none",
      // A brush owns the drag; wheel zoom and the slider still work.
      ...(spec.interactions?.brush ? { moveOnMouseMove: false } : {}),
    },
    ...(slider
      ? [{ type: "slider", ...target, ...view, filterMode: "none", height: 18, bottom: 6 }]
      : []),
  ];
}
