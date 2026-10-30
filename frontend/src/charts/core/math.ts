/**
 * Extremes without `Math.max(...values)`: spreading a large array into arguments throws a
 * RangeError in Safari (and Chrome) past ~100k elements, and charts can carry up to 50k marks
 * per series.
 */
export function maxOf(values: Iterable<number>, fallback = Number.NEGATIVE_INFINITY): number {
  let max = fallback;
  for (const v of values) if (v > max) max = v;
  return max;
}

export function minOf(values: Iterable<number>, fallback = Number.POSITIVE_INFINITY): number {
  let min = fallback;
  for (const v of values) if (v < min) min = v;
  return min;
}
