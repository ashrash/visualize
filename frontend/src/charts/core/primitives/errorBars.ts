import { resolveTheme } from "../theme";
import type { ChartTheme } from "../types";

export interface ErrorBarsOptions {
  /** x values (category names or numbers) */
  x: unknown[];
  lo: (number | null)[];
  hi: (number | null)[];
  orientation?: "v" | "h";
  name?: string;
  color?: string;
  /** For grouped bars: this series' position among `groupCount` bars, to center on its bar. */
  groupIndex?: number;
  groupCount?: number;
  yAxisIndex?: number;
  xAxisIndex?: number;
  capWidth?: number;
  /**
   * The bar chart's `barCategoryGap` and `barGap` as fractions (ECharts defaults 0.2 and 0.3),
   * so whiskers line up with grouped bars.
   */
  categoryGap?: number;
  barGap?: number;
  theme: ChartTheme;
}

interface RenderApi {
  value(dim: number): number;
  coord(point: unknown[]): number[];
  size(point: number[]): number[];
}

/**
 * Error bars as a custom series: a whisker with caps from `lo` to `hi` at each x.
 * Horizontal charts swap the axes (value on x, category on y).
 */
export function errorBars(o: ErrorBarsOptions): Record<string, unknown> {
  const theme = resolveTheme(o.theme);
  const horizontal = o.orientation === "h";
  const stroke = o.color ?? theme.text;
  const count = o.groupCount ?? 1;
  const index = o.groupIndex ?? 0;
  const cap = o.capWidth ?? 5;
  const categoryGap = o.categoryGap ?? 0.2;
  const barGap = o.barGap ?? 0.3;
  const data = o.x
    .map((x, i) => [x, o.lo[i] ?? null, o.hi[i] ?? null])
    .filter((row) => row[1] !== null && row[2] !== null);

  return {
    type: "custom",
    name: o.name,
    // Error bars decorate another series: no tooltip row.
    tooltip: { show: false },
    silent: true,
    z: 5,
    xAxisIndex: o.xAxisIndex ?? 0,
    yAxisIndex: o.yAxisIndex ?? 0,
    data,
    encode: horizontal ? { x: [1, 2], y: 0 } : { x: 0, y: [1, 2] },
    renderItem(_params: unknown, api: RenderApi) {
      const cx = api.value(0);
      const lo = api.value(1);
      const hi = api.value(2);
      const [ax = 0, ay = 0] = api.coord(horizontal ? [lo, cx] : [cx, lo]);
      const [bx = 0, by = 0] = api.coord(horizontal ? [hi, cx] : [cx, hi]);
      // Slide along the category axis so each grouped bar gets its own whisker.
      const band = api.size(horizontal ? [0, 1] : [1, 0])[horizontal ? 1 : 0] ?? 0;
      const barWidth = (band * (1 - categoryGap)) / (count + (count - 1) * barGap);
      const shift = (index - (count - 1) / 2) * barWidth * (1 + barGap);
      const dx = horizontal ? 0 : shift;
      const dy = horizontal ? shift : 0;
      const style = { stroke, lineWidth: 1.5, fill: "none" };
      const line = (x1: number, y1: number, x2: number, y2: number) => ({
        type: "line",
        shape: { x1: x1 + dx, y1: y1 + dy, x2: x2 + dx, y2: y2 + dy },
        style,
      });
      return {
        type: "group",
        children: horizontal
          ? [
              line(ax, ay, bx, by),
              line(ax, ay - cap, ax, ay + cap),
              line(bx, by - cap, bx, by + cap),
            ]
          : [
              line(ax, ay, bx, by),
              line(ax - cap, ay, ax + cap, ay),
              line(bx - cap, by, bx + cap, by),
            ],
      };
    },
  };
}
