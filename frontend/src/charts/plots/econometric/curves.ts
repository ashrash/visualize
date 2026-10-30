import { refLine } from "../../core/primitives/refLine";
import { param } from "../../core/series";
import { applyStyle } from "../../core/style";
import { definePlot } from "../../core/types";
import {
  ANY,
  binaryColumn,
  CATEGORICAL,
  colorsFor,
  count,
  EMPTY,
  fmt,
  hasName,
  num,
  nums,
  obj,
  objs,
  QUANT,
  statValues,
  str,
} from "../multivariate/stat";

type Obj = Record<string, unknown>;

const pairs = (xs: (number | null)[], ys: (number | null)[]) =>
  xs.map((x, i) => [x, ys[i] ?? null]);

const unitAxis = (name: string) => ({ type: "value", name, min: 0, max: 1 });

const SCORE_CHANNELS = [
  {
    name: "truth",
    label: "Actual outcome",
    required: true,
    multiple: false,
    accepts: ANY,
    aggregate: false,
    hint: "Two values, such as 0/1 or yes/no.",
  },
  {
    name: "score",
    label: "Scores (one curve each)",
    required: true,
    multiple: true,
    accepts: QUANT,
    aggregate: false,
  },
];

const POSITIVE = {
  name: "positive",
  affectsData: true,
  type: "text" as const,
  label: "Positive value (blank = automatic)",
  default: "",
};

function diagonal(color: string) {
  const line = refLine({ axis: "diagonal", extent: [0, 1], style: "dashed", color });
  return { ...line.series, name: undefined };
}

export const roc = definePlot({
  key: "roc",
  label: "ROC curve",
  family: "econometric",
  description: "True vs false positive rate across thresholds, with AUC and its interval.",
  encodingsSchema: SCORE_CHANNELS,
  paramsSchema: [
    POSITIVE,
    {
      name: "ciLevel",
      affectsData: true,
      type: "number",
      label: "AUC confidence level",
      min: 0.5,
      max: 0.999,
      step: 0.01,
      default: 0.95,
    },
    {
      name: "showDiagonal",
      affectsData: false,
      type: "boolean",
      label: "Chance line",
      default: true,
    },
    {
      name: "showBest",
      affectsData: false,
      type: "boolean",
      label: "Mark best threshold (Youden)",
      default: true,
    },
  ],
  styleSections: ["title", "legend", "tooltip", "palette", "annotations"],
  defaults: { params: { positive: "", ciLevel: 0.95, showDiagonal: true, showBest: true } },
  render(spec, data, theme, ctx) {
    const v = statValues(data);
    if (!v) return applyStyle(EMPTY, spec, theme, ctx);
    const curves = objs(v.curves);
    const colors = colorsFor(spec, theme, curves.length);
    const showBest = param(spec, "showBest", true);
    const series: Obj[] = curves.map((c, i) => {
      const best = obj(c.best);
      return {
        type: "line",
        name: `${str(c.score) ?? ""} (AUC ${fmt(num(c.auc))} [${fmt(num(c.aucLow))}, ${fmt(num(c.aucHigh))}])`,
        symbol: "none",
        data: pairs(nums(c.fpr), nums(c.tpr)),
        lineStyle: { width: 2, color: colors[i] },
        itemStyle: { color: colors[i] },
        markPoint: showBest
          ? {
              symbol: "circle",
              symbolSize: 9,
              label: { show: false },
              data: [
                {
                  coord: [num(best.fpr), num(best.tpr)],
                  name: `Best threshold ${fmt(num(best.threshold))}`,
                  value: fmt(num(best.youden)),
                },
              ],
              tooltip: {
                formatter: (p: { name: string; value: string }) =>
                  `${p.name}<br/>Youden J = ${p.value}`,
              },
            }
          : undefined,
      };
    });
    if (param(spec, "showDiagonal", true)) series.push(diagonal(theme.textMuted));
    const option = {
      legend: {},
      xAxis: unitAxis("False positive rate (1 - specificity)"),
      yAxis: unitAxis("True positive rate (sensitivity)"),
      tooltip: { trigger: "item" },
      series,
    };
    return applyStyle(option, spec, theme, ctx, { orientable: false, defaultTrigger: "item" });
  },
  recommend(columns) {
    return binaryColumn(columns) && hasName(columns, /score|prob|pred/i) ? 0.55 : 0;
  },
});

export const precisionRecall = definePlot({
  key: "precision_recall",
  label: "Precision-recall curve",
  family: "econometric",
  description: "Precision vs recall across thresholds, with average precision.",
  encodingsSchema: SCORE_CHANNELS,
  paramsSchema: [
    POSITIVE,
    {
      name: "showBaseline",
      affectsData: false,
      type: "boolean",
      label: "No-skill baseline",
      default: true,
    },
    {
      name: "showBest",
      affectsData: false,
      type: "boolean",
      label: "Mark best F1 threshold",
      default: true,
    },
  ],
  styleSections: ["title", "legend", "tooltip", "palette", "annotations"],
  defaults: { params: { positive: "", showBaseline: true, showBest: true } },
  render(spec, data, theme, ctx) {
    const v = statValues(data);
    if (!v) return applyStyle(EMPTY, spec, theme, ctx);
    const curves = objs(v.curves);
    const colors = colorsFor(spec, theme, curves.length);
    const showBest = param(spec, "showBest", true);
    const prevalence = num(v.prevalence);
    const series: Obj[] = curves.map((c, i) => {
      const best = obj(c.best);
      return {
        type: "line",
        name: `${str(c.score) ?? ""} (AP ${fmt(num(c.averagePrecision))})`,
        symbol: "none",
        step: "end",
        data: pairs(nums(c.recall), nums(c.precision)),
        lineStyle: { width: 2, color: colors[i] },
        itemStyle: { color: colors[i] },
        markPoint: showBest
          ? {
              symbol: "circle",
              symbolSize: 9,
              label: { show: false },
              data: [
                {
                  coord: [num(best.recall), num(best.precision)],
                  name: `Best F1 threshold ${fmt(num(best.threshold))}`,
                  value: fmt(num(best.f1)),
                },
              ],
              tooltip: {
                formatter: (p: { name: string; value: string }) => `${p.name}<br/>F1 = ${p.value}`,
              },
            }
          : undefined,
      };
    });
    if (param(spec, "showBaseline", true) && prevalence !== null && series[0]) {
      series[0].markLine = {
        silent: true,
        symbol: "none",
        lineStyle: { type: "dashed", color: theme.textMuted },
        label: { formatter: `No skill (${fmt(prevalence)})`, color: theme.textMuted },
        data: [{ yAxis: prevalence }],
      };
    }
    const option = {
      legend: {},
      xAxis: unitAxis("Recall"),
      yAxis: unitAxis("Precision"),
      tooltip: { trigger: "item" },
      series,
    };
    return applyStyle(option, spec, theme, ctx, { orientable: false, defaultTrigger: "item" });
  },
  recommend(columns) {
    return binaryColumn(columns) && hasName(columns, /score|prob|pred/i) ? 0.45 : 0;
  },
});

export const lorenz = definePlot({
  key: "lorenz",
  label: "Lorenz curve",
  family: "econometric",
  description: "How unequally a total is shared, with the Gini coefficient.",
  encodingsSchema: [
    {
      name: "y",
      label: "Value (income, wealth, ...)",
      required: true,
      multiple: false,
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
      name: "points",
      affectsData: true,
      type: "int",
      label: "Points per curve",
      min: 10,
      max: 2000,
      default: 200,
    },
    {
      name: "maxGroups",
      affectsData: true,
      type: "int",
      label: "Max groups",
      min: 1,
      max: 30,
      default: 10,
    },
    {
      name: "showEquality",
      affectsData: false,
      type: "boolean",
      label: "Line of equality",
      default: true,
    },
    { name: "fill", affectsData: false, type: "boolean", label: "Shade the gap", default: true },
  ],
  styleSections: ["title", "legend", "tooltip", "palette", "annotations"],
  defaults: { params: { points: 200, maxGroups: 10, showEquality: true, fill: true } },
  render(spec, data, theme, ctx) {
    const v = statValues(data);
    if (!v) return applyStyle(EMPTY, spec, theme, ctx);
    const groups = objs(v.groups);
    const colors = colorsFor(spec, theme, groups.length);
    const fill = param(spec, "fill", true) && groups.length === 1;
    const series: Obj[] = groups.map((g, i) => ({
      type: "line",
      name: `${str(g.key) ?? "All"} (Gini ${fmt(num(g.gini))})`,
      symbol: "none",
      data: pairs(nums(g.x), nums(g.y)),
      lineStyle: { width: 2, color: colors[i] },
      itemStyle: { color: colors[i] },
      areaStyle: fill ? { opacity: 0.15, color: colors[i] } : undefined,
    }));
    if (param(spec, "showEquality", true)) series.push(diagonal(theme.textMuted));
    const option = {
      legend: {},
      xAxis: unitAxis("Cumulative share of population"),
      yAxis: unitAxis("Cumulative share of total"),
      tooltip: { trigger: "axis" },
      series,
    };
    return applyStyle(option, spec, theme, ctx, {
      orientable: false,
      defaultTrigger: "axis",
      defaultYFormat: "percent:0",
    });
  },
  recommend(columns) {
    return hasName(columns, /income|wealth|revenue|salary|sales/i) ? 0.3 : 0;
  },
});

export const quantile = definePlot({
  key: "quantile",
  label: "Quantile plot",
  family: "econometric",
  description: "Every value against the fraction of the data below it (exact order statistics).",
  encodingsSchema: [
    {
      name: "y",
      label: "Value",
      required: true,
      multiple: false,
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
      name: "maxPoints",
      affectsData: true,
      type: "int",
      label: "Max points per group",
      min: 10,
      max: 2000,
      default: 1000,
    },
    {
      name: "maxGroups",
      affectsData: true,
      type: "int",
      label: "Max groups",
      min: 1,
      max: 30,
      default: 10,
    },
    {
      name: "showQuartiles",
      affectsData: false,
      type: "boolean",
      label: "Quartile lines",
      default: true,
    },
    {
      name: "connect",
      affectsData: false,
      type: "boolean",
      label: "Connect points",
      default: false,
    },
  ],
  styleSections: ["title", "legend", "axes", "tooltip", "palette", "refLines", "annotations"],
  defaults: { params: { maxPoints: 1000, maxGroups: 10, showQuartiles: true, connect: false } },
  render(spec, data, theme, ctx) {
    const v = statValues(data);
    if (!v) return applyStyle(EMPTY, spec, theme, ctx);
    const groups = objs(v.groups);
    const colors = colorsFor(spec, theme, groups.length);
    const connect = param(spec, "connect", false);
    const series: Obj[] = groups.map((g, i) => ({
      type: connect ? "line" : "scatter",
      name: str(g.key) ?? "All",
      symbolSize: 5,
      showSymbol: true,
      data: pairs(nums(g.f), nums(g.value)),
      itemStyle: { color: colors[i] },
      lineStyle: connect ? { width: 1.5, color: colors[i] } : undefined,
    }));
    const only = groups.length === 1 ? groups[0] : undefined;
    if (only && param(spec, "showQuartiles", true) && series[0]) {
      series[0].markLine = {
        silent: true,
        symbol: "none",
        lineStyle: { type: "dashed", color: theme.textMuted },
        label: { color: theme.textMuted, position: "insideEndTop" },
        data: [
          { xAxis: 0.25, name: "Q1", label: { formatter: `Q1 ${fmt(num(only.q1))}` } },
          { xAxis: 0.5, name: "Median", label: { formatter: `Median ${fmt(num(only.median))}` } },
          { xAxis: 0.75, name: "Q3", label: { formatter: `Q3 ${fmt(num(only.q3))}` } },
        ],
      };
    }
    const field = spec.encodings?.y?.[0]?.field ?? "Value";
    const option = {
      legend: groups.length > 1 ? {} : undefined,
      xAxis: unitAxis("Fraction of the data (f)"),
      yAxis: { type: "value", name: field, scale: true },
      tooltip: { trigger: "item" },
      series,
    };
    return applyStyle(option, spec, theme, ctx, { orientable: false, defaultTrigger: "item" });
  },
  recommend(columns) {
    return count(columns, "quantitative") >= 1 ? 0.1 : 0;
  },
});
