import type { ChartSpec } from "../../../api/types";
import { param } from "../../core/series";
import { applyStyle } from "../../core/style";
import type {
  ChannelSchema,
  ChartData,
  ChartTheme,
  ParamSchema,
  RenderContext,
} from "../../core/types";
import { definePlot } from "../../core/types";
import {
  ANY,
  binaryColumn,
  CATEGORICAL,
  count,
  EMPTY,
  escapeHtml,
  fmt,
  num,
  objs,
  pValue,
  QUANT,
  statValues,
  str,
  type Values,
} from "../multivariate/stat";
import { type IntervalRow, intervalOption } from "./intervals";

const CHANNELS: ChannelSchema[] = [
  {
    name: "y",
    label: "Outcome",
    required: true,
    multiple: false,
    accepts: [...QUANT, ...CATEGORICAL],
    aggregate: false,
    hint: "0/1 (or two values) for logit and probit; counts for Poisson.",
  },
  {
    name: "x",
    label: "Predictors",
    required: true,
    multiple: true,
    accepts: [...QUANT, ...CATEGORICAL],
    aggregate: false,
  },
  {
    name: "cluster",
    label: "Cluster standard errors by",
    required: false,
    multiple: false,
    accepts: ANY,
    aggregate: false,
  },
];

const MODEL_OPTIONS = [
  { value: "ols", label: "Linear (OLS)" },
  { value: "logit", label: "Logit" },
  { value: "probit", label: "Probit" },
  { value: "poisson", label: "Poisson" },
];

const SHARED_PARAMS: ParamSchema[] = [
  {
    name: "robust",
    affectsData: true,
    type: "boolean",
    label: "Robust (HC1) standard errors",
    default: false,
  },
  {
    name: "ciLevel",
    affectsData: true,
    type: "number",
    label: "Confidence level",
    min: 0.5,
    max: 0.999,
    step: 0.01,
    default: 0.95,
  },
  {
    name: "sort",
    affectsData: false,
    type: "select",
    label: "Order",
    default: "model",
    options: [
      { value: "model", label: "As in the model" },
      { value: "estimate", label: "By estimate" },
    ],
  },
];

function ordered<T extends { estimate: number | null }>(rows: T[], sort: string): T[] {
  if (sort !== "estimate") return rows;
  return [...rows].sort((a, b) => (b.estimate ?? -Infinity) - (a.estimate ?? -Infinity));
}

function tooltipFor(t: Values, scale: (v: number | null) => number | null, what: string): string {
  return (
    // Term names carry data values (categorical levels): escape them.
    `<b>${escapeHtml(str(t.term))}</b><br/>${escapeHtml(what)}: ${fmt(scale(num(t.estimate)))}` +
    ` [${fmt(scale(num(t.ciLow)))}, ${fmt(scale(num(t.ciHigh)))}]` +
    `<br/>SE: ${fmt(num(t.se))}, p = ${pValue(num(t.pValue))}`
  );
}

const RATIO_NAME: Record<string, string> = {
  logit: "Odds ratio",
  poisson: "Incidence rate ratio",
  probit: "exp(coefficient)",
  ols: "exp(coefficient)",
};

function renderCoefficients(
  spec: ChartSpec,
  data: ChartData,
  theme: ChartTheme,
  ctx?: RenderContext,
) {
  const v = statValues(data);
  if (!v) return applyStyle(EMPTY, spec, theme, ctx);
  const model = str(v.model) ?? "ols";
  const exponentiate = param(spec, "exponentiate", false) && model !== "ols";
  const scale = (x: number | null) => (x === null ? null : exponentiate ? Math.exp(x) : x);
  const showIntercept = param(spec, "showIntercept", false);
  const terms = objs(v.terms).filter((t) => showIntercept || t.intercept !== true);
  const what = exponentiate ? (RATIO_NAME[model] ?? "Ratio") : "Coefficient";
  const rows: IntervalRow[] = ordered(
    terms.map((t) => ({
      label: str(t.term) ?? "",
      estimate: scale(num(t.estimate)),
      lo: scale(num(t.ciLow)),
      hi: scale(num(t.ciHigh)),
      tooltip: tooltipFor(t, scale, what),
    })),
    param(spec, "sort", "model"),
  );
  return intervalOption(spec, theme, ctx, rows, {
    valueName: what,
    nullValue: exponentiate ? 1 : 0,
    log: exponentiate,
  });
}

export const coefficient = definePlot({
  key: "coefficient",
  label: "Coefficient plot",
  family: "econometric",
  description: "Regression coefficients with confidence intervals.",
  encodingsSchema: CHANNELS,
  paramsSchema: [
    {
      name: "model",
      affectsData: true,
      type: "select",
      label: "Model",
      default: "ols",
      options: MODEL_OPTIONS,
    },
    {
      name: "standardize",
      affectsData: true,
      type: "boolean",
      label: "Per standard deviation of numeric predictors",
      default: false,
    },
    ...SHARED_PARAMS,
    {
      name: "showIntercept",
      affectsData: false,
      type: "boolean",
      label: "Show intercept",
      default: false,
    },
    {
      name: "exponentiate",
      affectsData: false,
      type: "boolean",
      label: "Show as ratios (exp)",
      help: "Odds ratios for logit, rate ratios for Poisson.",
      default: false,
    },
  ],
  styleSections: ["title", "axes", "tooltip", "palette", "refLines", "annotations"],
  defaults: {
    params: {
      model: "ols",
      standardize: false,
      robust: false,
      ciLevel: 0.95,
      sort: "model",
      showIntercept: false,
      exponentiate: false,
    },
  },
  render: renderCoefficients,
  recommend(columns) {
    return count(columns, "quantitative") >= 3 ? 0.15 : 0;
  },
});

function renderMarginal(spec: ChartSpec, data: ChartData, theme: ChartTheme, ctx?: RenderContext) {
  const v = statValues(data);
  if (!v) return applyStyle(EMPTY, spec, theme, ctx);
  const probability = str(v.scale) === "probability";
  const what = probability ? `Change in P(${str(v.outcome) ?? "y"} = 1)` : "dy/dx";
  const same = (x: number | null) => x;
  const rows: IntervalRow[] = ordered(
    objs(v.effects).map((e) => ({
      label: str(e.term) ?? "",
      estimate: num(e.estimate),
      lo: num(e.ciLow),
      hi: num(e.ciHigh),
      tooltip: tooltipFor(e, same, "Marginal effect"),
    })),
    param(spec, "sort", "model"),
  );
  return intervalOption(spec, theme, ctx, rows, { valueName: what, nullValue: 0 });
}

export const marginalEffects = definePlot({
  key: "marginal_effects",
  label: "Marginal effects plot",
  family: "econometric",
  description: "How much the outcome changes per unit of each predictor, with intervals.",
  encodingsSchema: CHANNELS,
  paramsSchema: [
    {
      name: "model",
      affectsData: true,
      type: "select",
      label: "Model",
      default: "logit",
      options: MODEL_OPTIONS,
    },
    {
      name: "at",
      affectsData: true,
      type: "select",
      label: "Effects",
      default: "overall",
      options: [
        { value: "overall", label: "Average over rows (AME)" },
        { value: "mean", label: "At the means (MEM)" },
        { value: "median", label: "At the medians" },
      ],
    },
    {
      name: "discrete",
      affectsData: true,
      type: "boolean",
      label: "0/1 predictors as a discrete change",
      default: true,
    },
    ...SHARED_PARAMS,
  ],
  styleSections: ["title", "axes", "tooltip", "palette", "refLines", "annotations"],
  defaults: {
    params: {
      model: "logit",
      at: "overall",
      discrete: true,
      robust: false,
      ciLevel: 0.95,
      sort: "model",
    },
  },
  render: renderMarginal,
  recommend(columns) {
    return binaryColumn(columns) && count(columns, "quantitative") >= 2 ? 0.15 : 0;
  },
});
