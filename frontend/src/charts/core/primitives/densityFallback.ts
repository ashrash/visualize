import { sequentialRamp } from "../palettes";
import { resolveTheme } from "../theme";
import type { ChartData, ChartTheme } from "../types";

/** Marks above which a scatter should be drawn as density (matches the server's mark cap). */
export const SCATTER_MARK_CAP = 50_000;

interface RenderApi {
  value(dim: number): number;
  coord(point: number[]): number[];
  style(): Record<string, unknown>;
}

/**
 * Over-cap scatters arrive as 2-D bins (`reduction: "binned"`): columns `count` and the bin
 * edges `x_lo`, `x_hi`, `y_lo`, `y_hi`. This draws each bin as a rectangle colored by count,
 * with a continuous legend. Returns null when the data is not binned, so a scatter renderer
 * can do `densityFallback(data, {theme}) ?? pointsSeries(data)`.
 */
export function densityFallback(
  data: ChartData,
  o: { theme: ChartTheme; xAxisIndex?: number; yAxisIndex?: number },
): { series: Record<string, unknown>; visualMap: Record<string, unknown> } | null {
  if (data.meta.reduction !== "binned") return null;
  const { x_lo, x_hi, y_lo, y_hi, count } = data.columns as Record<string, number[] | undefined>;
  if (!x_lo || !x_hi || !y_lo || !y_hi || !count) return null;

  const theme = resolveTheme(o.theme);
  const max = Math.max(1, ...count);
  return {
    series: {
      type: "custom",
      name: "Density",
      xAxisIndex: o.xAxisIndex ?? 0,
      yAxisIndex: o.yAxisIndex ?? 0,
      // [x_lo, x_hi, y_lo, y_hi, count]
      data: count.map((c, i) => [x_lo[i], x_hi[i], y_lo[i], y_hi[i], c]),
      encode: { x: [0, 1], y: [2, 3], tooltip: [4] },
      renderItem(_params: unknown, api: RenderApi) {
        const [x0 = 0, y0 = 0] = api.coord([api.value(0), api.value(2)]);
        const [x1 = 0, y1 = 0] = api.coord([api.value(1), api.value(3)]);
        return {
          type: "rect",
          shape: {
            x: Math.min(x0, x1),
            y: Math.min(y0, y1),
            width: Math.abs(x1 - x0) + 0.5,
            height: Math.abs(y1 - y0) + 0.5,
          },
          style: api.style(),
        };
      },
    },
    visualMap: {
      type: "continuous",
      dimension: 4,
      min: 0,
      max,
      calculable: false,
      orient: "vertical",
      right: 4,
      top: "middle",
      inRange: { color: sequentialRamp(theme) },
      textStyle: { color: theme.textMuted },
      text: ["More", "Fewer"],
    },
  };
}
