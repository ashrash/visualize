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

interface RenderApi {
  coord(point: [number, number]): [number, number];
}

function buildRects(data: ChartData, colors: string[], locale?: string): Rect[] {
  const xs = data.columns.x ?? [];
  const colorCol = data.columns.color ?? [];
  const size = data.columns.size ?? [];
  const xShare = data.columns.xShare ?? [];
  const share = data.columns.share ?? [];
  const n = data.rowCount;

  const xOrder: unknown[] = [];
  const xLeft = new Map<unknown, number>();
  const xWidth = new Map<unknown, number>();
  const colorIndex = new Map<unknown, number>();
  let cursor = 0;
  for (let i = 0; i < n; i++) {
    const x = xs[i];
    if (!xLeft.has(x)) {
      const w = (typeof xShare[i] === "number" ? (xShare[i] as number) : 0) * 100;
      xLeft.set(x, cursor);
      xWidth.set(x, w);
      xOrder.push(x);
      cursor += w;
    }
    const c = colorCol[i];
    if (!colorIndex.has(c)) colorIndex.set(c, colorIndex.size);
  }

  const topByX = new Map<unknown, number>();
  const rects: Rect[] = [];
  for (let i = 0; i < n; i++) {
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
  return rects;
}

function render(
  spec: Parameters<typeof applyStyle>[1],
  data: ChartData,
  theme: ChartTheme,
  ctx?: Parameters<typeof applyStyle>[3],
): Obj {
  const palette = theme.palette;
  const rects = buildRects(data, palette, ctx?.locale);
  const colorLabels: string[] = [];
  const colorByLabel = new Map<string, string>();
  for (const r of rects) {
    if (!colorByLabel.has(r.colorLabel)) {
      colorByLabel.set(r.colorLabel, r.color);
      colorLabels.push(r.colorLabel);
    }
  }

  const series: Obj = {
    type: "custom",
    name: "mosaic",
    data: rects.map((_r, i) => i),
    renderItem(params: { dataIndex: number }, api: RenderApi) {
      const r = rects[params.dataIndex];
      if (!r) return { type: "group", children: [] };
      const [x0, y0] = api.coord([r.left, r.top + r.height]);
      const [x1, y1] = api.coord([r.left + r.width, r.top]);
      return {
        type: "rect",
        shape: {
          x: Math.min(x0, x1),
          y: Math.min(y0, y1),
          width: Math.abs(x1 - x0),
          height: Math.abs(y1 - y0),
        },
        style: { fill: r.color, stroke: theme.background ?? "#fff", lineWidth: 1 },
      };
    },
    tooltip: {
      formatter: (p: { dataIndex: number }) => {
        const r = rects[p.dataIndex];
        return r ? `${r.label} / ${r.colorLabel}: ${r.value.toLocaleString()}` : "";
      },
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
    series: [series],
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
