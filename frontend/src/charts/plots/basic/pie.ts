import type { ChartSpec } from "../../../api/types";
import { makeFormatter } from "../../core/format";
import { categoryLabel, type Obj, param, resolveStyle } from "../../core/series";
import { applyStyle } from "../../core/style";
import {
  type ChartData,
  type ChartTheme,
  definePlot,
  type PlotEntry,
  type RenderContext,
  type StyleSection,
} from "../../core/types";

const SECTIONS: StyleSection[] = [
  "title",
  "legend",
  "labels",
  "tooltip",
  "numberFormat",
  "palette",
];

interface Slice {
  name: string;
  value: number;
}

function slices(data: ChartData, spec: ChartSpec, locale?: string): Slice[] {
  const xs = data.columns.x ?? [];
  const ys = data.columns.y0 ?? [];
  const sorted = param(spec, "sortSlices", true);
  const maxSlices = Math.max(1, Math.floor(param(spec, "maxSlices", 12)));
  const out: Slice[] = [];
  xs.forEach((x, i) => {
    const value = ys[i];
    // Pie slices must be positive; null and negative values have no angle to draw.
    if (typeof value === "number" && value > 0) {
      out.push({ name: categoryLabel(x, data.kinds.x, null, locale), value });
    }
  });
  if (sorted) out.sort((a, b) => b.value - a.value);
  if (out.length <= maxSlices) return out;
  const head = out.slice(0, maxSlices - 1);
  const rest = out.slice(maxSlices - 1).reduce((sum, s) => sum + s.value, 0);
  return [...head, { name: "Other", value: rest }];
}

/** Slices need a positive size; say how many rows could not be drawn. */
function pieWarnings(_spec: ChartSpec, data: ChartData): string[] {
  let negative = 0;
  let zero = 0;
  let missing = 0;
  for (const v of data.columns.y0 ?? []) {
    if (typeof v !== "number") missing += 1;
    else if (v < 0) negative += 1;
    else if (v === 0) zero += 1;
  }
  const parts: string[] = [];
  if (negative) parts.push(`${negative.toLocaleString()} negative`);
  if (zero) parts.push(`${zero.toLocaleString()} zero`);
  if (missing) parts.push(`${missing.toLocaleString()} missing`);
  return parts.length
    ? [
        `Not shown as slices: ${parts.join(", ")} value${negative + zero + missing === 1 ? "" : "s"}.`,
      ]
    : [];
}

function renderPie(
  donut: boolean,
  spec: ChartSpec,
  data: ChartData,
  theme: ChartTheme,
  ctx?: RenderContext,
): Obj {
  const style = resolveStyle(spec);
  const items = slices(data, spec, ctx?.locale);
  const inner = donut ? Math.min(90, Math.max(10, param(spec, "innerRadius", 55))) : 0;
  const labelMode = param(spec, "labelMode", "percent");
  const fmt = makeFormatter(style.numberFormat ?? "auto", ctx?.locale);
  const total = items.reduce((sum, s) => sum + s.value, 0);

  const option: Obj = {
    legend: { data: items.map((s) => s.name) },
    tooltip: { trigger: "item" },
    series: [
      {
        type: "pie",
        name: data.meta.fields.y0 ?? "Value",
        radius: [`${inner}%`, "68%"],
        center: ["50%", "50%"],
        data: items,
        itemStyle: { borderColor: theme.background ?? "transparent", borderWidth: 1 },
        label: {
          show: style.showLabels,
          color: theme.text,
          formatter: (p: { name: string; value: number; percent: number }) =>
            labelMode === "name"
              ? p.name
              : labelMode === "value"
                ? `${p.name}: ${fmt(p.value)}`
                : `${p.name}: ${p.percent.toFixed(1)}%`,
        },
        labelLine: { show: style.showLabels },
        emphasis: { scaleSize: 4 },
        startAngle: param(spec, "startAngle", 90),
      },
    ],
  };
  // Donut total in the middle of the hole (the pie is centred, so the graphic is too).
  if (donut && param(spec, "showTotal", true) && total > 0) {
    option.graphic = {
      type: "text",
      left: "center",
      top: "middle",
      silent: true,
      style: {
        text: fmt(total),
        fill: theme.text,
        font: `600 18px ${theme.fontFamily}`,
        textAlign: "center",
        textVerticalAlign: "middle",
      },
    };
  }
  return applyStyle(option, spec, theme, ctx, { defaultTrigger: "item", colorCount: items.length });
}

function pieEntry(donut: boolean): PlotEntry {
  return definePlot({
    key: donut ? "donut" : "pie",
    label: donut ? "Donut chart" : "Pie chart",
    family: "basic",
    description: donut
      ? "Parts of a whole with a hollow centre."
      : "Parts of a whole. Best with few categories.",
    encodingsSchema: [
      {
        name: "x",
        label: "Slice",
        required: true,
        multiple: false,
        accepts: ["nominal", "ordinal"],
        aggregate: false,
        sort: true,
      },
      {
        name: "y",
        label: "Value",
        required: false,
        multiple: false,
        accepts: ["quantitative"],
        aggregate: true,
        hint: "Leave empty to count rows.",
      },
    ],
    paramsSchema: [
      {
        name: "labelMode",
        type: "select",
        label: "Slice labels show",
        default: "percent",
        options: [
          { value: "percent", label: "Name and percent" },
          { value: "value", label: "Name and value" },
          { value: "name", label: "Name only" },
        ],
      },
      { name: "sortSlices", type: "boolean", label: "Sort slices by size", default: true },
      {
        name: "maxSlices",
        type: "int",
        label: "Max slices (rest grouped as Other)",
        min: 2,
        max: 50,
        default: 12,
      },
      ...(donut
        ? ([
            {
              name: "innerRadius",
              type: "number",
              label: "Hole size (%)",
              min: 10,
              max: 90,
              step: 5,
              default: 55,
            },
            { name: "showTotal", type: "boolean", label: "Show total in the hole", default: true },
          ] as PlotEntry["paramsSchema"])
        : []),
    ],
    styleSections: SECTIONS,
    defaults: {
      params: donut
        ? {
            labelMode: "percent",
            sortSlices: true,
            maxSlices: 12,
            innerRadius: 55,
            showTotal: true,
          }
        : { labelMode: "percent", sortSlices: true, maxSlices: 12 },
      style: { showLabels: true },
    },
    render: (spec, data, theme, ctx) => renderPie(donut, spec, data, theme, ctx),
    warnings: pieWarnings,
    recommend(columns) {
      const small = columns.some((c) => c.semantic === "nominal" && c.approxDistinct <= 7);
      const measure = columns.some((c) => c.semantic === "quantitative");
      return small && measure ? (donut ? 0.5 : 0.55) : 0.05;
    },
  });
}

export const pie = pieEntry(false);
export const donut = pieEntry(true);
