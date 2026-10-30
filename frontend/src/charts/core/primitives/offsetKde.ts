import { resolveTheme } from "../theme";
import type { ChartTheme } from "../types";

export interface KdeGroup {
  name: string;
  x: number[];
  density: number[];
}

/**
 * Ridgeline layout: each group's density curve is drawn above its own baseline. Returns the
 * series and a value y axis whose tick labels are the group names. `overlap` 0 = curves just
 * touch, 1 = fully overlapped. Groups are listed top to bottom.
 */
export function offsetKde(o: {
  groups: KdeGroup[];
  overlap?: number;
  colors?: string[];
  theme: ChartTheme;
}): { series: Record<string, unknown>[]; yAxis: Record<string, unknown>; step: number } {
  const theme = resolveTheme(o.theme);
  const overlap = Math.min(1, Math.max(0, o.overlap ?? 0.5));
  const peak = Math.max(0, ...o.groups.flatMap((g) => g.density));
  const step = peak > 0 ? Math.max(peak * (1 - overlap), peak * 0.05) : 1;
  const n = o.groups.length;
  const names = o.groups.map((g) => g.name);
  const colors = o.colors ?? theme.palette;

  const series: Record<string, unknown>[] = [];
  // Each group is an invisible base series at its offset plus a stacked, filled density series.
  // Higher rows get a lower z so lower rows overlap them, as in a ridgeline.
  o.groups.forEach((group, i) => {
    const offset = (n - 1 - i) * step;
    const stack = `ridge-${i}`;
    const color = colors[i % colors.length];
    series.push({
      type: "line",
      stack,
      silent: true,
      symbol: "none",
      tooltip: { show: false },
      data: group.x.map((x) => [x, offset]),
      lineStyle: { opacity: 0 },
      z: 2 + i * 2,
    });
    series.push({
      type: "line",
      name: group.name,
      stack,
      symbol: "none",
      data: group.x.map((x, k) => [x, group.density[k] ?? 0]),
      lineStyle: { width: 1.5, color },
      areaStyle: { color, opacity: 0.55 },
      emphasis: { disabled: true },
      z: 3 + i * 2,
    });
  });

  return {
    series,
    step,
    yAxis: {
      type: "value",
      min: 0,
      max: (n - 1) * step + peak,
      interval: step,
      splitLine: { show: true, lineStyle: { color: theme.grid } },
      axisLabel: {
        color: theme.textMuted,
        formatter: (value: number) => names[n - 1 - Math.round(value / step)] ?? "",
      },
    },
  };
}
