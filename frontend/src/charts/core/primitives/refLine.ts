import { toDate } from "../format";

export type RefLineStyle = "solid" | "dashed" | "dotted";

export interface RefLineOptions {
  /** "x"/"y" draw a line through `value`; "diagonal" draws y = x across `extent`. */
  axis: "x" | "y" | "diagonal";
  value?: number | string;
  label?: string;
  style?: RefLineStyle;
  color?: string;
  extent?: [number, number];
  /** The x axis is a time axis: date strings are converted to epoch ms. */
  time?: boolean;
  /** Swap which ECharts axis x/y map to (horizontal bar charts). */
  swapped?: boolean;
}

/** One markLine data item (attach to any series' `markLine.data`). */
export function refLineItem(o: RefLineOptions): Record<string, unknown> {
  let value: number | string = o.value ?? 0;
  if (o.time && typeof value === "string") value = toDate(value)?.getTime() ?? value;
  const logical = o.axis === "y" ? "yAxis" : "xAxis";
  const key = o.swapped ? (logical === "yAxis" ? "xAxis" : "yAxis") : logical;
  return {
    [key]: value,
    name: o.label,
    label: o.label ? { show: true, formatter: o.label, position: "insideEndTop" } : { show: false },
    lineStyle: { type: o.style ?? "dashed", color: o.color, width: 1.5 },
  };
}

/** A reference line as a series fragment: a markLine for x/y, a two-point line for diagonals. */
export function refLine(o: RefLineOptions): {
  markLine?: Record<string, unknown>;
  series?: Record<string, unknown>;
} {
  if (o.axis === "diagonal") {
    const [lo, hi] = o.extent ?? [0, 1];
    return {
      series: {
        type: "line",
        name: o.label,
        data: [
          [lo, lo],
          [hi, hi],
        ],
        symbol: "none",
        silent: true,
        tooltip: { show: false },
        lineStyle: { type: o.style ?? "dashed", color: o.color, width: 1.5 },
        z: 1,
      },
    };
  }
  return { markLine: { silent: true, symbol: "none", data: [refLineItem(o)] } };
}
