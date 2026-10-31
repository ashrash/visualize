/**
 * Interaction signals to Predicates. Every click, brush, legend toggle and drill in a dashboard
 * ends up as the one shared `Predicate` type (CLAUDE.md section 3); nothing else crosses the bus.
 * The signals come from `ChartView`'s own callbacks, which report raw values from the data.
 */
import type { ChartSpec, Encoding, Predicate } from "../api/types";
import type { ChartBrush, ChartLegendChange, ChartPointClick, DrillValue } from "../charts";

type TimeUnit = NonNullable<Encoding["timeUnit"]>;

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

const isBlank = (v: unknown) => v === null || v === undefined || v === "";

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

/**
 * Plot types whose brushed axis is in the units of the x field, so a band can filter it: the
 * categories or values of that field. Not Q-Q plots (theoretical quantiles), lag, Andrews, PCA or
 * cluster scores, event time and the like: their x channel names a field but the axis shows
 * something computed from it, and a band over it would filter by numbers nobody selected.
 * Scatter is in because its value axis is in data units, even in the binned-density fallback.
 */
export const BRUSH_FILTER_PLOTS: ReadonlySet<string> = new Set([
  "bar",
  "horizontal_bar",
  "grouped_bar",
  "stacked_bar",
  "percent_stacked_bar",
  "line",
  "area",
  "stacked_area",
  "scatter",
]);

export const canClickFilter = (spec: ChartSpec) => CLICK_FILTER_PLOTS.has(spec.plotType);

export const canBrushFilter = (spec: ChartSpec) => BRUSH_FILTER_PLOTS.has(spec.plotType);

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

/**
 * The time bucket of the x field. Only the x channel is looked at: the same field can also be a
 * measure or a colour on another channel, and that encoding says nothing about the x axis.
 */
function bucketUnit(spec: ChartSpec, field: string): TimeUnit | undefined {
  const x = spec.encodings?.x?.[0];
  return x?.field === field ? (x.timeUnit ?? undefined) : undefined;
}

/**
 * One raw x value as a predicate. With a `timeUnit` the raw value is the START of a bucket, so it
 * becomes the half-open range of that bucket. A raw time value without a bucket becomes the
 * half-open millisecond around it: timestamps carry finer precision than a JavaScript date. Any
 * other value is an exact match (NULL for blank).
 */
function valuePredicate(
  field: string,
  raw: DrillValue,
  unit: TimeUnit | undefined,
  temporal: boolean,
): Predicate | null {
  if (unit) {
    const ts = toEpoch(raw);
    if (ts === null) return null;
    const [start, end] = bucketBounds(ts, unit);
    return datePredicate(field, start.getTime(), end.getTime(), false);
  }
  if (temporal && !isBlank(raw)) {
    const ts = toEpoch(raw);
    return ts === null ? null : datePredicate(field, ts, ts + 1, false);
  }
  return { op: "in", field, values: [isBlank(raw) ? null : raw] };
}

export interface ClickContext {
  /** The raw colour group of the clicked series, when it is known. */
  color?: DrillValue;
  /** The x column holds time values (from the chart's data kinds). */
  temporal?: boolean;
  /** The predicates of the chart's drill path, so a leaf click keeps its context. */
  drill?: Predicate[];
}

/**
 * A plain click on a mark, from the chart's own callback (`rawValue` is the value in the data, not
 * the label that was drawn). Nothing is filtered unless the value is known for certain, and a
 * click that drilled the chart one level deeper is that chart's own navigation, not a filter.
 */
export function pointPredicates(
  spec: ChartSpec,
  click: Pick<ChartPointClick, "field" | "rawValue" | "drilled">,
  context: ClickContext = {},
): Predicate[] {
  if (click.drilled || !click.field || click.rawValue === undefined) return [];
  const x = valuePredicate(
    click.field,
    click.rawValue,
    bucketUnit(spec, click.field),
    context.temporal === true,
  );
  if (!x) return [];
  const out: Predicate[] = [...(context.drill ?? []), x];
  const color = spec.encodings?.color?.find((e) => e.field);
  if (color?.field && context.color !== undefined) {
    out.push({
      op: "in",
      field: color.field,
      values: [isBlank(context.color) ? null : context.color],
    });
  }
  return out;
}

/**
 * A committed brush, from the chart's own callback. Category brushes carry the raw values under
 * the bands; value and time brushes carry the covered range. A band along a horizontal chart's
 * category axis reports `axis: "y"` and still filters its categories; a range along a y axis is
 * not a range of the x field and is ignored.
 *
 * Several bands on a time-bucketed axis are one date range only when they touch; otherwise the
 * single range spanning them would take in buckets nobody selected, so nothing is filtered.
 */
export function brushPredicates(
  spec: ChartSpec,
  brush: Pick<
    ChartBrush,
    "field" | "axis" | "kind" | "ranges" | "categories" | "values" | "cleared"
  >,
  context: Pick<ClickContext, "drill"> = {},
): Predicate[] {
  if (brush.cleared || !brush.field) return [];
  const field = brush.field;
  const drill = context.drill ?? [];
  const unit = bucketUnit(spec, field);
  if (brush.kind === "category") {
    // Every brushed label must map to a value in the data, or the selection would be wider or
    // narrower than what the user drew.
    if (brush.categories.length === 0 || brush.values.length !== brush.categories.length) return [];
    if (unit) {
      const times = brush.values.map(toEpoch);
      if (times.some((t) => t === null)) return [];
      const starts = [...new Set(times as number[])].sort((a, b) => a - b);
      for (let i = 0; i < starts.length - 1; i++) {
        if (bucketBounds(starts[i] as number, unit)[1].getTime() !== starts[i + 1]) return [];
      }
      const end = bucketBounds(starts.at(-1) as number, unit)[1].getTime();
      return [...drill, datePredicate(field, starts[0] as number, end, false)];
    }
    return [
      ...drill,
      { op: "in", field, values: brush.values.map((v) => (isBlank(v) ? null : v)) },
    ];
  }
  if (brush.axis === "y" || brush.ranges.length !== 1) return [];
  const [lo, hi] = brush.ranges[0] as [number, number];
  if (brush.kind === "time") return [...drill, datePredicate(field, lo, hi, true)];
  return [...drill, { op: "range", field, min: lo, max: hi, includeMin: true, includeMax: true }];
}

/**
 * Legend toggles. `hidden` are the entries switched off, `hiddenValues` their raw colour values and
 * `domain` every colour value in the chart's data. The filter keeps every value except the hidden
 * ones, and it only exists when each hidden entry maps to exactly one value: an entry that cannot
 * be resolved would otherwise silently narrow the result, so then nothing is filtered. Entries are
 * series, not groups, when the chart has several measures, so those charts are not filtered.
 */
export function legendPredicates(
  change: Pick<ChartLegendChange, "field" | "hidden" | "hiddenValues">,
  context: { domain: readonly DrillValue[] | undefined; measures: number; drill?: Predicate[] },
): Predicate[] {
  const { domain } = context;
  if (!change.field || !domain || context.measures > 1) return [];
  if (change.hidden.length === 0 || change.hiddenValues.length !== change.hidden.length) return [];
  const allowed = domain.filter((v) => !change.hiddenValues.includes(v));
  if (allowed.length === 0 || allowed.length === domain.length) return [];
  return [
    ...(context.drill ?? []),
    { op: "in", field: change.field, values: allowed.map((v) => (isBlank(v) ? null : v)) },
  ];
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
    if (p.op !== "in") {
      // A range or date bucket cannot be added to: a second one on the same field replaces it
      // (stacking them would select nothing).
      const same = next.findIndex((q) => q.field === p.field && q.op === p.op);
      if (same >= 0) next[same] = p;
      else next.push(p);
      continue;
    }
    if (existing?.op !== "in") {
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
