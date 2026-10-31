/** Filter control values to Predicates and back. Controls hold plain strings/numbers. */
import type { Pipeline, Predicate, SourceRef } from "../api/types";

const DAY_MS = 86_400_000;

const isDay = (value: string) => /^\d{4}-\d{2}-\d{2}$/.test(value);

/** Add `days` to a YYYY-MM-DD date string (UTC), returning YYYY-MM-DD. */
export function addDays(day: string, days: number): string {
  return new Date(Date.parse(`${day}T00:00:00Z`) + days * DAY_MS).toISOString().slice(0, 10);
}

/**
 * A date range control (inclusive days) as a predicate: [from, to + 1 day), so a TIMESTAMP
 * column keeps the whole last day. Either bound may be empty (open); both empty = no filter.
 */
export function dateRangePredicate(field: string, from: string, to: string): Predicate | null {
  const lo = isDay(from) ? from : null;
  const hi = isDay(to) ? addDays(to, 1) : null;
  if (!lo && !hi) return null;
  return { op: "between_dates", field, min: lo, max: hi, includeMin: true, includeMax: false };
}

/** The inverse of `dateRangePredicate`, for showing the control's current value. */
export function dateRangeValue(predicate: Predicate | null | undefined): {
  from: string;
  to: string;
} {
  if (predicate?.op !== "between_dates") return { from: "", to: "" };
  const from = typeof predicate.min === "string" ? predicate.min.slice(0, 10) : "";
  const exclusiveEnd = typeof predicate.max === "string" ? predicate.max.slice(0, 10) : "";
  if (!exclusiveEnd) return { from, to: "" };
  const to =
    predicate.includeMax === false && isDay(exclusiveEnd)
      ? addDays(exclusiveEnd, -1)
      : exclusiveEnd;
  return { from, to };
}

/** A numeric range control; a range covering the whole domain is "no filter". */
export function rangePredicate(
  field: string,
  lo: number,
  hi: number,
  domain: [number, number],
): Predicate | null {
  const [min, max] = lo <= hi ? [lo, hi] : [hi, lo];
  if (min <= domain[0] && max >= domain[1]) return null;
  return { op: "range", field, min, max, includeMin: true, includeMax: true };
}

export function rangeValue(
  predicate: Predicate | null | undefined,
  domain: [number, number],
): [number, number] {
  if (predicate?.op !== "range") return domain;
  return [
    typeof predicate.min === "number" ? predicate.min : domain[0],
    typeof predicate.max === "number" ? predicate.max : domain[1],
  ];
}

/** A dropdown selection; nothing selected = no filter. */
export function membershipPredicate(
  field: string,
  values: (string | number | boolean | null)[],
): Predicate | null {
  return values.length === 0 ? null : { op: "in", field, values };
}

export function membershipValue(
  predicate: Predicate | null | undefined,
): (string | number | boolean | null)[] {
  return predicate?.op === "in" ? predicate.values : [];
}

/** The dataset behind a chart source: a pipeline's own source dataset, followed to the end. */
export function datasetOfSource(source: SourceRef, pipelines: Pipeline[]): string {
  const seen = new Set<string>();
  let ref = source;
  while (ref.kind === "pipeline" && !seen.has(ref.id)) {
    seen.add(ref.id);
    const pipeline = pipelines.find((p) => p.id === ref.id);
    if (!pipeline) break;
    ref = pipeline.source;
  }
  return ref.id;
}
