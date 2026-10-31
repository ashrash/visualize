import type { ChartSpec } from "../../api/types";
import { makeFormatter, toDate, type ValueFormatter } from "./format";
import { maxOf, minOf } from "./math";
import { paletteColors } from "./palettes";
import { ciBand } from "./primitives/ciBand";
import { refLineItem } from "./primitives/refLine";
import { type Obj, resolveStyle } from "./series";
import { resolveTheme, svgSafeFont } from "./theme";
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
  /**
   * `false`: do not manage axes (no scale/format/title/orientation/multi-axis handling, no
   * reference lines or trendlines); the plot's `xAxis`/`yAxis`/`grid` pass through with only
   * theme colors filled in. Detected automatically when `grid` or `xAxis` is an array.
   */
  axes?: false;
  /** `false`: keep the plot's own `grid`; title, legend and palette are still applied. */
  layout?: false;
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

/**
 * True only for a real multi-grid layout: more than one grid, or axes attached to more than one
 * distinct `gridIndex`. A single grid given as `grid: [{...}]`, or one grid with x axes on both
 * the top and the bottom, is still a single-grid chart and stays fully managed.
 */
export function hasSeveralGrids(option: Obj): boolean {
  if (Array.isArray(option.grid) && option.grid.length > 1) return true;
  const indices = new Set<number>();
  for (const axis of [
    ...asArray(option.xAxis as Obj | Obj[]),
    ...asArray(option.yAxis as Obj | Obj[]),
  ]) {
    indices.add(typeof axis.gridIndex === "number" ? axis.gridIndex : 0);
  }
  return indices.size > 1;
}

/**
 * Remove points whose x is zero or negative (log x axes). `xDims` are the item positions that
 * hold x (default: the first, as in [x, y]). Items that are not arrays pass through.
 */
export function dropNonPositiveX(data: unknown, xDims: number[] = [0]): unknown {
  if (!Array.isArray(data)) return data;
  return data.filter(
    (item) =>
      !(
        Array.isArray(item) &&
        xDims.some((d) => typeof item[d] === "number" && (item[d] as number) <= 0)
      ),
  );
}

/** The data positions a series reads x from: its `encode.x`, else the first. */
function xDimsOf(series: Obj): number[] {
  const encoded = (series.encode as Obj | undefined)?.x;
  const dims = asArray(encoded as unknown as number | number[] | undefined).filter(
    (d): d is number => typeof d === "number",
  );
  return dims.length > 0 ? dims : [0];
}

/** Round to the precision of the tick step, so 0.30000000000000004 prints as 0.3. */
function tidy(value: number, step: number): number {
  const decimals = Math.max(0, 1 - Math.floor(Math.log10(step)));
  return Number(value.toFixed(Math.min(decimals, 10)));
}

/** ECharts' "nice" step: a 1, 2, 5 or 10 times a power of ten. */
function niceStep(span: number, count: number): number {
  const raw = span / count;
  const exp = 10 ** Math.floor(Math.log10(raw));
  const f = raw / exp;
  const nf = f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10;
  return nf * exp;
}

/**
 * The tick labels a value or log axis draws for a data extent, so the space an axis title needs
 * is sized from what is on screen rather than from the raw data maximum (1607.42 is drawn as
 * "1500", not "1,607.42"). A linear extent always includes 0 like ECharts' value axis does.
 */
export function niceTickLabels(
  extent: [number, number],
  o: {
    log?: boolean;
    logBase?: number;
    format?: (v: unknown) => string;
    splitNumber?: number;
  } = {},
): string[] {
  const show = (v: number) => (o.format ? o.format(v) : String(v));
  let [lo, hi] = extent;
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [show(0)];
  if (o.log) {
    const base = o.logBase ?? 10;
    lo = Math.max(lo, Number.MIN_VALUE);
    hi = Math.max(hi, lo);
    const first = Math.floor(Math.log(lo) / Math.log(base));
    const last = Math.max(first, Math.ceil(Math.log(hi) / Math.log(base)));
    const labels: string[] = [];
    for (let e = first; e <= last && labels.length < 20; e++) labels.push(show(base ** e));
    return labels;
  }
  lo = Math.min(lo, 0);
  hi = Math.max(hi, 0);
  if (hi === lo) hi = lo + 1;
  const step = niceStep(hi - lo, o.splitNumber ?? 5);
  const labels: string[] = [];
  const start = Math.floor(lo / step);
  const end = Math.ceil(hi / step);
  for (let k = start; k <= end && labels.length < 20; k++) labels.push(show(tidy(k * step, step)));
  return labels;
}

/** Replace non-positive values with null (log axes). Handles plain values and [x, y] pairs. */
export function dropNonPositive(data: unknown): unknown {
  if (!Array.isArray(data)) return data;
  return data.map((item) => {
    if (Array.isArray(item)) {
      const y = item[item.length - 1];
      return typeof y === "number" && y <= 0 ? [...item.slice(0, -1), null] : item;
    }
    return typeof item === "number" && item <= 0 ? null : item;
  });
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

/** Theme colors for an axis the plot laid out itself; the plot's own settings win. */
function themeAxis(a: Obj, t: Required<ChartTheme>): Obj {
  return {
    nameTextStyle: { color: t.textMuted },
    ...a,
    axisLine: { lineStyle: { color: t.axis }, ...(a.axisLine as Obj | undefined) },
    axisTick: { lineStyle: { color: t.axis }, ...(a.axisTick as Obj | undefined) },
    axisLabel: { color: t.textMuted, hideOverlap: true, ...(a.axisLabel as Obj | undefined) },
    splitLine: { lineStyle: { color: t.grid }, ...(a.splitLine as Obj | undefined) },
  };
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
  locale?: string;
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
    o.format && o.format !== "auto" ? makeFormatter(o.format, o.locale) : undefined;

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
    id: number;
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
      id: o.id,
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
  // Several grids (candlestick + volume, pair plots, marginals) are laid out by the plot: axes
  // and grids are passed through with only the theme applied.
  const multiGrid = hasSeveralGrids(option);
  const managed = cartesian && opts.axes !== false && !multiGrid;
  const horizontal = managed && (opts.orientable ?? true) && style.orientation === "horizontal";
  const locale = ctx?.locale;
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
    fontFamily: svgSafeFont(style.fontFamily ?? t.fontFamily),
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
  // A vertical visualMap (continuous legend) sits at the right edge: keep the plot clear of it.
  const visualMaps = asArray(option.visualMap as Obj | Obj[] | undefined);
  const visualMapSpace = visualMaps.some((v) => v.orient !== "horizontal") ? 90 : 0;
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
  if (managed) {
    yAxisCount = Math.max(
      asArray(option.yAxis as Obj | Obj[]).length,
      style.yAxes.length,
      ...rawSeries.map((s, i) => axisOf(s, i) + 1),
    );
  }

  // One grid can still have several axes (x on the top and bottom): the first is the managed
  // one, the rest are passed through with theme colors.
  const xBases = managed ? asArray(option.xAxis as Obj | Obj[]) : [];
  const yBases = managed ? asArray(option.yAxis as Obj | Obj[]) : [];
  const baseX = xBases[0];
  const baseY = yBases[0];
  const xType = (baseX?.type as string | undefined) ?? "category";
  const categories = baseX?.data as unknown[] | undefined;
  const extent = ctx?.viewport?.fullExtent;

  const yAxisTypes: string[] = [];
  if (managed && baseX && baseY) {
    const yFormatOf = (i: number) =>
      style.yAxes[i]?.format ?? (i === 0 ? (style.numberFormat ?? opts.defaultYFormat) : undefined);
    const fmtAuto = makeFormatter("auto");
    const sidesUsed = { start: 0, end: 0 };
    const yAxes: Obj[] = [];
    for (let i = 0; i < yAxisCount; i++) {
      const a = style.yAxes[i] ?? ({ grid: true } as AxisStyle);
      const side = a.position ?? (i === 0 ? "start" : "end");
      const onAxis = rawSeries.filter((s, k) => axisOf(s, k) === i);
      const fmt = makeFormatter(yFormatOf(i) ?? "auto", locale);
      const vals = valuesOf(onAxis);
      // The widest label is a tick the axis draws (a rounded value), not the data maximum.
      const customFormat = yFormatOf(i) && yFormatOf(i) !== "auto";
      const log = a.scale === "log";
      const lo = bound(a.min, "value") ?? minOf(vals);
      const hi = bound(a.max, "value") ?? maxOf(vals);
      const ticks = vals.length
        ? niceTickLabels([lo, hi], {
            log,
            logBase: a.logBase ?? 10,
            format: customFormat ? (v) => fmt(v) : undefined,
          })
        : [fmtAuto(0)];
      yAxes.push(
        buildAxis({
          base: yBases[i] ?? { type: "value" },
          style: a,
          role: "y",
          vertical: !horizontal,
          side,
          offset: sidesUsed[side] * (horizontal ? 36 : 64),
          format: yFormatOf(i) ?? undefined,
          tickLabels: ticks,
          theme: t,
          locale,
        }),
      );
      yAxisTypes.push(yAxes[i]?.type as string);
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
      inverse:
        horizontal && xType === "category"
          ? !style.xAxis.inverse
          : (style.xAxis.inverse ?? undefined),
      fullExtent: extent,
      locale,
    });
    if (xAxis.name) {
      if (horizontal) left += AXIS_TITLE;
      else bottom += AXIS_TITLE;
    }
    // A secondary value axis on the far side adds width/height for its labels.
    const extraX = xBases.slice(1).map((a) => themeAxis(a, t));
    const xOut = extraX.length > 0 ? [xAxis, ...extraX] : xAxis;
    if (horizontal) {
      out.xAxis = yAxes;
      out.yAxis = xOut;
    } else {
      out.xAxis = xOut;
      out.yAxis = yAxes.length === 1 ? yAxes[0] : yAxes;
    }
  }

  // Brush: a band along the x channel's axis (categories run down the y axis when horizontal).
  // EChart switches the brush tool on; the chart reports what the band covers.
  if (managed && spec.interactions?.brush) {
    out.brush = {
      [horizontal ? "yAxisIndex" : "xAxisIndex"]: 0,
      brushType: horizontal ? "lineY" : "lineX",
      brushMode: "multiple",
      throttleType: "debounce",
      throttleDelay: 150,
      brushStyle: { color: `${t.textMuted}33`, borderColor: t.textMuted, borderWidth: 1 },
      toolbox: [],
    };
  }

  // Series.
  const valueFormat = (axisIndex: number): string | null | undefined =>
    style.tooltip.format ??
    style.yAxes[axisIndex]?.format ??
    style.numberFormat ??
    opts.defaultYFormat;
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
    // A log axis cannot draw zero or negative values; drop them rather than draw garbage.
    if (managed && yAxisTypes[ss?.yAxis ?? 0] === "log") result.data = dropNonPositive(s.data);
    // Likewise a log x axis cannot place x <= 0: those points are left out.
    // Custom series place their own marks and may keep a category index first: not touched.
    if (managed && !horizontal && xType === "log" && type !== "custom") {
      result.data = dropNonPositiveX(result.data, xDimsOf(s));
    }
    if (managed) {
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
      const fmt = makeFormatter(valueFormat(ss?.yAxis ?? 0) ?? "auto", locale);
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
      const fmt = makeFormatter(tooltipFormat, locale);
      result.tooltip = {
        ...(s.tooltip as Obj | undefined),
        valueFormatter: (v: unknown) =>
          Array.isArray(v) ? v.map((x) => fmt(x)).join(", ") : fmt(v),
      };
    }
    return result;
  });

  // Server-computed overlays (trendlines, regression), drawn in the base series' color.
  for (const [overlayIndex, ov] of overlays.entries()) {
    const idx = rawSeries.findIndex((s) => (s.yIndex as number | undefined) === ov.series);
    const base = rawSeries[idx];
    const color =
      (seriesOut[idx]?.color as string | undefined) ??
      colors[Math.max(idx, 0) % colors.length] ??
      t.text;
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
        id: overlayIndex,
      }),
    );
  }

  // Reference lines and annotations ride on the first real series.
  if (managed && (style.refLines.length > 0 || style.annotations.length > 0)) {
    let host = seriesOut.findIndex((s) => s.silent !== true);
    if (host < 0) {
      seriesOut = [
        { type: "line", data: [], silent: true, tooltip: { show: false } },
        ...seriesOut,
      ];
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
      style.tooltip.trigger ??
      optionTooltip?.trigger ??
      opts.defaultTrigger ??
      (cartesian ? "axis" : "item"),
    confine: true,
    backgroundColor: t.tooltipBg,
    borderColor: t.border,
    textStyle: { color: t.tooltipText },
  };

  if (managed && opts.layout !== false) {
    out.grid = { left, right: right + visualMapSpace, top, bottom, containLabel: true };
  } else {
    out.grid = option.grid;
  }
  if (cartesian && !managed) {
    out.xAxis = Array.isArray(option.xAxis)
      ? option.xAxis.map((a: Obj) => themeAxis(a, t))
      : themeAxis(option.xAxis as Obj, t);
    out.yAxis = Array.isArray(option.yAxis)
      ? option.yAxis.map((a: Obj) => themeAxis(a, t))
      : themeAxis(option.yAxis as Obj, t);
  }
  return out;
}
