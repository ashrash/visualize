export interface CiBandOptions {
  /** x values: category names (aligned with the data) or numbers (value/time axis) */
  x: unknown[];
  lo: (number | null)[];
  hi: (number | null)[];
  name?: string;
  color?: string;
  opacity?: number;
  yAxisIndex?: number;
  xAxisIndex?: number;
  /** Category axes take aligned values; value/time axes take [x, y] pairs. */
  axis?: "category" | "value";
}

let counter = 0;

/**
 * A shaded confidence band between `lo` and `hi`: two stacked line series, an invisible base at
 * `lo` and a filled one of height `hi - lo`. Neither appears in the tooltip; they are unnamed,
 * so they stay out of the legend too.
 */
export function ciBand(o: CiBandOptions): Record<string, unknown>[] {
  counter += 1;
  const stack = `ci-band-${counter}`;
  const pair = (values: (number | null)[]) =>
    o.axis === "category" ? values : o.x.map((x, i) => [x, values[i] ?? null]);
  const height = o.hi.map((h, i) => {
    const l = o.lo[i];
    return h === null || h === undefined || l === null || l === undefined ? null : h - l;
  });
  const common = {
    type: "line",
    stack,
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
