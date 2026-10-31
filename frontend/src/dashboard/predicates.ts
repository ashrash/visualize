/**
 * Interaction signals to Predicates. Every click, brush, legend toggle and drill in a dashboard
 * ends up as the one shared `Predicate` type (CLAUDE.md section 3); nothing else crosses the bus.
 */
import type { ChartSpec, Encoding, Predicate } from "../api/types";
import { categoryLabel } from "../charts/core/series";

type TimeUnit = NonNullable<Encoding["timeUnit"]>;

/** What a chart reports for a plain click on a mark (ECharts event params, reduced). */
export interface PointClick {
  /** Category name, or the formatted axis label. */
  name?: string | number | null;
  /** Series name: the colour group of the clicked mark. */
  seriesName?: string | null;
  /** Raw axis value for numeric/time axes (epoch ms or ISO string). */
  axisValue?: string | number | null;
}

/** A brushed span on the x axis, in axis values (epoch ms for time axes). */
export interface BrushSpan {
  /** Category names inside the brush, for category axes. */
  categories?: string[];
  min?: number;
  max?: number;
}

const COLOR_CHANNEL = "color";

function channelField(spec: ChartSpec, channel: string): Encoding | undefined {
  return spec.encodings?.[channel]?.find((e) => e.field);
}

/** Start of the time bucket containing `ts`, and where the next one begins (UTC). */
export function bucketBounds(ts: number, unit: TimeUnit): [Date, Date] {
  const d = new Date(ts);
  const y = d.getUTCFullYear();
  const m = d.getUTCMonth();
  const day = d.getUTCDate();
  switch (unit) {
    case "year":
      return [new Date(Date.UTC(y, 0, 1)), new Date(Date.UTC(y + 1, 0, 1))];
    case "quarter": {
      const q = Math.floor(m / 3) * 3;
      return [new Date(Date.UTC(y, q, 1)), new Date(Date.UTC(y, q + 3, 1))];
    }
    case "month":
      return [new Date(Date.UTC(y, m, 1)), new Date(Date.UTC(y, m + 1, 1))];
    case "week": {
      // ISO week: Monday start (DuckDB date_trunc('week') is Monday-based).
      const start = new Date(Date.UTC(y, m, day - ((d.getUTCDay() + 6) % 7)));
      return [start, new Date(start.getTime() + 7 * 86_400_000)];
    }
    case "day":
      return [new Date(Date.UTC(y, m, day)), new Date(Date.UTC(y, m, day + 1))];
    case "hour": {
      const start = new Date(Date.UTC(y, m, day, d.getUTCHours()));
      return [start, new Date(start.getTime() + 3_600_000)];
    }
    case "minute": {
      const start = new Date(Date.UTC(y, m, day, d.getUTCHours(), d.getUTCMinutes()));
      return [start, new Date(start.getTime() + 60_000)];
    }
  }
}

function toEpoch(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (value instanceof Date) return value.getTime();
  if (typeof value === "string") {
    const parsed = Date.parse(value);
    return Number.isNaN(parsed) ? null : parsed;
  }
  return null;
}

/**
 * The raw values behind a chart's labels. ECharts reports a clicked category by its display
 * label ("2,020", "(blank)", "Jan 2024"), never by the value in the data, so a click is mapped
 * back by running the renderer's own `categoryLabel` over the raw values the chart was drawn from.
 */
export interface ChartLookup {
  xs: readonly unknown[];
  xKind?: string;
  /** `style.xAxis.format`: the renderer labels categories with it (pies do not). */
  xFormat?: string | null;
  colors: readonly unknown[];
  colorKind?: string;
}

type Raw = { found: true; value: unknown } | { found: false };

/** The one raw value whose label is `label`; not found when none or several match. */
function rawFor(
  label: string,
  raws: readonly unknown[],
  kind: string | undefined,
  formats: (string | null | undefined)[],
): Raw {
  const hits = new Set<unknown>();
  for (const raw of new Set(raws)) {
    if (formats.some((f) => categoryLabel(raw, kind, f) === label)) hits.add(raw);
  }
  if (hits.size !== 1) return { found: false };
  return { found: true, value: [...hits][0] };
}

const isBlank = (v: unknown) => v === null || v === undefined || v === "";
const scalar = (v: unknown): string | number | boolean | null =>
  isBlank(v) ? null : typeof v === "number" || typeof v === "boolean" ? v : String(v);

/** Plot types whose x channel carries the raw values of the x field, so a click can filter it. */
export const CLICK_FILTER_PLOTS: ReadonlySet<string> = new Set([
  "bar",
  "horizontal_bar",
  "grouped_bar",
  "stacked_bar",
  "percent_stacked_bar",
  "line",
  "area",
  "stacked_area",
  "pie",
  "donut",
]);

/** Plot types with a horizontal x axis that a drag can span (not the swapped-axis forms). */
export const BRUSH_PLOTS: ReadonlySet<string> = new Set([
  "bar",
  "grouped_bar",
  "stacked_bar",
  "percent_stacked_bar",
  "line",
  "area",
  "stacked_area",
]);

export const canClickFilter = (spec: ChartSpec) => CLICK_FILTER_PLOTS.has(spec.plotType);

export const canBrush = (spec: ChartSpec) =>
  BRUSH_PLOTS.has(spec.plotType) && spec.style?.orientation !== "horizontal";

function datePredicate(field: string, from: number, to: number, closed: boolean): Predicate {
  return {
    op: "between_dates",
    field,
    min: new Date(from).toISOString(),
    max: new Date(to).toISOString(),
    includeMin: true,
    includeMax: closed,
  };
}

/** The x field's `time` semantics: a bucketed time field, or a raw temporal one. */
function isTemporal(x: Encoding, lookup: ChartLookup | undefined) {
  return Boolean(x.timeUnit) || lookup?.xKind === "temporal";
}

/** A single x value (already resolved to its raw form) as a predicate. */
function xValuePredicate(x: Encoding & { field: string }, raw: unknown): Predicate | null {
  if (x.timeUnit) {
    const ts = toEpoch(raw);
    if (ts === null) return null;
    const [start, end] = bucketBounds(ts, x.timeUnit);
    return datePredicate(x.field, start.getTime(), end.getTime(), false);
  }
  return { op: "in", field: x.field, values: [scalar(raw)] };
}

/**
 * A plain click on a mark: the raw x value (or time bucket) and, for grouped series, the raw
 * colour group. Returns [] when it cannot be resolved with certainty: a click never filters by
 * a guess.
 */
export function pointPredicates(
  spec: ChartSpec,
  click: PointClick,
  lookup?: ChartLookup,
): Predicate[] {
  const x = channelField(spec, "x");
  if (!x?.field) return [];
  const field = { ...x, field: x.field };
  const label = isBlank(click.name) ? "" : String(click.name);
  const byLabel: Raw =
    lookup && label !== ""
      ? rawFor(label, lookup.xs, lookup.xKind, [lookup.xFormat, null])
      : { found: false };
  let predicate: Predicate | null = null;
  if (isTemporal(x, lookup)) {
    // Prefer the raw axis value (time axes report epoch ms); else map the label back.
    const ts = toEpoch(click.axisValue) ?? (byLabel.found ? toEpoch(byLabel.value) : null);
    if (ts === null) return [];
    predicate = x.timeUnit ? xValuePredicate(field, ts) : datePredicate(x.field, ts, ts + 1, false);
  } else if (byLabel.found) {
    predicate = xValuePredicate(field, byLabel.value);
  } else if (typeof click.axisValue === "number") {
    predicate = xValuePredicate(field, click.axisValue);
  } else if (label === "(blank)" && lookup?.xs.some(isBlank)) {
    predicate = xValuePredicate(field, null);
  }
  if (!predicate) return [];
  const out: Predicate[] = [predicate];
  const color = channelField(spec, COLOR_CHANNEL);
  if (color?.field && click.seriesName && lookup) {
    const group = rawFor(click.seriesName, lookup.colors, lookup.colorKind, [null]);
    if (group.found) out.push({ op: "in", field: color.field, values: [scalar(group.value)] });
  }
  return out;
}

/**
 * A brush over the x axis: a numeric/time range, or the categories it covers (labels mapped back
 * to raw values; an unresolvable label cancels the whole selection).
 */
export function brushPredicates(
  spec: ChartSpec,
  span: BrushSpan,
  lookup?: ChartLookup,
): Predicate[] {
  const x = channelField(spec, "x");
  if (!x?.field) return [];
  if (span.categories) {
    if (span.categories.length === 0 || !lookup) return [];
    const raws: unknown[] = [];
    for (const label of span.categories) {
      const hit = rawFor(label, lookup.xs, lookup.xKind, [lookup.xFormat, null]);
      if (!hit.found) return [];
      raws.push(hit.value);
    }
    if (isTemporal(x, lookup)) {
      const times = raws.map(toEpoch);
      if (times.some((t) => t === null)) return [];
      const lo = Math.min(...(times as number[]));
      const hi = Math.max(...(times as number[]));
      const end = x.timeUnit ? bucketBounds(hi, x.timeUnit)[1].getTime() : hi + 1;
      return [datePredicate(x.field, lo, end, false)];
    }
    return [{ op: "in", field: x.field, values: raws.map(scalar) }];
  }
  if (span.min === undefined || span.max === undefined) return [];
  const lo = Math.min(span.min, span.max);
  const hi = Math.max(span.min, span.max);
  if (isTemporal(x, lookup)) return [datePredicate(x.field, lo, hi, true)];
  return [{ op: "range", field: x.field, min: lo, max: hi, includeMin: true, includeMax: true }];
}

/**
 * Legend toggles: the colour groups still switched on, as raw values, become an `in` predicate;
 * nothing is filtered while every group is on, or if a group cannot be resolved.
 */
export function legendPredicates(
  spec: ChartSpec,
  selected: Record<string, boolean>,
  lookup?: ChartLookup,
): Predicate[] {
  const color = channelField(spec, COLOR_CHANNEL);
  if (!color?.field || !lookup) return [];
  const names = Object.keys(selected);
  const on = names.filter((n) => selected[n]);
  if (on.length === names.length || on.length === 0) return [];
  const raws: (string | number | boolean | null)[] = [];
  for (const name of on) {
    const hit = rawFor(name, lookup.colors, lookup.colorKind, [null]);
    if (!hit.found) return [];
    raws.push(scalar(hit.value));
  }
  return [{ op: "in", field: color.field, values: raws }];
}

type RangeLike = { min?: number | string | null; max?: number | string | null };

const sameValues = (a: unknown[], b: unknown[]) =>
  a.length === b.length && a.every((v) => b.includes(v));

/** True when two predicates select the same thing (used to toggle a click off again). */
export function samePredicate(a: Predicate, b: Predicate): boolean {
  if (a.op !== b.op || a.field !== b.field) return false;
  if (a.op === "in" || a.op === "not_in") {
    return sameValues(a.values, (b as typeof a).values);
  }
  if (a.op === "eq") return a.value === (b as typeof a).value;
  if (a.op === "is_null") return a.isNull === (b as typeof a).isNull;
  const [ra, rb] = [a, b] as [RangeLike, RangeLike];
  return ra.min === rb.min && ra.max === rb.max;
}

export function sameSelection(a: Predicate[], b: Predicate[]): boolean {
  return a.length === b.length && a.every((p, i) => b[i] !== undefined && samePredicate(p, b[i]));
}

/**
 * Multi-select: add this click's values to what is already selected on the same field
 * (shift/ctrl-click). A value already selected is removed; emptying a field removes it.
 */
export function extendSelection(current: Predicate[], clicked: Predicate[]): Predicate[] {
  const next = [...current];
  for (const p of clicked) {
    const at = next.findIndex((q) => q.field === p.field && q.op === "in");
    const existing = next[at];
    if (p.op !== "in" || !existing || existing.op !== "in") {
      next.push(p);
      continue;
    }
    const values = [...existing.values];
    for (const v of p.values) {
      const i = values.indexOf(v);
      if (i >= 0) values.splice(i, 1);
      else values.push(v);
    }
    if (values.length === 0) next.splice(at, 1);
    else next[at] = { ...existing, values };
  }
  return next;
}
