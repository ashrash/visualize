import { errorBars } from "../../core/primitives/errorBars";
import { param } from "../../core/series";
import { applyStyle } from "../../core/style";
import { definePlot } from "../../core/types";
import {
  ANY,
  colorsFor,
  EMPTY,
  fmt,
  hasName,
  list,
  num,
  obj,
  objs,
  pValue,
  QUANT,
  statValues,
} from "../multivariate/stat";
import { detachAxisLine } from "./intervals";

type Obj = Record<string, unknown>;

export const eventStudy = definePlot({
  key: "event_study",
  label: "Event study plot",
  family: "econometric",
  description: "Effects by period relative to treatment, with a pre-trend check.",
  encodingsSchema: [
    {
      name: "y",
      label: "Outcome",
      required: true,
      multiple: false,
      accepts: QUANT,
      aggregate: false,
    },
    {
      name: "unit",
      label: "Unit (panel id)",
      required: true,
      multiple: false,
      accepts: ANY,
      aggregate: false,
    },
    {
      name: "time",
      label: "Period",
      required: true,
      multiple: false,
      accepts: QUANT,
      aggregate: false,
    },
    {
      name: "treatTime",
      label: "Treatment period",
      required: false,
      multiple: false,
      accepts: QUANT,
      aggregate: false,
      hint: "Empty for never-treated units. Or give the event time instead.",
    },
    {
      name: "eventTime",
      label: "Event time (periods since treatment)",
      required: false,
      multiple: false,
      accepts: QUANT,
      aggregate: false,
    },
    {
      name: "x",
      label: "Controls",
      required: false,
      multiple: true,
      accepts: QUANT,
      aggregate: false,
    },
  ],
  paramsSchema: [
    {
      name: "windowStart",
      affectsData: true,
      type: "int",
      label: "Window start",
      min: -30,
      max: -1,
      default: -5,
    },
    {
      name: "windowEnd",
      affectsData: true,
      type: "int",
      label: "Window end",
      min: 0,
      max: 30,
      default: 5,
    },
    {
      name: "reference",
      affectsData: true,
      type: "int",
      label: "Reference period (omitted)",
      min: -30,
      max: 30,
      default: -1,
    },
    {
      name: "fixedEffects",
      affectsData: true,
      type: "select",
      label: "Fixed effects",
      default: "unit_time",
      options: [
        { value: "unit_time", label: "Unit and period (two-way)" },
        { value: "unit", label: "Unit" },
        { value: "time", label: "Period" },
        { value: "none", label: "None" },
      ],
    },
    {
      name: "binEndpoints",
      affectsData: true,
      type: "boolean",
      label: "Bin periods beyond the window into the endpoints",
      default: true,
    },
    {
      name: "cluster",
      affectsData: true,
      type: "boolean",
      label: "Cluster standard errors by unit",
      default: true,
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
  ],
  styleSections: ["title", "axes", "tooltip", "palette", "refLines", "annotations"],
  defaults: {
    params: {
      windowStart: -5,
      windowEnd: 5,
      reference: -1,
      fixedEffects: "unit_time",
      binEndpoints: true,
      cluster: true,
      ciLevel: 0.95,
    },
  },
  render(spec, data, theme, ctx) {
    const v = statValues(data);
    if (!v) return applyStyle(EMPTY, spec, theme, ctx);
    const estimates = objs(v.estimates);
    const [color] = colorsFor(spec, theme, 1);
    const reference = num(v.reference) ?? param(spec, "reference", -1);
    const window = list(v.window).map((w) => num(w));
    const points = estimates.map((e) => ({
      k: num(e.relTime),
      estimate: num(e.estimate),
      lo: num(e.ciLow),
      hi: num(e.ciHigh),
      reference: e.reference === true,
      tooltip:
        e.reference === true
          ? `<b>Period ${num(e.relTime)}</b><br/>Reference (fixed at 0)`
          : `<b>Period ${num(e.relTime)}</b><br/>Effect ${fmt(num(e.estimate))} ` +
            `[${fmt(num(e.ciLow))}, ${fmt(num(e.ciHigh))}]<br/>SE ${fmt(num(e.se))}, ` +
            `p = ${pValue(num(e.pValue))}<br/>Treated rows: ${num(e.n) ?? 0}`,
    }));
    const series: Obj[] = [
      {
        type: "line",
        name: "Effect",
        data: points.map((p) => ({
          value: [p.k, p.estimate],
          tooltip: p.tooltip,
          symbol: p.reference ? "emptyCircle" : "circle",
        })),
        symbolSize: 8,
        lineStyle: { width: 1.5, color, type: "dotted" },
        itemStyle: { color },
        z: 4,
        tooltip: { formatter: (p: { data: { tooltip: string } }) => p.data.tooltip },
        markLine: {
          silent: true,
          symbol: "none",
          lineStyle: { type: "dashed", color: theme.textMuted },
          label: { color: theme.textMuted },
          data: [
            { yAxis: 0, label: { show: false } },
            { xAxis: reference + 0.5, label: { formatter: "treatment", position: "insideEndTop" } },
          ],
        },
      },
      errorBars({
        x: points.filter((p) => !p.reference).map((p) => p.k),
        lo: points.filter((p) => !p.reference).map((p) => p.lo),
        hi: points.filter((p) => !p.reference).map((p) => p.hi),
        color,
        capWidth: 4,
        theme,
      }),
    ];
    const pre = obj(v.preTrend);
    const option: Obj = {
      xAxis: {
        type: "value",
        name: "Periods relative to treatment",
        minInterval: 1,
        min: (window[0] ?? -5) - 0.5,
        max: (window[1] ?? 5) + 0.5,
        axisLabel: { formatter: (value: number) => (Number.isInteger(value) ? String(value) : "") },
      },
      yAxis: { type: "value", name: spec.encodings?.y?.[0]?.field ?? "Effect", scale: true },
      tooltip: { trigger: "item" },
      series,
    };
    if (v.preTrend) {
      const df = list(pre.df).map((d) => num(d));
      option.graphic = {
        type: "text",
        left: 80,
        bottom: 64,
        silent: true,
        style: {
          text: `Pre-trend test: F(${df[0] ?? "–"}, ${df[1] ?? "–"}) = ${fmt(num(pre.statistic))}, p = ${pValue(num(pre.pValue))}`,
          fill: theme.textMuted,
          font: `12px ${theme.fontFamily}`,
        },
      };
    }
    // The y axis sits at the left edge, not through period 0 (the treatment line marks that).
    return detachAxisLine(
      applyStyle(option, spec, theme, ctx, { orientable: false, defaultTrigger: "item" }),
      "yAxis",
    );
  },
  recommend(columns) {
    return hasName(columns, /event_?time|treat/i) && hasName(columns, /unit|id|firm|state/i)
      ? 0.6
      : 0;
  },
});
