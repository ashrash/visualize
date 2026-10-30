import { param } from "../../core/series";
import { applyStyle } from "../../core/style";
import { definePlot } from "../../core/types";
import {
  ANY,
  CATEGORICAL,
  EMPTY,
  escapeHtml,
  fmt,
  hasName,
  num,
  obj,
  objs,
  pValue,
  QUANT,
  statValues,
  str,
  type Values,
} from "../multivariate/stat";
import { type IntervalRow, intervalOption } from "./intervals";

function pooledRow(p: Values, label: string, colorIndex: number): IntervalRow {
  const heterogeneity =
    num(p.i2) !== null
      ? `<br/>I² = ${fmt((num(p.i2) ?? 0) * 100, 1)}%, τ² = ${fmt(num(p.tau2))}, ` +
        `Q = ${fmt(num(p.q))} (df ${num(p.qDf) ?? "–"}, p = ${pValue(num(p.qPValue))})`
      : "";
  return {
    label,
    estimate: num(p.estimate),
    lo: num(p.lower),
    hi: num(p.upper),
    pooled: true,
    colorIndex,
    tooltip:
      `<b>${escapeHtml(label)}</b> (${num(p.k) ?? 0} studies)<br/>` +
      `${fmt(num(p.estimate))} [${fmt(num(p.lower))}, ${fmt(num(p.upper))}], ` +
      `p = ${pValue(num(p.pValue))}${heterogeneity}`,
  };
}

export const forest = definePlot({
  key: "forest",
  label: "Forest plot",
  family: "econometric",
  description: "Study estimates with intervals, weights and a pooled (meta-analysis) estimate.",
  encodingsSchema: [
    {
      name: "label",
      label: "Study",
      required: true,
      multiple: false,
      accepts: ANY,
      aggregate: false,
    },
    {
      name: "estimate",
      label: "Estimate",
      required: true,
      multiple: false,
      accepts: QUANT,
      aggregate: false,
    },
    {
      name: "lower",
      label: "Lower limit",
      required: false,
      multiple: false,
      accepts: QUANT,
      aggregate: false,
    },
    {
      name: "upper",
      label: "Upper limit",
      required: false,
      multiple: false,
      accepts: QUANT,
      aggregate: false,
    },
    {
      name: "se",
      label: "Standard error",
      required: false,
      multiple: false,
      accepts: QUANT,
      aggregate: false,
      hint: "Give a standard error, or both limits. For ratios, the SE of log(ratio).",
    },
    {
      name: "color",
      label: "Subgroup",
      required: false,
      multiple: false,
      accepts: CATEGORICAL,
      aggregate: false,
    },
  ],
  paramsSchema: [
    {
      name: "measure",
      affectsData: true,
      type: "select",
      label: "Effect measure",
      default: "difference",
      options: [
        { value: "difference", label: "Difference (mean difference, log ratio, ...)" },
        { value: "ratio", label: "Ratio (odds, risk or hazard ratio)" },
      ],
    },
    {
      name: "pooled",
      affectsData: true,
      type: "select",
      label: "Pooled estimate",
      default: "random",
      options: [
        { value: "random", label: "Random effects (DerSimonian-Laird)" },
        { value: "fixed", label: "Fixed effect (inverse variance)" },
        { value: "none", label: "None" },
      ],
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
      name: "maxRows",
      affectsData: true,
      type: "int",
      label: "Max studies",
      min: 1,
      max: 1000,
      default: 200,
    },
    {
      name: "sort",
      affectsData: false,
      type: "select",
      label: "Order",
      default: "data",
      options: [
        { value: "data", label: "As in the data" },
        { value: "estimate", label: "By estimate" },
        { value: "label", label: "By name" },
      ],
    },
    {
      name: "nullLine",
      affectsData: false,
      type: "boolean",
      label: "Line of no effect",
      default: true,
    },
  ],
  styleSections: ["title", "axes", "tooltip", "palette", "refLines", "annotations"],
  defaults: {
    params: {
      measure: "difference",
      pooled: "random",
      ciLevel: 0.95,
      maxRows: 200,
      sort: "data",
      nullLine: true,
    },
  },
  render(spec, data, theme, ctx) {
    const v = statValues(data);
    if (!v) return applyStyle(EMPTY, spec, theme, ctx);
    const ratio = str(v.measure) === "ratio";
    const sort = param(spec, "sort", "data");
    const studies = objs(v.rows);
    const weights = studies.map((s) => num(s.weight) ?? 0);
    const maxWeight = Math.max(1e-9, ...weights);
    const subgroups = objs(v.subgroups);
    const groupKeys = subgroups.map((s) => str(s.key) ?? "");
    const toRow = (s: Values): IntervalRow => {
      const weight = num(s.weight);
      const group = str(s.group);
      return {
        label: str(s.label) ?? "",
        estimate: num(s.estimate),
        lo: num(s.lower),
        hi: num(s.upper),
        size: weight !== null ? 5 + 11 * Math.sqrt(weight / maxWeight) : 8,
        colorIndex: Math.max(0, group ? groupKeys.indexOf(group) : 0),
        tooltip:
          `<b>${escapeHtml(str(s.label))}</b><br/>${fmt(num(s.estimate))} ` +
          `[${fmt(num(s.lower))}, ${fmt(num(s.upper))}]` +
          (weight !== null ? `<br/>Weight ${fmt(weight, 1)}%` : ""),
      };
    };
    const sortRows = (rows: IntervalRow[]) =>
      sort === "estimate"
        ? [...rows].sort((a, b) => (a.estimate ?? 0) - (b.estimate ?? 0))
        : sort === "label"
          ? [...rows].sort((a, b) => a.label.localeCompare(b.label))
          : rows;
    let rows: IntervalRow[];
    if (subgroups.length) {
      rows = subgroups.flatMap((sg, gi) => [
        ...sortRows(studies.filter((s) => str(s.group) === groupKeys[gi]).map(toRow)),
        pooledRow(sg, `${groupKeys[gi]} (subtotal)`, gi),
      ]);
      rows.push(...sortRows(studies.filter((s) => str(s.group) === null).map(toRow)));
    } else {
      rows = sortRows(studies.map(toRow));
    }
    const pooled = obj(v.pooled);
    if (v.pooled) rows.push(pooledRow(pooled, "Overall", -1));
    const level = num(v.ciLevel) ?? 0.95;
    return intervalOption(spec, theme, ctx, rows, {
      valueName: `${ratio ? "Ratio" : "Estimate"} (${Math.round(level * 100)}% CI)`,
      nullValue: num(v.nullValue) ?? (ratio ? 1 : 0),
      log: ratio,
    });
  },
  recommend(columns) {
    return hasName(columns, /ci_?lo|lower|ci_?hi|upper|std_?err|^se$/i) && columns.length < 20
      ? 0.6
      : 0;
  },
});
