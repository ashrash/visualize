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

export const canClickFilter = (spec: ChartSpec) => CLICK_FILTER_PLOTS.has(spec.plotType);

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

/** The encoding that feeds `field` (the x channel, or the drilled level's replacement). */
function encodingOf(spec: ChartSpec, field: string): Encoding | undefined {
  for (const items of Object.values(spec.encodings ?? {})) {
    const hit = items.find((e) => e.field === field);
    if (hit) return hit;
  }
  return undefined;
}

/**
 * One raw x value as a predicate. With a `timeUnit` the raw value is the START of a bucket, so it
 * becomes the half-open range of that bucket; otherwise it is an exact match (NULL for blank).
 */
function valuePredicate(
  field: string,
  raw: DrillValue,
  unit: TimeUnit | undefined,
): Predicate | null {
  if (unit) {
    const ts = toEpoch(raw);
    if (ts === null) return null;
    const [start, end] = bucketBounds(ts, unit);
    return datePredicate(field, start.getTime(), end.getTime(), false);
  }
  return { op: "in", field, values: [isBlank(raw) ? null : raw] };
}

/**
 * A plain click on a mark, from the chart's own callback (`rawValue` is the value in the data, not
 * the label that was drawn). Nothing is filtered unless the value is known for certain, and a
 * click that drilled the chart one level deeper is that chart's own navigation, not a filter.
 *
 * `colorValue` is the raw colour group of the clicked series, when it is known.
 */
export function pointPredicates(
  spec: ChartSpec,
  click: Pick<ChartPointClick, "field" | "rawValue" | "drilled">,
  colorValue?: DrillValue,
): Predicate[] {
  if (click.drilled || !click.field || click.rawValue === undefined) return [];
  const unit = encodingOf(spec, click.field)?.timeUnit ?? undefined;
  const x = valuePredicate(click.field, click.rawValue, unit);
  if (!x) return [];
  const out: Predicate[] = [x];
  const color = spec.encodings?.color?.find((e) => e.field);
  if (color?.field && colorValue !== undefined) {
    out.push({ op: "in", field: color.field, values: [isBlank(colorValue) ? null : colorValue] });
  }
  return out;
}

/**
 * A committed brush, from the chart's own callback. Category brushes carry the raw values under
 * the band; value and time brushes carry the covered range. Only a brush along the x field
 * filters: on a horizontal chart the band spans the measure axis, which no predicate can express.
 */
export function brushPredicates(
  spec: ChartSpec,
  brush: Pick<
    ChartBrush,
    "field" | "axis" | "kind" | "ranges" | "categories" | "values" | "cleared"
  >,
): Predicate[] {
  if (brush.cleared || !brush.field) return [];
  const field = brush.field;
  const unit = encodingOf(spec, field)?.timeUnit ?? undefined;
  if (brush.kind === "category") {
    // Every brushed label must map to a value in the data, or the selection would be wider or
    // narrower than what the user drew.
    if (brush.categories.length === 0 || brush.values.length !== brush.categories.length) return [];
    if (unit) {
      const times = brush.values.map(toEpoch);
      if (times.some((t) => t === null)) return [];
      const lo = Math.min(...(times as number[]));
      const hi = Math.max(...(times as number[]));
      return [datePredicate(field, lo, bucketBounds(hi, unit)[1].getTime(), false)];
    }
    return [{ op: "in", field, values: brush.values.map((v) => (isBlank(v) ? null : v)) }];
  }
  if (brush.axis === "y" || brush.ranges.length !== 1) return [];
  const [lo, hi] = brush.ranges[0] as [number, number];
  if (brush.kind === "time") return [datePredicate(field, lo, hi, true)];
  return [{ op: "range", field, min: lo, max: hi, includeMin: true, includeMax: true }];
}

/**
 * Legend toggles, from the chart's own callback: the colour groups still switched on, as raw
 * values, become an `in` predicate. Nothing is filtered while every group is on, or when none of
 * the shown groups maps to a value in the data.
 */
export function legendPredicates(
  change: Pick<ChartLegendChange, "field" | "hidden" | "shownValues">,
): Predicate[] {
  if (!change.field || change.hidden.length === 0 || change.shownValues.length === 0) return [];
  return [
    {
      op: "in",
      field: change.field,
      values: change.shownValues.map((v) => (isBlank(v) ? null : v)),
    },
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
