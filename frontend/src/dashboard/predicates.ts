/**
 * Interaction signals to Predicates. Every click, brush, legend toggle and drill in a dashboard
 * ends up as the one shared `Predicate` type (CLAUDE.md section 3); nothing else crosses the bus.
 */
import type { ChartSpec, Encoding, Predicate } from "../api/types";

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

function toEpoch(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

function xPredicate(spec: ChartSpec, click: PointClick): Predicate | null {
  const x = channelField(spec, "x");
  if (!x?.field) return null;
  if (x.timeUnit) {
    const ts = toEpoch(click.axisValue) ?? toEpoch(click.name);
    if (ts === null) return null;
    const [start, end] = bucketBounds(ts, x.timeUnit);
    return {
      op: "between_dates",
      field: x.field,
      min: start.toISOString(),
      max: end.toISOString(),
      includeMin: true,
      includeMax: false,
    };
  }
  if (click.name === null || click.name === undefined) return null;
  return { op: "in", field: x.field, values: [click.name] };
}

/**
 * A plain click on a mark: the x category (or time bucket) and, for grouped series, the colour
 * group. Returns [] when the chart has no x field to filter on (the click means nothing).
 */
export function pointPredicates(spec: ChartSpec, click: PointClick): Predicate[] {
  const out: Predicate[] = [];
  const x = xPredicate(spec, click);
  if (x) out.push(x);
  const color = channelField(spec, COLOR_CHANNEL);
  if (color?.field && click.seriesName) {
    out.push({ op: "in", field: color.field, values: [click.seriesName] });
  }
  return out;
}

/** A brush over the x axis: a numeric/time range, or a multi-select on a category axis. */
export function brushPredicates(spec: ChartSpec, span: BrushSpan): Predicate[] {
  const x = channelField(spec, "x");
  if (!x?.field) return [];
  if (span.categories) {
    return span.categories.length > 0
      ? [{ op: "in", field: x.field, values: span.categories }]
      : [];
  }
  if (span.min === undefined || span.max === undefined) return [];
  const lo = Math.min(span.min, span.max);
  const hi = Math.max(span.min, span.max);
  if (x.timeUnit) {
    return [
      {
        op: "between_dates",
        field: x.field,
        min: new Date(lo).toISOString(),
        max: new Date(hi).toISOString(),
        includeMin: true,
        includeMax: true,
      },
    ];
  }
  return [{ op: "range", field: x.field, min: lo, max: hi, includeMin: true, includeMax: true }];
}

/**
 * Legend toggles: the colour groups still switched on become an `in` predicate; nothing is
 * filtered while every group is on (or none could be matched).
 */
export function legendPredicates(spec: ChartSpec, selected: Record<string, boolean>): Predicate[] {
  const color = channelField(spec, COLOR_CHANNEL);
  if (!color?.field) return [];
  const names = Object.keys(selected);
  const on = names.filter((n) => selected[n]);
  if (on.length === names.length || on.length === 0) return [];
  return [{ op: "in", field: color.field, values: on }];
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
