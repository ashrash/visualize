import { echarts } from "../../core/echarts";
import { categoryLabel, type Obj } from "../../core/series";
import { applyStyle } from "../../core/style";
import {
  type ChannelSchema,
  type ChartData,
  type ChartTheme,
  definePlot,
  type StyleSection,
} from "../../core/types";

const SECTIONS: StyleSection[] = ["title", "legend", "labels", "tooltip", "palette"];

const encodingsSchema: ChannelSchema[] = [
  {
    name: "x",
    label: "Columns",
    required: true,
    multiple: false,
    accepts: ["nominal", "ordinal"],
    aggregate: false,
  },
  {
    name: "color",
    label: "Segments",
    required: true,
    multiple: false,
    accepts: ["nominal", "ordinal"],
    aggregate: false,
  },
  {
    name: "size",
    label: "Size",
    required: false,
    multiple: false,
    accepts: ["quantitative"],
    aggregate: true,
    hint: "Leave empty to count rows.",
  },
];

const paramsSchema = [
  {
    name: "maxX",
    label: "Max columns",
    affectsData: true,
    type: "int" as const,
    min: 1,
    max: 200,
    default: 20,
  },
  {
    name: "maxColor",
    label: "Max segments",
    affectsData: true,
    type: "int" as const,
    min: 1,
    max: 200,
    default: 20,
  },
];

interface Rect {
  left: number;
  top: number;
  width: number;
  height: number;
  color: string;
  label: string;
  colorLabel: string;
  value: number;
}

interface Column {
  x: unknown;
  label: string;
  left: number;
  width: number;
}

interface RenderApi {
  coord(point: [number, number]): [number, number];
}

function buildLayout(
  data: ChartData,
  colors: string[],
  locale?: string,
): { rects: Rect[]; columns: Column[] } {
  const xs = data.columns.x ?? [];
  const colorCol = data.columns.color ?? [];
  const size = data.columns.size ?? [];
  const xShare = data.columns.xShare ?? [];
  const share = data.columns.share ?? [];
  const n = data.rowCount;

  // Totals per x and per color, so columns/segments sort by size (stable across refreshes)
  // instead of the server's group-by order, which is not guaranteed.
  const xTotal = new Map<unknown, number>();
  const colorTotal = new Map<unknown, number>();
  for (let i = 0; i < n; i++) {
    const v = typeof size[i] === "number" ? (size[i] as number) : 0;
    xTotal.set(xs[i], (xTotal.get(xs[i]) ?? 0) + v);
    colorTotal.set(colorCol[i], (colorTotal.get(colorCol[i]) ?? 0) + v);
  }
  const xOrder = [...xTotal.keys()].sort((a, b) => (xTotal.get(b) ?? 0) - (xTotal.get(a) ?? 0));
  const colorOrder = [...colorTotal.keys()].sort(
    (a, b) => (colorTotal.get(b) ?? 0) - (colorTotal.get(a) ?? 0),
  );

  const xLeft = new Map<unknown, number>();
  const xWidth = new Map<unknown, number>();
  let cursor = 0;
  const columns: Column[] = [];
  for (const x of xOrder) {
    let w = 0;
    for (let i = 0; i < n; i++) {
      if (xs[i] === x && typeof xShare[i] === "number") {
        w = (xShare[i] as number) * 100;
        break;
      }
    }
    xLeft.set(x, cursor);
    xWidth.set(x, w);
    columns.push({
      x,
      label: categoryLabel(x, data.kinds.x, null, locale),
      left: cursor,
      width: w,
    });
    cursor += w;
  }
  const colorIndex = new Map(colorOrder.map((c, i) => [c, i]));

  const topByX = new Map<unknown, number>();
  // Draw in colorOrder within each x so segments stack in a stable, consistent order.
  const rows = Array.from({ length: n }, (_v, i) => i).sort((a, b) => {
    const xa = xOrder.indexOf(xs[a]);
    const xb = xOrder.indexOf(xs[b]);
    if (xa !== xb) return xa - xb;
    return (colorIndex.get(colorCol[a]) ?? 0) - (colorIndex.get(colorCol[b]) ?? 0);
  });
  const rects: Rect[] = [];
  for (const i of rows) {
    const x = xs[i];
    const top = topByX.get(x) ?? 0;
    const h = (typeof share[i] === "number" ? (share[i] as number) : 0) * 100;
    rects.push({
      left: xLeft.get(x) ?? 0,
      top,
      width: xWidth.get(x) ?? 0,
      height: h,
      color: colors[(colorIndex.get(colorCol[i]) ?? 0) % colors.length] ?? "#999",
      label: categoryLabel(x, data.kinds.x, null, locale),
      colorLabel: categoryLabel(colorCol[i], data.kinds.color, null, locale),
      value: typeof size[i] === "number" ? (size[i] as number) : 0,
    });
    topByX.set(x, top + h);
  }
  return { rects, columns };
}

function render(
  spec: Parameters<typeof applyStyle>[1],
  data: ChartData,
  theme: ChartTheme,
  ctx?: Parameters<typeof applyStyle>[3],
): Obj {
  const palette = theme.palette;
  const { rects, columns } = buildLayout(data, palette, ctx?.locale);
  const colorLabels: string[] = [];
  const colorByLabel = new Map<string, string>();
  for (const r of rects) {
    if (!colorByLabel.has(r.colorLabel)) {
      colorByLabel.set(r.colorLabel, r.color);
      colorLabels.push(r.colorLabel);
    }
  }

  const safe = echarts.format.encodeHTML;
  const cellSeries: Obj = {
    type: "custom",
    name: "mosaic",
    data: rects.map((_r, i) => i),
    tooltip: {
      formatter: (p: { dataIndex: number }) => {
        const r = rects[p.dataIndex];
        return r ? `${safe(r.label)} / ${safe(r.colorLabel)}: ${r.value.toLocaleString()}` : "";
      },
    },
    renderItem(params: { dataIndex: number }, api: RenderApi) {
      const r = rects[params.dataIndex];
      if (!r) return { type: "group", children: [] };
      const [x0, y0] = api.coord([r.left, r.top + r.height]);
      const [x1, y1] = api.coord([r.left + r.width, r.top]);
      const w = Math.abs(x1 - x0);
      const h = Math.abs(y1 - y0);
      const children: Obj[] = [
        {
          type: "rect",
          shape: { x: Math.min(x0, x1), y: Math.min(y0, y1), width: w, height: h },
          style: { fill: r.color, stroke: theme.background ?? "#fff", lineWidth: 1 },
        },
      ];
      // A segment label when there's room for it; text drawn on canvas is never HTML, so this
      // (unlike the tooltip) needs no escaping.
      if (w > 36 && h > 16) {
        children.push({
          type: "text",
          x: Math.min(x0, x1) + w / 2,
          y: Math.min(y0, y1) + h / 2,
          style: {
            text: r.colorLabel,
            fill: theme.background ?? "#fff",
            align: "center",
            verticalAlign: "middle",
            fontSize: 11,
            overflow: "truncate",
            width: w - 6,
          },
        });
      }
      return { type: "group", children };
    },
  };

  const labelSeries: Obj = {
    type: "custom",
    name: "columns",
    silent: true,
    tooltip: { show: false },
    data: columns.map((_c, i) => i),
    renderItem(params: { dataIndex: number }, api: RenderApi) {
      const c = columns[params.dataIndex];
      if (!c) return { type: "group", children: [] };
      const [x0] = api.coord([c.left, 0]);
      const [x1, yTop] = api.coord([c.left + c.width, 100]);
      return {
        type: "text",
        x: (x0 + x1) / 2,
        y: yTop - 6,
        style: {
          text: c.label,
          fill: theme.text,
          align: "center",
          verticalAlign: "bottom",
          fontSize: 11,
          overflow: "truncate",
          width: Math.abs(x1 - x0),
        },
      };
    },
  };

  const option: Obj = {
    xAxis: { type: "value", min: 0, max: 100, show: false },
    yAxis: { type: "value", min: 0, max: 100, show: false },
    legend: {
      selectedMode: false,
      data: colorLabels.map((name) => ({ name, itemStyle: { color: colorByLabel.get(name) } })),
    },
    tooltip: { trigger: "item" },
    series: [cellSeries, labelSeries],
  };
  return applyStyle(option, spec, theme, ctx, {
    axes: false,
    defaultTrigger: "item",
    colorCount: colorLabels.length,
  });
}

export const mosaic = definePlot({
  key: "mosaic",
  label: "Mosaic plot",
  family: "composition",
  description: "A crosstab of two categories, drawn as proportional rectangles.",
  encodingsSchema,
  paramsSchema,
  styleSections: SECTIONS,
  defaults: { params: { maxX: 20, maxColor: 20 } },
  render,
  recommend(columns) {
    const categorical = columns.filter((c) => c.semantic === "nominal" || c.semantic === "ordinal");
    return categorical.length >= 2 ? 0.25 : 0.03;
  },
});

export const marimekko = definePlot({
  key: "marimekko",
  label: "Marimekko chart",
  family: "composition",
  description: "A stacked-bar crosstab where each column's width also carries meaning.",
  encodingsSchema,
  paramsSchema,
  styleSections: SECTIONS,
  defaults: { params: { maxX: 20, maxColor: 20 } },
  render,
  recommend(columns) {
    const categorical = columns.filter((c) => c.semantic === "nominal" || c.semantic === "ordinal");
    return categorical.length >= 2 ? 0.2 : 0.03;
  },
});
