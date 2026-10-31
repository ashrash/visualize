import type { DrillValue } from "./drill";
import { categoryLabel } from "./series";
import type { ChartData } from "./types";

/**
 * Raw interaction signals a chart hands up to whoever embeds it (the dashboard's cross-filter
 * bus, for one). They describe what the user did in chart terms; turning them into `Predicate`s
 * and filtering other charts is the embedder's job.
 */

type Obj = Record<string, unknown>;

/** A click on a series mark (bar, point, slice). */
export interface PointHit {
  seriesIndex: number;
  seriesName: string;
  /** The category name (or point name) as drawn. */
  name: string;
  dataIndex: number;
  value: unknown;
  color?: string;
  /** Held while clicking: the embedder may treat it as "add to the selection". */
  shiftKey: boolean;
}

export function pointHitFrom(params: unknown): PointHit | null {
  const p = (params ?? {}) as Obj;
  if (p.componentType !== "series") return null;
  const native = (p.event as { event?: { shiftKey?: boolean } } | undefined)?.event;
  return {
    seriesIndex: typeof p.seriesIndex === "number" ? p.seriesIndex : 0,
    seriesName: typeof p.seriesName === "string" ? p.seriesName : "",
    name: p.name === undefined || p.name === null ? "" : String(p.name),
    dataIndex: typeof p.dataIndex === "number" ? p.dataIndex : -1,
    value: p.value,
    color: typeof p.color === "string" ? p.color : undefined,
    shiftKey: native?.shiftKey === true,
  };
}

/** What a brush covers on the x channel's axis. */
export interface BrushPayload {
  /** Which screen axis the brush spans: "y" for horizontal bar charts, where categories run down. */
  axis: "x" | "y";
  kind: "category" | "value" | "time" | "log";
  /** Value, time (epoch ms) and log axes: one [low, high] per brushed area. */
  ranges: [number, number][];
  /** Category axes: the labels covered by any area, in axis order. */
  categories: string[];
  /** True when the brush was removed (nothing selected any more). */
  cleared: boolean;
}

interface BrushArea {
  brushType?: string;
  coordRange?: unknown;
}

const axisList = (option: Obj, key: "xAxis" | "yAxis"): Obj[] => {
  const value = option[key];
  return Array.isArray(value) ? (value as Obj[]) : value ? [value as Obj] : [];
};

/**
 * Interpret an ECharts `brushEnd` event (its `areas`) against the chart's option. Only the
 * one-axis brushes the chart enables (`lineX`, `lineY`) become ranges; `null` for other shapes.
 */
export function brushPayloadFrom(event: unknown, option: Obj): BrushPayload | null {
  const areas = ((event as { areas?: BrushArea[] } | undefined)?.areas ?? []).filter(
    (a) => a.brushType === "lineX" || a.brushType === "lineY" || a.brushType === undefined,
  );
  const rawCount = (event as { areas?: unknown[] } | undefined)?.areas?.length ?? 0;
  if (rawCount > 0 && areas.length === 0) return null;
  const vertical = areas[0]?.brushType === "lineY";
  const axis = vertical ? "y" : "x";
  const def = axisList(option, vertical ? "yAxis" : "xAxis")[0];
  const type = (def?.type as string | undefined) ?? "value";
  const kind = type === "category" || type === "time" || type === "log" ? type : "value";
  const payload: BrushPayload = { axis, kind, ranges: [], categories: [], cleared: rawCount === 0 };
  const labels = ((def?.data as unknown[] | undefined) ?? []).map((c) =>
    typeof c === "object" && c !== null ? String((c as { value?: unknown }).value) : String(c),
  );
  const picked = new Set<number>();
  for (const area of areas) {
    const range = area.coordRange;
    if (!Array.isArray(range) || typeof range[0] !== "number" || typeof range[1] !== "number") {
      continue;
    }
    const [a, b] = range as [number, number];
    const lo = Math.min(a, b);
    const hi = Math.max(a, b);
    if (kind === "category") {
      // Category coordinates are positions along the axis; a bar occupies one unit around its index.
      const first = Math.max(0, Math.round(lo));
      const last = Math.min(labels.length - 1, Math.round(hi));
      for (let i = first; i <= last; i++) picked.add(i);
    } else {
      payload.ranges.push([lo, hi]);
    }
  }
  payload.categories = [...picked].sort((x, y) => x - y).map((i) => labels[i] as string);
  return payload;
}

/** Raw column values (the `x` channel) behind brushed category labels, labels with no row dropped. */
export function resolveBrushValues(
  data: ChartData,
  labels: string[],
  o: { format?: string | null; locale?: string } = {},
): DrillValue[] {
  const column = data.columns.x ?? [];
  const kind = data.kinds.x;
  const out: DrillValue[] = [];
  for (const label of labels) {
    const raw = column.find(
      (v) => categoryLabel(v, kind, o.format, o.locale) === label || String(v) === label,
    );
    if (raw === undefined) continue;
    out.push(toDrillValue(raw));
  }
  return out;
}

export function toDrillValue(raw: unknown): DrillValue {
  if (raw === null || raw === undefined) return null;
  if (typeof raw === "string" || typeof raw === "number" || typeof raw === "boolean") return raw;
  if (typeof raw === "bigint" && Math.abs(Number(raw)) <= Number.MAX_SAFE_INTEGER) {
    return Number(raw);
  }
  return String(raw);
}

/** The legend's state after one entry was toggled. */
export interface LegendChange {
  /** The series name that was clicked. */
  toggled: string;
  selected: Record<string, boolean>;
  shown: string[];
  hidden: string[];
}

export function legendChangeFrom(event: unknown): LegendChange {
  const e = (event ?? {}) as { name?: string; selected?: Record<string, boolean> };
  const selected = e.selected ?? {};
  const names = Object.keys(selected);
  return {
    toggled: e.name ?? "",
    selected,
    shown: names.filter((n) => selected[n]),
    hidden: names.filter((n) => !selected[n]),
  };
}

/**
 * Raw `color` column values for legend entries. A grouped series is named after its color value,
 * or "<value> · <measure>" with several measures; names that match no row are dropped.
 */
export function legendValues(data: ChartData, names: string[]): DrillValue[] {
  const column = data.columns.color ?? [];
  const kind = data.kinds.color;
  const out: DrillValue[] = [];
  for (const name of names) {
    const raw = column.find((v) => {
      const label = categoryLabel(v, kind);
      return name === label || name.startsWith(`${label} · `);
    });
    const value = raw === undefined ? undefined : toDrillValue(raw);
    if (value !== undefined && !out.includes(value)) out.push(value);
  }
  return out;
}
