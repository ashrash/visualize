import { RadarChart } from "echarts/charts";
import { RadarComponent } from "echarts/components";
import { echarts } from "../../core/echarts";
import { maxOf, minOf } from "../../core/math";
import { param, resolveStyle } from "../../core/series";
import { applyStyle } from "../../core/style";
import { definePlot } from "../../core/types";
import { CATEGORICAL, count, escapeHtml, fmt, QUANT } from "./stat";

echarts.use([RadarChart, RadarComponent]);

const AGG_LABEL: Record<string, string> = {
  mean: "Mean",
  median: "Median",
  sum: "Sum",
  min: "Min",
  max: "Max",
};

export const radar = definePlot({
  key: "radar",
  label: "Radar chart",
  family: "multivariate",
  description: "Compare groups across several measures, each scaled to its own axis.",
  encodingsSchema: [
    {
      name: "y",
      label: "Measures (3-12)",
      required: true,
      multiple: true,
      accepts: QUANT,
      aggregate: false,
    },
    {
      name: "color",
      label: "Group",
      required: false,
      multiple: false,
      accepts: CATEGORICAL,
      aggregate: false,
    },
  ],
  paramsSchema: [
    {
      name: "agg",
      affectsData: true,
      type: "select",
      label: "Summary per group",
      default: "mean",
      options: Object.entries(AGG_LABEL).map(([value, label]) => ({ value, label })),
    },
    {
      name: "scale",
      affectsData: true,
      type: "select",
      label: "Axis scaling",
      help: "Scale each axis to 0-1 so measures in different units compare.",
      default: "column_range",
      options: [
        { value: "column_range", label: "Range of all rows" },
        { value: "group_range", label: "Across groups" },
        { value: "none", label: "None (raw values)" },
      ],
    },
    {
      name: "maxGroups",
      affectsData: true,
      type: "int",
      label: "Max groups",
      min: 1,
      max: 50,
      default: 12,
    },
    { name: "fill", affectsData: false, type: "boolean", label: "Fill areas", default: true },
  ],
  styleSections: ["title", "legend", "tooltip", "palette"],
  defaults: { params: { agg: "mean", scale: "column_range", maxGroups: 12, fill: true } },
  render(spec, data, theme, ctx) {
    const style = resolveStyle(spec);
    const measures = Object.keys(data.columns)
      .filter((k) => /^y\d+$/.test(k))
      .sort((a, b) => Number(a.slice(1)) - Number(b.slice(1)));
    const scale = param(spec, "scale", "column_range");
    const agg = AGG_LABEL[param(spec, "agg", "mean")] ?? "Mean";
    const scaled = scale !== "none";
    const groups = (data.columns.color ?? ["All"]).map((g) =>
      g === null ? "(missing)" : String(g),
    );
    const value = (k: string, i: number) => {
      const v = data.columns[scaled ? `${k}_norm` : k]?.[i];
      return typeof v === "number" ? v : null;
    };
    const indicator = measures.map((k) => {
      const raw = (data.columns[k] ?? []).filter((v): v is number => typeof v === "number");
      const lo = scaled ? 0 : Math.min(0, minOf(raw, 0));
      const hi = scaled ? 1 : maxOf(raw, 1) * 1.1 || 1;
      return { name: data.meta.fields[k] ?? k, min: lo, max: hi };
    });
    const fill = param(spec, "fill", true);
    const option = {
      legend: data.columns.color ? { data: groups } : undefined,
      radar: {
        indicator,
        radius: "65%",
        center: ["50%", style.legend.show && data.columns.color ? "55%" : "50%"],
        axisName: { color: theme.textMuted },
        splitLine: { lineStyle: { color: theme.grid ?? theme.border } },
        splitArea: { show: false },
        axisLine: { lineStyle: { color: theme.axis ?? theme.border } },
      },
      tooltip: { trigger: "item" },
      series: [
        {
          type: "radar",
          name: agg,
          // One color per group (data item), matching the legend.
          colorBy: "data",
          symbolSize: 5,
          areaStyle: fill ? { opacity: 0.15 } : undefined,
          data: groups.map((name, i) => ({
            name,
            value: measures.map((k) => value(k, i)),
            raw: measures.map((k) => data.columns[k]?.[i] ?? null),
          })),
          tooltip: {
            formatter: (p: { name: string; data: { raw: unknown[] } }) =>
              [
                // Group values and field names are user data: escaped.
                `<b>${escapeHtml(p.name)}</b>`,
                ...measures.map(
                  (k, j) =>
                    `${agg} ${escapeHtml(data.meta.fields[k] ?? k)}: ` +
                    fmt(p.data.raw[j] as number | null),
                ),
              ].join("<br/>"),
          },
        },
      ],
    };
    return applyStyle(option, spec, theme, ctx, {
      defaultTrigger: "item",
      colorCount: groups.length,
    });
  },
  recommend(columns) {
    const q = count(columns, "quantitative");
    return q >= 3 && columns.some((c) => c.semantic === "nominal" && c.approxDistinct <= 8)
      ? 0.35
      : 0.05;
  },
});
