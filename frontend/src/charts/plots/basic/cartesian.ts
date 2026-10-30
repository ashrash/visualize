import type { ChartSpec } from "../../../api/types";
import { maxOf } from "../../core/math";
import { errorBars } from "../../core/primitives/errorBars";
import {
  cartesianSeries,
  categoryLabel,
  dataZoomOption,
  isHorizontal,
  type Obj,
  param,
  resolveStyle,
  seriesData,
  type XAxisType,
  xAxisType,
} from "../../core/series";
import { applyStyle } from "../../core/style";
import type {
  ChannelSchema,
  ChartData,
  ChartTheme,
  ParamSchema,
  RenderContext,
} from "../../core/types";

export type CartesianKind = "bar" | "line" | "area";

/** Channels shared by bar / line / area: x, any number of y measures, and a color group. */
export function cartesianChannels(
  xLabel: string,
  xAccepts: ChannelSchema["accepts"],
): ChannelSchema[] {
  return [
    {
      name: "x",
      label: xLabel,
      required: true,
      multiple: false,
      accepts: xAccepts,
      aggregate: false,
      timeUnit: true,
      sort: true,
    },
    {
      name: "y",
      label: "Value",
      required: false,
      multiple: true,
      accepts: ["quantitative"],
      aggregate: true,
      hint: "Leave empty to count rows.",
    },
    {
      name: "color",
      label: "Group by",
      required: false,
      multiple: false,
      accepts: ["nominal", "ordinal"],
      aggregate: false,
    },
  ];
}

export const BAR_PARAMS: ParamSchema[] = [
  {
    name: "barWidth",
    type: "number",
    label: "Bar width (%)",
    min: 10,
    max: 100,
    step: 5,
    default: 60,
  },
  {
    name: "barGap",
    type: "number",
    label: "Gap between grouped bars (%)",
    min: 0,
    max: 100,
    step: 5,
    default: 10,
    // Grouped bars appear with a color group or with several measures.
    showIf: [
      { param: "color", equals: true },
      { param: "y", atLeast: 2 },
    ],
  },
];

export const LINE_PARAMS: ParamSchema[] = [
  {
    name: "step",
    type: "select",
    label: "Line shape",
    default: "none",
    options: [
      { value: "none", label: "Straight" },
      { value: "start", label: "Step (start)" },
      { value: "middle", label: "Step (middle)" },
      { value: "end", label: "Step (end)" },
    ],
  },
  { name: "connectNulls", type: "boolean", label: "Connect across gaps", default: false },
];

function normalizePercent(series: { values: (number | null)[] }[]): void {
  const length = maxOf(
    series.map((s) => s.values.length),
    0,
  );
  for (let i = 0; i < length; i++) {
    let total = 0;
    for (const s of series) total += Math.abs(s.values[i] ?? 0);
    for (const s of series) {
      const v = s.values[i];
      s.values[i] = v === null || v === undefined ? null : total === 0 ? 0 : v / total;
    }
  }
}

/** Shared renderer for bar, line and area: pivot data, build series, then `applyStyle`. */
export function renderCartesian(
  kind: CartesianKind,
  spec: ChartSpec,
  data: ChartData,
  theme: ChartTheme,
  ctx?: RenderContext,
): Obj {
  const style = resolveStyle(spec);
  const horizontal = isHorizontal(spec);
  const cd = cartesianSeries(data);
  const stacked = style.stack !== "none";
  const percent = style.stack === "percent";
  if (percent) normalizePercent(cd.series);

  const type: XAxisType = xAxisType(cd.xKind, style.xAxis.scale, kind !== "bar", cd.xs);
  const categories =
    type === "category"
      ? cd.xs.map((x) => categoryLabel(x, cd.xKind, style.xAxis.format, ctx?.locale))
      : undefined;
  const dataFor = (values: (number | null)[]) => seriesData(categories ?? cd.xs, values, type);

  const comboBars = style.series.some((s) => s.kind === "bar");
  const barWidth = param(spec, "barWidth", 60);
  const barGap = param(spec, "barGap", 10);
  const step = param(spec, "step", "none");
  const connectNulls = param(spec, "connectNulls", false);
  const dense = cd.xs.length > 60;

  const series: Obj[] = cd.series.map((s) => {
    const base: Obj = {
      name: s.name,
      yIndex: s.yIndex,
      group: s.group,
      data: dataFor(s.values),
      emphasis: { focus: "series" },
      stack: stacked ? "total" : undefined,
    };
    if (kind === "bar") {
      return {
        ...base,
        type: "bar",
        barCategoryGap: `${100 - barWidth}%`,
        barGap: `${barGap}%`,
      };
    }
    return {
      ...base,
      type: "line",
      showSymbol: !dense,
      step: step === "none" ? undefined : step,
      connectNulls,
      ...(kind === "area" ? { areaStyle: { opacity: 0.25 } } : {}),
    };
  });

  // Error bars come from y{i}_lo / y{i}_hi columns the server adds when style.errorBars is set.
  if (style.errorBars && !stacked) {
    const withBounds = cd.series.filter((s) => s.lo && s.hi);
    withBounds.forEach((s, i) => {
      series.push({
        ...errorBars({
          x: categories ?? cd.xs,
          lo: s.lo as (number | null)[],
          hi: s.hi as (number | null)[],
          orientation: horizontal ? "h" : "v",
          groupIndex: kind === "bar" ? i : 0,
          groupCount: kind === "bar" ? withBounds.length : 1,
          categoryGap: (100 - barWidth) / 100,
          barGap: barGap / 100,
          theme,
        }),
        yIndex: s.yIndex,
      });
    });
  }

  const single = cd.series.length === 1 ? cd.series[0]?.name : undefined;
  const option: Obj = {
    xAxis: {
      type: type === "log" ? "log" : type,
      data: categories,
      name: cd.xLabel,
      boundaryGap: type === "category" ? kind === "bar" || comboBars : undefined,
    },
    yAxis: { type: "value", name: single },
    series,
    tooltip: { trigger: "axis", axisPointer: { type: kind === "bar" ? "shadow" : "line" } },
    dataZoom: dataZoomOption(spec, { type, horizontal, count: cd.xs.length }, ctx),
  };
  return applyStyle(option, spec, theme, ctx, {
    defaultYFormat: percent ? "percent:0" : undefined,
  });
}

export function hasMeasureAndCategory(
  columns: { semantic: string; approxDistinct: number }[],
  maxDistinct: number,
): [boolean, boolean] {
  return [
    columns.some((c) => c.semantic === "nominal" && c.approxDistinct <= maxDistinct),
    columns.some((c) => c.semantic === "quantitative"),
  ];
}

/**
 * Notices for the chart footer: values a log axis cannot draw (zero or negative) are left out
 * of the drawn series, and the user is told how many.
 */
export function cartesianWarnings(spec: ChartSpec, data: ChartData): string[] {
  const style = resolveStyle(spec);
  const logged = style.yAxes.some((a) => a.scale === "log");
  if (!logged) return [];
  let omitted = 0;
  for (const s of cartesianSeries(data).series) {
    const axis = style.series.find((x) => x.index === s.yIndex)?.yAxis ?? 0;
    if (style.yAxes[axis]?.scale !== "log") continue;
    for (const v of s.values) if (v !== null && v <= 0) omitted += 1;
  }
  return omitted > 0
    ? [
        `${omitted.toLocaleString()} value${omitted === 1 ? "" : "s"} of zero or below not shown on the log axis.`,
      ]
    : [];
}
