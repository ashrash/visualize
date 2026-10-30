import type { EChartsCoreOption } from "echarts/core";
import type { ColumnarResult } from "../../api/arrow";
import type { components } from "../../api/schema";
import type {
  AggFn,
  ChartDataMeta,
  ChartSpec,
  ChartStyle,
  Column,
  ColumnSemantic,
  Encoding,
  PlotFamily,
  PlotType,
} from "../../api/types";

type Schemas = components["schemas"];

export type StatResult = Schemas["StatResult"];

export type AxisStyle = Schemas["AxisStyle"];
export type SeriesStyle = Schemas["SeriesStyle"];
export type Trendline = Schemas["Trendline"];
export type TrendlineKind = Schemas["TrendlineKind"];
export type ErrorBarsStyle = Schemas["ErrorBars"];
export type Interactions = Schemas["Interactions"];
export type Annotation = Schemas["Annotation"];
export type RefLine = Schemas["RefLine"];

export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

export type DeepPartial<T> = T extends (infer U)[]
  ? U[]
  : T extends object
    ? { [K in keyof T]?: DeepPartial<T[K]> }
    : T;

/** A channel a plot accepts (x, y, color, size, …). The config panel is generated from these. */
export interface ChannelSchema {
  name: string;
  label: string;
  required: boolean;
  multiple: boolean;
  accepts: ColumnSemantic[];
  /** Whether the channel takes an aggregate (sum, mean, …). */
  aggregate: boolean;
  /** Allowed aggregates; default is all of them. */
  aggs?: AggFn[];
  /** Show the time-unit control (bucket a temporal column by day, month, …). */
  timeUnit?: boolean;
  /** Show the sort control. */
  sort?: boolean;
  hint?: string;
}

/**
 * When a param control is shown. `param` names another param (compare its value with `equals`)
 * or a channel (`equals: true` = a field is bound; `atLeast: n` = at least n encodings).
 */
export type ShowIf = { param: string; equals: JsonValue } | { param: string; atLeast: number };

/**
 * A plot-specific setting, stored in `spec.params[name]`. The config panel renders one control
 * per entry.
 *
 * `showIf`: one condition, or an array meaning "any of these".
 *
 * `affectsData` is REQUIRED (no default, so nobody forgets to decide): `true` when the server
 * needs the param to build the chart data (bin count, bandwidth, lags, ...), `false` for
 * cosmetic or client-side settings. Only `true` params are part of the chart-data query key:
 * changing one re-queries, changing a `false` one re-renders from the cached data, so a slider
 * that drives a purely visual setting does not hit the server on every step. `validateEntry`
 * fails when it is missing. A param wrongly marked `false` shows up as "my control does
 * nothing".
 */
export type ParamSchema = {
  name: string;
  label: string;
  help?: string;
  showIf?: ShowIf | ShowIf[];
  affectsData: boolean;
} & (
  | { type: "number"; min?: number; max?: number; step?: number; default: number }
  | { type: "int"; min?: number; max?: number; default: number }
  | { type: "boolean"; default: boolean }
  | { type: "select"; options: { value: string; label: string }[]; default: string }
  | { type: "text"; default: string }
  | { type: "color"; default: string }
  | { type: "column"; accepts: ColumnSemantic[] }
  | { type: "range"; min: number; max: number; step?: number; default: [number, number] }
);

/** The generic (plot-independent) sections of the Style tab a plot supports. */
export type StyleSection =
  | "title"
  | "legend"
  | "axes"
  | "multiAxis"
  | "labels"
  | "annotations"
  | "refLines"
  | "markers"
  | "lines"
  | "stack"
  | "orientation"
  | "trendline"
  | "errorBars"
  | "tooltip"
  | "numberFormat"
  | "palette"
  | "seriesStyle";

/**
 * Chart data as columns (`x`, `y0..`, ...). Plots that compute a stat transform on the server
 * get an empty table plus the structured `stat` result (the response content type decides).
 */
export type ChartData = ColumnarResult<ChartDataMeta> & { stat?: StatResult };

/** Server-computed overlay (trendline, regression, confidence band) for one y series. */
export interface OverlayResult {
  kind: TrendlineKind | "regression";
  /** Index into the y channel. */
  series: number;
  x: number[];
  y: number[];
  lo: number[] | null;
  hi: number[] | null;
  equation: string | null;
  r2: number | null;
  n: number;
  sampled: boolean;
  warnings: string[];
}

/** Zoom state passed to renderers (detail-on-demand). */
export interface ViewportState {
  /** x extent of the unzoomed data; fixes the axis while zoomed re-queries return less. */
  fullExtent?: [number, number];
  /** Visible x range (numeric / epoch ms), or null/undefined when not zoomed. */
  xRange?: [number, number] | null;
}

export interface RenderContext {
  overlays?: OverlayResult[];
  locale?: string;
  viewport?: ViewportState;
}

/** Resolved design tokens for charts (read from CSS variables). Only the first five are required. */
export interface ChartTheme {
  palette: string[];
  text: string;
  textMuted: string;
  border: string;
  fontFamily: string;
  mode?: "light" | "dark";
  background?: string;
  grid?: string;
  axis?: string;
  tooltipBg?: string;
  tooltipText?: string;
  /** Sequential ramp (low to high), e.g. viridis. */
  sequential?: string[];
  /** Diverging ramp (negative to positive). */
  diverging?: string[];
}

export interface PlotDefaults {
  encodings?: Record<string, Encoding[]>;
  params?: Record<string, JsonValue>;
  style?: DeepPartial<ChartStyle>;
  interactions?: Partial<Interactions>;
}

export interface PlotEntry {
  key: PlotType;
  label: string;
  family: PlotFamily;
  description?: string;
  encodingsSchema: ChannelSchema[];
  paramsSchema: ParamSchema[];
  /** Which generic Style-tab sections apply to this plot. */
  styleSections: StyleSection[];
  defaults: PlotDefaults;
  /** Pure: same inputs, same option. */
  render(
    spec: ChartSpec,
    data: ChartData,
    theme: ChartTheme,
    ctx?: RenderContext,
  ): EChartsCoreOption;
  /** Lazy assets (GeoJSON, custom series) loaded before the first render. */
  prepare?(spec: ChartSpec): Promise<void>;
  /**
   * Notices for the chart footer about data the plot could not draw ("3 negative values
   * omitted"). Pure, like render. Return [] when nothing was dropped.
   */
  warnings?(spec: ChartSpec, data: ChartData): string[];
  /** 0–1 suitability for the given columns (chart recommendations). */
  recommend?(columns: Column[]): number;
}

/** Type helper for plot modules: `export const histogram = definePlot({...})`. */
export const definePlot = (entry: PlotEntry): PlotEntry => entry;
