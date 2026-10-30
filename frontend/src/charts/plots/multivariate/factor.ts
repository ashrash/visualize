import { HeatmapChart } from "echarts/charts";
import { echarts } from "../../core/echarts";
import { divergingRamp } from "../../core/palettes";
import { param } from "../../core/series";
import { applyStyle } from "../../core/style";
import { definePlot } from "../../core/types";
import { colorsFor, count, EMPTY, fmt, list, nums, QUANT, statValues, strs } from "./stat";

echarts.use([HeatmapChart]);

export const factorLoading = definePlot({
  key: "factor_loading",
  label: "Factor loading plot",
  family: "multivariate",
  description: "How strongly each measure loads on each factor (factor analysis or PCA).",
  encodingsSchema: [
    {
      name: "y",
      label: "Measures (2+)",
      required: true,
      multiple: true,
      accepts: QUANT,
      aggregate: false,
    },
  ],
  paramsSchema: [
    {
      name: "method",
      affectsData: true,
      type: "select",
      label: "Method",
      default: "fa",
      options: [
        { value: "fa", label: "Factor analysis (maximum likelihood)" },
        { value: "pca", label: "Principal components" },
      ],
    },
    {
      name: "factors",
      affectsData: true,
      type: "int",
      label: "Factors",
      min: 1,
      max: 10,
      default: 2,
    },
    {
      name: "rotation",
      affectsData: true,
      type: "select",
      label: "Rotation",
      default: "varimax",
      options: [
        { value: "varimax", label: "Varimax" },
        { value: "quartimax", label: "Quartimax" },
        { value: "none", label: "None" },
      ],
    },
    {
      name: "display",
      affectsData: false,
      type: "select",
      label: "Display",
      default: "heatmap",
      options: [
        { value: "heatmap", label: "Heatmap" },
        { value: "bars", label: "Bars" },
      ],
    },
    {
      name: "showValues",
      affectsData: false,
      type: "boolean",
      label: "Show values",
      default: true,
    },
  ],
  styleSections: ["title", "legend", "tooltip", "palette"],
  defaults: {
    params: { method: "fa", factors: 2, rotation: "varimax", display: "heatmap", showValues: true },
  },
  render(spec, data, theme, ctx) {
    const v = statValues(data);
    if (!v) return applyStyle(EMPTY, spec, theme, ctx);
    const variables = strs(v.variables).map((s) => s ?? "");
    const factors = strs(v.factors).map((s) => s ?? "");
    const loadings = list(v.loadings).map((row) => nums(row));
    const explained = nums(v.explainedVarianceRatio);
    const factorNames = factors.map((f, j) =>
      explained[j] !== null && explained[j] !== undefined
        ? `${f} (${((explained[j] as number) * 100).toFixed(0)}%)`
        : f,
    );
    const communality = nums(v.communality);
    const showValues = param(spec, "showValues", true);
    // Variables read top to bottom: the first one sits at the top of the category axis.
    const rows = [...variables].reverse();
    const at = (vi: number, fi: number) => loadings[vi]?.[fi] ?? null;
    const tooltip = (vi: number, fi: number) =>
      `<b>${variables[vi]}</b><br/>${factorNames[fi]}: ${fmt(at(vi, fi))}` +
      `<br/>Communality: ${fmt(communality[vi] ?? null)}`;
    if (param(spec, "display", "heatmap") === "bars") {
      const colors = colorsFor(spec, theme, factors.length);
      const option = {
        legend: {},
        tooltip: { trigger: "item" },
        xAxis: { type: "value", name: "Loading", min: -1, max: 1 },
        yAxis: { type: "category", data: rows },
        series: factors.map((_, fi) => ({
          type: "bar",
          name: factorNames[fi],
          itemStyle: { color: colors[fi] },
          data: rows.map((_, r) => at(variables.length - 1 - r, fi)),
          label: showValues
            ? {
                show: true,
                position: "right",
                formatter: (p: { value: number }) => fmt(p.value, 2),
              }
            : undefined,
          tooltip: {
            formatter: (p: { dataIndex: number }) =>
              tooltip(variables.length - 1 - p.dataIndex, fi),
          },
        })),
      };
      return applyStyle(option, spec, theme, ctx, { orientable: false, defaultTrigger: "item" });
    }
    const cells: [number, number, number | null][] = [];
    variables.forEach((_, vi) => {
      factors.forEach((_, fi) => {
        cells.push([fi, variables.length - 1 - vi, at(vi, fi)]);
      });
    });
    const option = {
      tooltip: {
        trigger: "item",
        formatter: (p: { value: [number, number, number] }) =>
          tooltip(variables.length - 1 - p.value[1], p.value[0]),
      },
      xAxis: { type: "category", data: factorNames, splitArea: { show: false } },
      yAxis: { type: "category", data: rows },
      visualMap: {
        min: -1,
        max: 1,
        calculable: true,
        orient: "vertical",
        right: 4,
        top: "middle",
        inRange: { color: divergingRamp(theme) },
        textStyle: { color: theme.textMuted },
      },
      series: [
        {
          type: "heatmap",
          name: "Loading",
          data: cells,
          label: showValues
            ? {
                show: true,
                color: theme.text,
                formatter: (p: { value: [number, number, number | null] }) => fmt(p.value[2], 2),
              }
            : undefined,
          itemStyle: { borderColor: theme.background ?? "transparent", borderWidth: 1 },
        },
      ],
    };
    return applyStyle(option, spec, theme, ctx, { orientable: false, defaultTrigger: "item" });
  },
  recommend(columns) {
    return count(columns, "quantitative") >= 4 ? 0.25 : 0;
  },
});
