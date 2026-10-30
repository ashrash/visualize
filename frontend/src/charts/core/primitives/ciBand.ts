export interface CiBandOptions {
  /** x values: category names (aligned with the data) or numbers (value/time axis) */
  x: unknown[];
  lo: (number | null)[];
  hi: (number | null)[];
  /**
   * Distinguishes bands in one chart (they stack by id). Pass a different `id` per band when a
   * chart draws several; the default is fine for one. Kept explicit so `render` stays pure.
   */
  id?: string | number;
  color?: string;
  opacity?: number;
  yAxisIndex?: number;
  xAxisIndex?: number;
  /** Category axes take aligned values; value/time axes take [x, y] pairs. */
  axis?: "category" | "value";
}

/**
 * A shaded confidence band between `lo` and `hi`: two stacked line series, an invisible base at
 * `lo` and a filled one of height `hi - lo`. Neither appears in the tooltip; they are unnamed,
 * so they stay out of the legend too.
 *
 * `stackStrategy: "all"` is essential: ECharts' default ("samesign") stacks a positive height
 * onto 0 rather than onto a negative base, which draws the wrong band whenever `lo < 0`
 * (regression CIs, ACF, forest and impulse-response bands all cross zero).
 */
export function ciBand(o: CiBandOptions): Record<string, unknown>[] {
  const stack = `ci-band-${o.id ?? 0}`;
  const pair = (values: (number | null)[]) =>
    o.axis === "category" ? values : o.x.map((x, i) => [x, values[i] ?? null]);
  const height = o.hi.map((h, i) => {
    const l = o.lo[i];
    return h === null || h === undefined || l === null || l === undefined ? null : h - l;
  });
  const common = {
    type: "line",
    stack,
    stackStrategy: "all",
    symbol: "none",
    silent: true,
    tooltip: { show: false },
    xAxisIndex: o.xAxisIndex ?? 0,
    yAxisIndex: o.yAxisIndex ?? 0,
    z: 1,
  };
  return [
    { ...common, data: pair(o.lo), lineStyle: { opacity: 0 } },
    {
      ...common,
      data: pair(height),
      lineStyle: { opacity: 0 },
      areaStyle: { color: o.color, opacity: o.opacity ?? 0.18 },
    },
  ];
}
