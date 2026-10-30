import { echarts } from "../../core/echarts";
import { makeFormatter } from "../../core/format";
import { categoryLabel, type Obj } from "../../core/series";
import { applyStyle } from "../../core/style";
import {
  type ChannelSchema,
  type ChartData,
  type ChartTheme,
  definePlot,
  type StyleSection,
} from "../../core/types";

const SECTIONS: StyleSection[] = ["title", "legend", "axes", "labels", "tooltip", "palette"];

const encodingsSchema: ChannelSchema[] = [
  {
    name: "label",
    label: "Task",
    required: true,
    multiple: false,
    accepts: ["nominal", "ordinal"],
    aggregate: false,
  },
  {
    name: "start",
    label: "Start",
    required: true,
    multiple: false,
    accepts: ["temporal"],
    aggregate: false,
  },
  {
    name: "end",
    label: "End",
    required: true,
    multiple: false,
    accepts: ["temporal"],
    aggregate: false,
  },
  {
    name: "color",
    label: "Color by",
    required: false,
    multiple: false,
    accepts: ["nominal", "ordinal"],
    aggregate: false,
  },
  {
    name: "progress",
    label: "% complete",
    required: false,
    multiple: false,
    accepts: ["quantitative"],
    aggregate: false,
  },
];

interface RenderApi {
  value(dim: number): number | string;
  coord(point: [number, number]): [number, number];
  size(point: [number, number]): [number, number];
}

function toMs(v: unknown): number {
  if (typeof v === "number") return v;
  const t = Date.parse(String(v));
  return Number.isNaN(t) ? 0 : t;
}

export const gantt = definePlot({
  key: "gantt",
  label: "Gantt chart",
  family: "specialized",
  description: "Tasks as bars on a time axis.",
  encodingsSchema,
  paramsSchema: [
    {
      name: "maxTasks",
      label: "Max tasks",
      affectsData: true,
      type: "int",
      min: 1,
      max: 5000,
      default: 500,
    },
  ],
  styleSections: SECTIONS,
  // Earliest task at the top, like most Gantt tools (the generic y-axis handling only inverts
  // when the style says so, so this is a default the user can still change).
  defaults: { params: { maxTasks: 500 }, style: { yAxes: [{ grid: true, inverse: true }] } },
  render(spec, data: ChartData, theme: ChartTheme, ctx) {
    const labels = data.columns.label ?? [];
    const starts = data.columns.start ?? [];
    const ends = data.columns.end ?? [];
    const colorCol = data.columns.color;
    const progressCol = data.columns.progress;
    const n = data.rowCount;
    const categories = labels.map((l) => categoryLabel(l, data.kinds.label, null, ctx?.locale));

    const colorOf = new Map<unknown, string>();
    const palette = theme.palette;
    if (colorCol) {
      for (const c of colorCol) {
        if (!colorOf.has(c)) colorOf.set(c, palette[colorOf.size % palette.length] ?? theme.text);
      }
    }
    const items = Array.from({ length: n }, (_, i) => ({
      task: categories[i] ?? "",
      start: toMs(starts[i]),
      end: toMs(ends[i]),
      color: colorCol ? (colorOf.get(colorCol[i]) ?? theme.text) : (palette[0] ?? theme.text),
      progress:
        progressCol && typeof progressCol[i] === "number" ? (progressCol[i] as number) : null,
      colorLabel: colorCol ? categoryLabel(colorCol[i], data.kinds.color, null, ctx?.locale) : null,
    }));

    const series: Obj = {
      type: "custom",
      name: "tasks",
      // [categoryIndex, startMs, endMs]: real dimensions, so ECharts computes the time axis's
      // extent from the data (plain row indices here left the x axis with no real range, and
      // every bar landed off-canvas near 1970-01-01).
      data: items.map((item, i) => [i, item.start, item.end]),
      encode: { x: [1, 2], y: 0 },
      renderItem(params: { dataIndex: number }, api: RenderApi) {
        const item = items[params.dataIndex];
        if (!item) return { type: "group", children: [] };
        const [x0, y] = api.coord([item.start, params.dataIndex]);
        const [x1] = api.coord([item.end, params.dataIndex]);
        const [, barHeight] = api.size([0, 1]);
        const height = Math.max(4, barHeight * 0.55);
        const width = Math.max(1, x1 - x0);
        const children: Obj[] = [
          {
            type: "rect",
            shape: { x: x0, y: y - height / 2, width, height, r: 2 },
            style: { fill: item.color, opacity: 0.85 },
          },
        ];
        if (item.progress !== null) {
          children.push({
            type: "rect",
            shape: {
              x: x0,
              y: y - height / 2,
              width: width * Math.min(1, Math.max(0, item.progress / 100)),
              height,
              r: 2,
            },
            style: { fill: item.color, opacity: 1 },
          });
        }
        return { type: "group", children };
      },
    };

    const times = items.flatMap((item) => [item.start, item.end]).filter(Number.isFinite);
    const minTime = times.length ? Math.min(...times) : undefined;
    const maxTime = times.length ? Math.max(...times) : undefined;
    const fmtDate = makeFormatter("date", ctx?.locale);
    const safe = echarts.format.encodeHTML;

    const option: Obj = {
      // min/max are set explicitly (not left to auto-scale from the custom series): a custom
      // series with no "real" axis-range data item would otherwise leave the time axis at its
      // default [0, 1] ms extent, pushing every bar off-canvas.
      xAxis: { type: "time", min: minTime, max: maxTime },
      yAxis: { type: "category", data: categories },
      tooltip: {
        formatter: (p: { dataIndex: number }) => {
          const item = items[p.dataIndex];
          if (!item) return "";
          const range = `${fmtDate(item.start)} to ${fmtDate(item.end)}`;
          const colorPart = item.colorLabel ? `<br/>${safe(item.colorLabel)}` : "";
          const progressPart = item.progress !== null ? `<br/>${item.progress}% complete` : "";
          return `${safe(item.task)}<br/>${range}${colorPart}${progressPart}`;
        },
      },
      series: [series],
    };
    return applyStyle(option, spec, theme, ctx, { orientable: false, defaultTrigger: "item" });
  },
  recommend(columns) {
    const temporal = columns.filter((c) => c.semantic === "temporal").length >= 2;
    const hasLabel = columns.some((c) => c.semantic === "nominal" || c.semantic === "ordinal");
    return temporal && hasLabel ? 0.3 : 0.03;
  },
});
