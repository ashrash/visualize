import type { ChartSpec } from "../../api/types";
import { makeFormatter, toDate, type ValueFormatter } from "./format";
import { paletteColors } from "./palettes";
import { ciBand } from "./primitives/ciBand";
import { refLineItem } from "./primitives/refLine";
import { type Obj, resolveStyle } from "./series";
import { resolveTheme } from "./theme";
import type { AxisStyle, ChartTheme, OverlayResult, RenderContext } from "./types";

export interface ApplyStyleOptions {
  /** The plot can swap its axes (bar, line, area). Default true. */
  orientable?: boolean;
  /** Tooltip trigger when neither the style nor the plot sets one. */
  defaultTrigger?: "axis" | "item";
  /** Value format when the style sets none (e.g. "percent:0" for a normalized 100% stack). */
  defaultYFormat?: string;
  /** Colors needed when the plot has no series list (pie: one color per slice). */
  colorCount?: number;
}

const TITLE_HEIGHT = 30;
const SUBTITLE_HEIGHT = 18;
const LEGEND_HEIGHT = 30;
const AXIS_TITLE = 22;

const asArray = <T>(value: T | T[] | undefined): T[] =>
  value === undefined ? [] : Array.isArray(value) ? value : [value];

/** Rough width in px of the widest tick label, used to place a vertical axis title. */
function labelWidth(values: string[]): number {
  const longest = values.reduce((n, s) => Math.max(n, s.length), 0);
  return Math.min(140, Math.max(24, longest * 7));
}

function valuesOf(series: Obj[]): number[] {
  const out: number[] = [];
  for (const s of series) {
    for (const item of asArray(s.data as unknown[])) {
      const v = Array.isArray(item) ? item[item.length - 1] : item;
      if (typeof v === "number" && Number.isFinite(v)) out.push(v);
    }
  }
  return out;
}

function bound(value: AxisStyle["min"], type: string): number | undefined {
  if (value === null || value === undefined) return undefined;
  if (type === "time") {
    if (typeof value === "number") return value;
    return toDate(value)?.getTime();
  }
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : undefined;
}

interface AxisBuild {
  base: Obj;
  style: AxisStyle;
  role: "x" | "y";
  /** True when the axis runs vertically on screen. */
  vertical: boolean;
  side: "start" | "end";
  offset: number;
  format?: string | null;
  tickLabels: string[];
  theme: Required<ChartTheme>;
  inverse?: boolean;
  fullExtent?: [number, number];
}

function buildAxis(o: AxisBuild): Obj {
  const { base, style: a, theme } = o;
  let type = (base.type as string | undefined) ?? "value";
  if (o.role === "y" && type !== "category") {
    if (a.scale === "log") type = "log";
    else if (a.scale === "linear") type = "value";
    else if (a.scale === "time") type = "time";
  }
  const continuous = type === "value" || type === "log" || type === "time";
  const title = a.title ?? (base.name as string | undefined);
  const width = labelWidth(o.tickLabels);
  const position = o.vertical
    ? o.side === "start"
      ? "left"
      : "right"
    : o.side === "start"
      ? "bottom"
      : "top";
  const formatter: ValueFormatter | undefined =
    o.format && o.format !== "auto" ? makeFormatter(o.format) : undefined;

  const axis: Obj = {
    ...base,
    type,
    position,
    offset: o.offset,
    inverse: o.inverse ?? a.inverse ?? false,
    name: title,
    nameLocation: "middle",
    nameRotate: o.vertical ? (o.side === "start" ? 90 : -90) : 0,
    nameGap: o.vertical ? width + 12 : 28 + (a.tickRotate ? 14 : 0),
    nameTextStyle: { color: theme.textMuted },
    axisLine: { lineStyle: { color: theme.axis } },
    axisTick: { lineStyle: { color: theme.axis } },
    axisLabel: {
      ...(base.axisLabel as Obj | undefined),
      color: theme.textMuted,
      hideOverlap: true,
      rotate: a.tickRotate ?? 0,
      ...(formatter && continuous ? { formatter: (v: unknown) => formatter(v) } : {}),
    },
    // Gridlines help read values; on a category axis they only add clutter.
    splitLine: {
      show: continuous && a.grid !== false && o.side === "start",
      lineStyle: { color: theme.grid },
    },
  };
  if (type === "log") axis.logBase = a.logBase ?? 10;
  if (continuous) {
    const min = bound(a.min, type) ?? o.fullExtent?.[0];
    const max = bound(a.max, type) ?? o.fullExtent?.[1];
    if (min !== undefined) axis.min = min;
    if (max !== undefined) axis.max = max;
  }
  return axis;
}

/** Overlay (trendline / regression) series for one fitted y series, plus its confidence band. */
function overlaySeries(
  ov: OverlayResult,
  base: Obj | undefined,
  color: string,
  o: {
    categories: unknown[] | undefined;
    nameSuffix: string;
    axisIndex: number;
    horizontal: boolean;
    category: boolean;
  },
): Obj[] {
  const n = o.categories?.length ?? 0;
  const align = (values: (number | null)[]) => {
    const out: (number | null)[] = new Array(n).fill(null);
    ov.x.forEach((x, k) => {
      const i = Math.round(x);
      if (i >= 0 && i < n) out[i] = values[k] ?? null;
    });
    return out;
  };
  const toData = (values: (number | null)[]) =>
    o.category ? align(values) : ov.x.map((x, k) => [x, values[k] ?? null]);
  const axisKey = o.horizontal ? "xAxisIndex" : "yAxisIndex";
  const series: Obj[] = [];
  if (ov.lo && ov.hi) {
    const x = o.category ? (o.categories ?? []) : ov.x;
    const lo = o.category ? align(ov.lo) : ov.lo;
    const hi = o.category ? align(ov.hi) : ov.hi;
    for (const s of ciBand({
      x,
      lo,
      hi,
      color,
      axis: o.category ? "category" : "value",
      yAxisIndex: o.axisIndex,
    })) {
      if (o.horizontal) {
        s.xAxisIndex = o.axisIndex;
        s.yAxisIndex = 0;
      }
      series.push(s);
    }
  }
  const name = `${(base?.name as string | undefined) ?? "Series"} ${o.nameSuffix}`.trim();
  series.push({
    type: "line",
    name,
    data: toData(ov.y),
    symbol: "none",
    smooth: false,
    lineStyle: { type: "dashed", width: 2, color },
    itemStyle: { color },
    z: 4,
    [axisKey]: o.axisIndex,
  });
  return series;
}

const TREND_LABEL: Record<string, string> = {
  linear: "linear trend",
  polynomial: "polynomial trend",
  exponential: "exponential trend",
  logarithmic: "log trend",
  power: "power trend",
  loess: "LOESS trend",
  moving_average: "moving average",
  regression: "regression",
};

/**
 * Apply the generic, plot-independent part of a ChartStyle to an option a plot has built in
 * logical (vertical) orientation: titles, legend, palette, fonts, axes and scales (with multiple
 * y axes), orientation swap, per-series overrides, labels, tooltips, reference lines,
 * annotations and server-computed trendlines. Plots build series only and call this last, so
 * MVP §4 is implemented once.
 *
 * Conventions for the input option:
 * - `xAxis` carries the x channel (type decided by the plot), `yAxis` the first value axis.
 * - Series may carry `yIndex` (index into the y channel) and `group` (set for color-group
 *   series); both are consumed here and stripped from the output.
 * - Category labels are already formatted; value-axis formatting is applied here.
 * - `style.xAxis` styles the axis that carries the x channel, `style.yAxes` the value axes,
 *   whichever way the chart is oriented.
 */
export function applyStyle(
  option: Obj,
  spec: ChartSpec,
  theme: ChartTheme,
  ctx?: RenderContext,
  opts: ApplyStyleOptions = {},
): Obj {
  const style = resolveStyle(spec);
  const t = resolveTheme(theme);
  const rawSeries = asArray(option.series as Obj[] | undefined);
  const cartesian = option.xAxis !== undefined && option.yAxis !== undefined;
  const horizontal = cartesian && (opts.orientable ?? true) && style.orientation === "horizontal";
  const out: Obj = { ...option };

  // Palette and base text.
  const colorCount = Math.max(rawSeries.length, opts.colorCount ?? 0, 8);
  const colors = paletteColors(style.palette, t, colorCount);
  out.color = colors;
  out.backgroundColor = t.background;
  out.useUTC = true;
  out.aria = { enabled: true, ...(option.aria as Obj | undefined) };
  out.animationDuration = 300;
  out.textStyle = {
    color: t.text,
    fontFamily: style.fontFamily ?? t.fontFamily,
    ...(style.fontSize ? { fontSize: style.fontSize } : {}),
  };

  // Title and legend reserve space; the grid is computed from what is shown.
  let top = 12;
  let bottom = 8;
  let left = 8;
  let right = 20;
  const hasTitle = Boolean(style.title);
  const hasSubtitle = Boolean(style.subtitle);
  if (hasTitle || hasSubtitle) {
    out.title = {
      text: style.title ?? "",
      subtext: style.subtitle ?? "",
      left: 8,
      top: 4,
      textStyle: { color: t.text, fontSize: 15, fontWeight: 600 },
      subtextStyle: { color: t.textMuted, fontSize: 12 },
    };
    top += (hasTitle ? TITLE_HEIGHT : 8) + (hasSubtitle ? SUBTITLE_HEIGHT : 0);
  } else {
    out.title = undefined;
  }

  const overlays = ctx?.overlays ?? [];
  // Trendlines are named series too, so they get a legend entry that says what the dashed line is.
  const named =
    rawSeries.filter((s) => typeof s.name === "string" && s.name !== "").length + overlays.length;
  const wantsLegend = option.legend !== undefined || named > 1;
  const dataZoom = asArray(option.dataZoom as Obj | Obj[] | undefined);
  const hasSlider = dataZoom.some((z) => z.type === "slider");
  if (hasSlider) bottom += 34;
  if (style.legend.show && wantsLegend) {
    const position = style.legend.position;
    const legendBase: Obj = {
      ...(option.legend as Obj | undefined),
      type: "scroll",
      show: true,
      textStyle: { color: t.text },
      pageIconColor: t.textMuted,
      pageTextStyle: { color: t.textMuted },
    };
    if (position === "top") {
      out.legend = { ...legendBase, top: top - 8, left: 8, right: 8 };
      top += LEGEND_HEIGHT;
    } else if (position === "bottom") {
      out.legend = { ...legendBase, bottom: hasSlider ? 40 : 4, left: "center" };
      bottom += LEGEND_HEIGHT + (hasSlider ? 0 : 0);
    } else {
      const longest = rawSeries.reduce(
        (n, s) => Math.max(n, typeof s.name === "string" ? s.name.length : 0),
        0,
      );
      const width = Math.min(200, longest * 7 + 36);
      out.legend = { ...legendBase, orient: "vertical", top: "middle", [position]: 8 };
      if (position === "left") left += width;
      else right += width;
    }
  } else {
    out.legend = undefined;
  }

  // Axes.
  const seriesStyles = new Map(style.series.map((s) => [s.index, s]));
  const axisOf = (s: Obj, i: number) =>
    seriesStyles.get((s.yIndex as number | undefined) ?? i)?.yAxis ?? 0;
  let yAxisCount = 1;
  if (cartesian) {
    yAxisCount = Math.max(
      asArray(option.yAxis as Obj | Obj[]).length,
      style.yAxes.length,
      ...rawSeries.map((s, i) => axisOf(s, i) + 1),
    );
  }

  const baseX = (cartesian ? option.xAxis : undefined) as Obj | undefined;
  const baseY = (cartesian ? asArray(option.yAxis as Obj | Obj[])[0] : undefined) as
    | Obj
    | undefined;
  const xType = (baseX?.type as string | undefined) ?? "category";
  const categories = baseX?.data as unknown[] | undefined;
  const extent = ctx?.viewport?.fullExtent;

  if (cartesian && baseX && baseY) {
    const yFormatOf = (i: number) =>
      style.yAxes[i]?.format ?? (i === 0 ? (style.numberFormat ?? opts.defaultYFormat) : undefined);
    const fmtAuto = makeFormatter("auto");
    const sidesUsed = { start: 0, end: 0 };
    const yAxes: Obj[] = [];
    for (let i = 0; i < yAxisCount; i++) {
      const a = style.yAxes[i] ?? ({ grid: true } as AxisStyle);
      const side = a.position ?? (i === 0 ? "start" : "end");
      const onAxis = rawSeries.filter((s, k) => axisOf(s, k) === i);
      const fmt = makeFormatter(yFormatOf(i) ?? "auto");
      const vals = valuesOf(onAxis);
      const ticks = vals.length
        ? [fmt(Math.max(...vals)), fmt(Math.min(...vals))]
        : [fmtAuto(0)];
      yAxes.push(
        buildAxis({
          base: i === 0 ? baseY : { type: "value" },
          style: a,
          role: "y",
          vertical: !horizontal,
          side,
          offset: sidesUsed[side] * (horizontal ? 36 : 64),
          format: yFormatOf(i) ?? undefined,
          tickLabels: ticks,
          theme: t,
        }),
      );
      sidesUsed[side] += 1;
      // Title space beyond the tick labels.
      if (yAxes[i]?.name) {
        if (horizontal) {
          if (side === "start") bottom += AXIS_TITLE;
          else top += AXIS_TITLE;
        } else if (side === "start") left += AXIS_TITLE;
        else right += AXIS_TITLE + (sidesUsed.end - 1) * 64;
      }
    }

    const xLabels = (categories ?? []).map((c) => String(c));
    const xAxis = buildAxis({
      base: baseX,
      style: style.xAxis,
      role: "x",
      vertical: horizontal,
      side: style.xAxis.position ?? "start",
      offset: 0,
      format: xType === "category" ? undefined : style.xAxis.format,
      tickLabels: xLabels,
      theme: t,
      // Horizontal bars read top to bottom: first category at the top.
      inverse: horizontal && xType === "category" ? !style.xAxis.inverse : style.xAxis.inverse ?? undefined,
      fullExtent: extent,
    });
    if (xAxis.name) {
      if (horizontal) left += AXIS_TITLE;
      else bottom += AXIS_TITLE;
    }
    // A secondary value axis on the far side adds width/height for its labels.
    if (horizontal) {
      out.xAxis = yAxes;
      out.yAxis = xAxis;
    } else {
      out.xAxis = xAxis;
      out.yAxis = yAxes.length === 1 ? yAxes[0] : yAxes;
    }
  }

  // Series.
  const valueFormat = (axisIndex: number): string | null | undefined =>
    style.tooltip.format ?? style.yAxes[axisIndex]?.format ?? style.numberFormat ?? opts.defaultYFormat;
  const labelPosition = (): string | undefined => {
    switch (style.labelPosition) {
      case "inside":
        return "inside";
      case "outside":
        return horizontal ? "right" : "top";
      case "top":
        return "top";
      case "end":
        return horizontal ? "insideRight" : "insideTop";
      default:
        return undefined;
    }
  };
  const axisKey = horizontal ? "xAxisIndex" : "yAxisIndex";
  let seriesOut: Obj[] = rawSeries.map((raw, i) => {
    const { yIndex, group, ...s } = raw as Obj & { yIndex?: number; group?: string };
    const ss = seriesStyles.get(yIndex ?? i);
    let type = (s.type as string | undefined) ?? "line";
    if (ss?.kind) type = ss.kind === "area" ? "line" : ss.kind;
    const result: Obj = { ...s, type };
    if (cartesian) {
      const axis = ss?.yAxis ?? 0;
      if (axis > 0 || s[axisKey] !== undefined) result[axisKey] = axis;
    }
    const color = ss?.color && group === undefined ? ss.color : undefined;
    if (color) {
      result.color = color;
      result.itemStyle = { ...(s.itemStyle as Obj | undefined), color };
    }
    if (type === "line") {
      const markers = ss?.marker ?? style.markers;
      if (markers === true) {
        result.symbol = "circle";
        result.symbolSize = 6;
        result.showSymbol = true;
      } else if (markers === false) {
        result.symbol = "none";
      }
      const width = ss?.lineWidth ?? style.lineWidth;
      const dash = ss?.lineStyle ?? style.lineStyle;
      if (width || dash || color) {
        result.lineStyle = {
          ...(s.lineStyle as Obj | undefined),
          ...(width ? { width } : {}),
          ...(dash ? { type: dash } : {}),
          ...(color ? { color } : {}),
        };
      }
      if (style.smooth !== null && style.smooth !== undefined) result.smooth = style.smooth;
      if (ss?.kind === "area" || s.areaStyle) {
        result.areaStyle = {
          ...(s.areaStyle as Obj | undefined),
          opacity: style.areaOpacity ?? (s.areaStyle as Obj | undefined)?.opacity ?? 0.25,
        };
      }
    }
    if (style.showLabels && type !== "pie") {
      const fmt = makeFormatter(valueFormat(ss?.yAxis ?? 0) ?? "auto");
      result.label = {
        show: true,
        color: t.textMuted,
        position: labelPosition(),
        formatter: (p: { value: unknown }) =>
          fmt(Array.isArray(p.value) ? p.value[p.value.length - 1] : p.value),
      };
    }
    const tooltipFormat = valueFormat(ss?.yAxis ?? 0);
    if (tooltipFormat && tooltipFormat !== "auto" && type !== "custom") {
      const fmt = makeFormatter(tooltipFormat);
      result.tooltip = {
        ...(s.tooltip as Obj | undefined),
        valueFormatter: (v: unknown) =>
          Array.isArray(v) ? v.map((x) => fmt(x)).join(", ") : fmt(v),
      };
    }
    return result;
  });

  // Server-computed overlays (trendlines, regression), drawn in the base series' color.
  for (const ov of overlays) {
    const idx = rawSeries.findIndex((s) => (s.yIndex as number | undefined) === ov.series);
    const base = rawSeries[idx];
    const color = (seriesOut[idx]?.color as string | undefined) ?? colors[Math.max(idx, 0) % colors.length] ?? t.text;
    const spec_ = style.trendlines.find((tl) => tl.series === ov.series);
    const bits = [TREND_LABEL[ov.kind] ?? "trend"];
    if (spec_?.showR2 && ov.r2 !== null) bits.push(`R² ${ov.r2.toFixed(3)}`);
    if (spec_?.showEquation && ov.equation) bits.push(ov.equation);
    seriesOut.push(
      ...overlaySeries(ov, base, color, {
        categories,
        nameSuffix: bits.length > 1 ? `${bits[0]} (${bits.slice(1).join(", ")})` : (bits[0] ?? ""),
        axisIndex: seriesStyles.get(ov.series)?.yAxis ?? 0,
        horizontal,
        category: xType === "category",
      }),
    );
  }

  // Reference lines and annotations ride on the first real series.
  if (cartesian && (style.refLines.length > 0 || style.annotations.length > 0)) {
    let host = seriesOut.findIndex((s) => s.silent !== true);
    if (host < 0) {
      seriesOut = [{ type: "line", data: [], silent: true, tooltip: { show: false } }, ...seriesOut];
      host = 0;
    }
    const target = { ...seriesOut[host] } as Obj;
    const time = xType === "time";
    const lines = style.refLines.map((r) =>
      refLineItem({
        axis: r.axis,
        value: r.value,
        label: r.label ?? undefined,
        style: r.lineStyle,
        color: t.textMuted,
        time: time && r.axis === "x",
        swapped: horizontal,
      }),
    );
    if (lines.length > 0) {
      target.markLine = {
        ...(target.markLine as Obj | undefined),
        silent: true,
        symbol: "none",
        data: [...asArray((target.markLine as Obj | undefined)?.data as Obj[]), ...lines],
      };
    }
    const toCoord = (v: number | string | null | undefined, axis: "x" | "y") =>
      axis === "x" && time && typeof v === "string" ? (toDate(v)?.getTime() ?? v) : v;
    const points: Obj[] = [];
    const areas: Obj[][] = [];
    for (const an of style.annotations) {
      const x = toCoord(an.x, "x");
      const y = an.y;
      if (an.kind === "area") {
        const x2 = toCoord(an.x2, "x");
        const corner = (cx: unknown, cy: unknown): Obj => {
          const point: Obj = {};
          if (cx !== null && cx !== undefined) point[horizontal ? "yAxis" : "xAxis"] = cx;
          if (cy !== null && cy !== undefined) point[horizontal ? "xAxis" : "yAxis"] = cy;
          return point;
        };
        const first = corner(x, y);
        first.name = an.text ?? undefined;
        areas.push([first, corner(x2, an.y2)]);
      } else if (x !== null && x !== undefined && y !== null && y !== undefined) {
        const coord = horizontal ? [y, x] : [x, y];
        points.push(
          an.kind === "text"
            ? {
                coord,
                symbolSize: 0,
                label: { show: true, formatter: an.text ?? "", color: t.text },
              }
            : {
                coord,
                name: an.text ?? undefined,
                value: an.text ?? undefined,
                label: { show: Boolean(an.text), formatter: an.text ?? "", color: t.text },
              },
        );
      }
    }
    if (points.length > 0) target.markPoint = { data: points, silent: true };
    if (areas.length > 0) {
      target.markArea = {
        silent: true,
        itemStyle: { color: t.grid, opacity: 0.35 },
        label: { color: t.textMuted },
        data: areas,
      };
    }
    seriesOut[host] = target;
  }
  out.series = seriesOut;

  // Tooltip.
  const optionTooltip = option.tooltip as Obj | undefined;
  out.tooltip = {
    ...optionTooltip,
    show: style.tooltip.show,
    trigger:
      style.tooltip.trigger ?? optionTooltip?.trigger ?? opts.defaultTrigger ?? (cartesian ? "axis" : "item"),
    confine: true,
    backgroundColor: t.tooltipBg,
    borderColor: t.border,
    textStyle: { color: t.tooltipText },
  };

  out.grid = cartesian
    ? { left, right, top, bottom, containLabel: true }
    : option.grid;
  return out;
}
