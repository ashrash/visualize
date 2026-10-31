import type { ChartSpec } from "../../../api/types";
import { maxOf, minOf } from "../../core/math";
import type { Obj } from "../../core/series";
import { applyStyle } from "../../core/style";
import { resolveTheme } from "../../core/theme";
import type { ChartData } from "../../core/types";
import { definePlot } from "../../core/types";
import { serverWarnings, timeChannel, valueChannel } from "./shared";

// Each calendar is ~166px tall (CALENDAR_HEIGHT + CALENDAR_GAP): unbounded years would make an
// unusably tall chart, so only the most recent ones are drawn.
const MAX_YEARS = 15;

/** Epoch ms (UTC midnight, as `toColumns` delivers a DATE column) to 'YYYY-MM-DD'. */
function isoDate(ms: number): string {
  const d = new Date(ms);
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** Distinct years present in an x (epoch ms) column, ascending. */
function yearsPresent(xs: number[]): number[] {
  const years = new Set<number>();
  for (const ms of xs) if (typeof ms === "number") years.add(new Date(ms).getUTCFullYear());
  return [...years].sort((a, b) => a - b);
}

const CALENDAR_HEIGHT = 130;
const CALENDAR_TOP = 40;
const CALENDAR_GAP = 36;

/**
 * Calendar Heatmap (Plots.md Sec4): daily values on a calendar coordinate system, one calendar
 * stacked per year present in the data.
 */
export const calendarHeatmap = definePlot({
  key: "calendar_heatmap",
  label: "Calendar heatmap",
  family: "timeseries",
  description: "Daily values shown as a GitHub-style calendar, one grid per year.",
  encodingsSchema: [timeChannel("Date"), valueChannel()],
  paramsSchema: [],
  styleSections: ["title", "tooltip", "numberFormat", "palette"],
  defaults: {},
  render(spec, data, theme) {
    const { columns } = data;
    const xs = (columns.x ?? []) as number[];
    const y0 = (columns.y0 ?? []) as (number | null)[];
    const byYear = new Map<number, [string, number | null][]>();
    for (let i = 0; i < xs.length; i++) {
      const ms = xs[i];
      if (typeof ms !== "number") continue;
      const year = new Date(ms).getUTCFullYear();
      const list = byYear.get(year);
      const point: [string, number | null] = [isoDate(ms), y0[i] ?? null];
      if (list) list.push(point);
      else byYear.set(year, [point]);
    }
    const years = yearsPresent(xs).slice(-MAX_YEARS);
    const t = resolveTheme(theme);
    const calendars: Obj[] = years.map((year, i) => ({
      range: String(year),
      top: CALENDAR_TOP + i * (CALENDAR_HEIGHT + CALENDAR_GAP),
      left: 56,
      right: 16,
      cellSize: ["auto", 14],
      yearLabel: { show: true, color: t.textMuted },
      dayLabel: { color: t.textMuted },
      monthLabel: { color: t.textMuted },
      itemStyle: { borderColor: t.background, borderWidth: 2, color: t.grid },
      splitLine: { lineStyle: { color: t.axis } },
    }));
    const series: Obj[] = years.map((year, i) => ({
      type: "heatmap",
      name: String(year),
      coordinateSystem: "calendar",
      calendarIndex: i,
      data: byYear.get(year) ?? [],
    }));
    const values = y0.filter((v): v is number => typeof v === "number" && Number.isFinite(v));
    const option: Obj = {
      calendar: calendars,
      series,
      visualMap: {
        min: values.length ? minOf(values) : 0,
        max: values.length ? maxOf(values) : 1,
        calculable: true,
        orient: "horizontal",
        left: "center",
        bottom: 0,
        inRange: { color: t.sequential },
        textStyle: { color: t.textMuted },
      },
      tooltip: { trigger: "item" },
    };
    return applyStyle(option, spec, theme, undefined, { defaultTrigger: "item" });
  },
  warnings(spec: ChartSpec, data: ChartData): string[] {
    const xs = (data.columns.x ?? []) as number[];
    const years = yearsPresent(xs);
    const notices = serverWarnings(spec, data);
    if (years.length > MAX_YEARS) {
      const hidden = years.length - MAX_YEARS;
      notices.push(
        `Showing the ${MAX_YEARS} most recent years; ${hidden} earlier year(s) not shown.`,
      );
    }
    return notices;
  },
  recommend(columns) {
    const temporal = columns.some((c) => c.semantic === "temporal");
    return temporal ? 0.2 : 0.02;
  },
});
