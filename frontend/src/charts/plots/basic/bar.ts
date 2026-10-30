import { formatValue } from "../../../api/format";
import { axisStyle, baseOption } from "../../core/theme";
import type { ChartData, PlotEntry } from "../../core/types";

interface Series {
  name: string;
  data: unknown[];
}

function seriesFor(data: ChartData): { categories: string[]; series: Series[] } {
  const { columns, kinds, meta } = data;
  const xs = columns.x ?? [];
  const label = (value: unknown) => formatValue(value, kinds.x) || "(blank)";

  const colors = columns.color;
  if (colors) {
    // One series per color value, pivoted onto the shared category axis.
    const categories = [...new Set(xs.map(label))];
    const groups = [...new Set(colors.map((c) => formatValue(c, kinds.color) || "(blank)"))];
    const values = columns.y0 ?? [];
    const lookup = new Map<string, unknown>();
    xs.forEach((x, i) => {
      lookup.set(`${label(x)}\u0000${formatValue(colors[i], kinds.color) || "(blank)"}`, values[i]);
    });
    return {
      categories,
      series: groups.map((group) => ({
        name: group,
        data: categories.map((c) => lookup.get(`${c}\u0000${group}`) ?? null),
      })),
    };
  }

  const yKeys = Object.keys(columns).filter((key) => /^y\d+$/.test(key));
  return {
    categories: xs.map(label),
    series: yKeys.map((key) => ({ name: meta.fields[key] ?? key, data: columns[key] ?? [] })),
  };
}

export const bar: PlotEntry = {
  key: "bar",
  label: "Bar chart",
  family: "basic",
  channels: [
    {
      name: "x",
      label: "Category",
      required: true,
      multiple: false,
      accepts: ["nominal", "ordinal", "temporal", "quantitative"],
      aggregate: false,
    },
    {
      name: "y",
      label: "Value",
      required: false,
      multiple: true,
      accepts: ["quantitative"],
      aggregate: true,
    },
    {
      name: "color",
      label: "Color",
      required: false,
      multiple: false,
      accepts: ["nominal", "ordinal"],
      aggregate: false,
    },
  ],

  render(spec, data, theme) {
    const { categories, series } = seriesFor(data);
    const style = spec.style;
    const horizontal = style?.orientation === "horizontal";
    const stacked = style?.stack !== undefined && style.stack !== "none";
    const showLegend = series.length > 1;
    const valueName = series.length === 1 ? series[0]?.name : undefined;

    // Axis titles sit centred alongside the axis (the default end position gets clipped).
    const categoryAxis = {
      type: "category",
      data: categories,
      name: data.meta.fields.x,
      nameLocation: "middle",
      nameGap: horizontal ? 56 : 28,
      ...axisStyle(theme),
    };
    const valueAxis = {
      type: style?.yAxes?.[0]?.scale === "log" ? "log" : "value",
      name: valueName,
      ...axisStyle(theme),
      // Left-align the title above a vertical value axis so it isn't clipped at the edge.
      nameTextStyle: { color: theme.textMuted, align: horizontal ? "right" : "left" },
    };

    return {
      ...baseOption(theme),
      title: style?.title
        ? {
            text: style.title,
            subtext: style.subtitle ?? undefined,
            textStyle: { color: theme.text },
          }
        : undefined,
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      legend: showLegend ? { top: 0, textStyle: { color: theme.text } } : undefined,
      grid: {
        left: horizontal ? 40 : 8,
        right: 24,
        top: showLegend ? 36 : 24,
        bottom: horizontal ? 8 : 28,
        containLabel: true,
      },
      xAxis: horizontal ? valueAxis : categoryAxis,
      yAxis: horizontal ? categoryAxis : valueAxis,
      series: series.map((s) => ({
        type: "bar",
        name: s.name,
        data: s.data,
        stack: stacked ? "total" : undefined,
        emphasis: { focus: "series" },
        label: style?.showLabels ? { show: true, color: theme.textMuted } : undefined,
      })),
    };
  },

  recommend(columns) {
    const hasCategory = columns.some((c) => c.semantic === "nominal" && c.approxDistinct <= 50);
    const hasMeasure = columns.some((c) => c.semantic === "quantitative");
    return hasCategory && hasMeasure ? 0.8 : hasCategory ? 0.5 : 0.1;
  },
};
