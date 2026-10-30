import { ciBand } from "../../core/primitives/ciBand";
import { param } from "../../core/series";
import { applyStyle } from "../../core/style";
import { definePlot } from "../../core/types";
import {
  ANY,
  CATEGORICAL,
  colorsFor,
  EMPTY,
  fmt,
  hasName,
  num,
  nums,
  obj,
  objs,
  pValue,
  QUANT,
  statValues,
  str,
} from "../multivariate/stat";

type Obj = Record<string, unknown>;

/**
 * Expand a right-continuous step function into points a straight-line series draws as steps:
 * (t0, v0), (t1, v0), (t1, v1), (t2, v1), ...
 */
export function stepPoints(
  times: (number | null)[],
  values: (number | null)[],
): { x: number[]; y: (number | null)[] } {
  const x: number[] = [];
  const y: (number | null)[] = [];
  times.forEach((t, i) => {
    if (t === null) return;
    if (i > 0) {
      x.push(t);
      y.push(values[i - 1] ?? null);
    }
    x.push(t);
    y.push(values[i] ?? null);
  });
  return { x, y };
}

export const kaplanMeier = definePlot({
  key: "kaplan_meier",
  label: "Kaplan-Meier survival curve",
  family: "econometric",
  description: "Share still event-free over time, with censoring marks and confidence bands.",
  encodingsSchema: [
    {
      name: "time",
      label: "Time",
      required: true,
      multiple: false,
      accepts: QUANT,
      aggregate: false,
    },
    {
      name: "event",
      label: "Event (1) / censored (0)",
      required: true,
      multiple: false,
      accepts: ANY,
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
      name: "ciType",
      affectsData: true,
      type: "select",
      label: "Band",
      default: "log-log",
      options: [
        { value: "log-log", label: "Log-log (stays within 0-1)" },
        { value: "log", label: "Log (R's default)" },
        { value: "plain", label: "Plain (Greenwood)" },
      ],
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
      name: "eventValue",
      affectsData: true,
      type: "text",
      label: "Event value (blank = automatic)",
      help: "The event field's value that means the event happened. Automatic: 1 of 0/1, true, 2 of 1/2 (R's Surv coding), or a word like 'died'.",
      default: "",
    },
    {
      name: "showCi",
      affectsData: false,
      type: "boolean",
      label: "Show confidence bands",
      default: true,
    },
    {
      name: "showCensored",
      affectsData: false,
      type: "boolean",
      label: "Mark censored times",
      default: true,
    },
    {
      name: "showMedian",
      affectsData: false,
      type: "boolean",
      label: "Show median line",
      default: false,
    },
  ],
  styleSections: ["title", "legend", "axes", "tooltip", "palette", "refLines", "annotations"],
  defaults: {
    params: {
      ciLevel: 0.95,
      ciType: "log-log",
      maxGroups: 10,
      eventValue: "",
      showCi: true,
      showCensored: true,
      showMedian: false,
    },
  },
  render(spec, data, theme, ctx) {
    const v = statValues(data);
    if (!v) return applyStyle(EMPTY, spec, theme, ctx);
    const groups = objs(v.groups);
    const colors = colorsFor(spec, theme, groups.length);
    const showCi = param(spec, "showCi", true);
    const showCensored = param(spec, "showCensored", true);
    const series: Obj[] = [];
    const medians: string[] = [];
    groups.forEach((g, gi) => {
      const name = str(g.key) ?? "All";
      const times = nums(g.time);
      const survival = nums(g.survival);
      const lowerValues = nums(g.lower);
      const upperValues = nums(g.upper);
      // Follow-up continues past the last event when later rows are censored: extend the
      // last step to the last censoring time.
      const lastCensor = Math.max(-Infinity, ...nums(g.censorTime).map((t) => t ?? -Infinity));
      const lastTime = times[times.length - 1] ?? null;
      if (lastTime !== null && lastCensor > lastTime) {
        times.push(lastCensor);
        survival.push(survival[survival.length - 1] ?? null);
        lowerValues.push(lowerValues[lowerValues.length - 1] ?? null);
        upperValues.push(upperValues[upperValues.length - 1] ?? null);
      }
      const color = colors[gi];
      const median = num(g.median);
      const atRisk = nums(g.atRisk);
      medians.push(
        `${name}: median ${median !== null ? `${fmt(median)} [${fmt(num(g.medianLow))}, ${fmt(num(g.medianHigh))}]` : "not reached"}`,
      );
      series.push({
        type: "line",
        name,
        step: "end",
        symbol: "none",
        data: times.map((t, i) => [t, survival[i] ?? null, atRisk[i] ?? null]),
        lineStyle: { width: 2, color },
        itemStyle: { color },
        tooltip: {
          valueFormatter: (value: unknown) => fmt(value as number),
        },
      });
      if (showCi) {
        const lower = stepPoints(times, lowerValues);
        const upper = stepPoints(times, upperValues);
        series.push(
          ...ciBand({
            x: lower.x,
            lo: lower.y,
            hi: upper.y,
            color,
            id: gi,
            axis: "value",
            opacity: 0.12,
          }),
        );
      }
      if (showCensored) {
        const ct = nums(g.censorTime);
        const cs = nums(g.censorSurvival);
        series.push({
          type: "scatter",
          name,
          symbol: "rect",
          symbolSize: [1.5, 9],
          itemStyle: { color },
          tooltip: { show: false },
          silent: true,
          data: ct.map((t, i) => [t, cs[i] ?? null]),
        });
      }
    });
    if (param(spec, "showMedian", false) && series[0]) {
      series[0].markLine = {
        silent: true,
        symbol: "none",
        lineStyle: { type: "dotted", color: theme.textMuted },
        label: { formatter: "50%", color: theme.textMuted },
        data: [{ yAxis: 0.5 }],
      };
    }
    const timeField = spec.encodings?.time?.[0]?.field ?? "Time";
    const logRank = obj(v.logRank);
    const option: Obj = {
      legend: groups.length > 1 ? { data: groups.map((g) => str(g.key) ?? "All") } : undefined,
      xAxis: { type: "value", name: timeField, min: 0 },
      yAxis: { type: "value", name: "Survival probability", min: 0, max: 1 },
      tooltip: { trigger: "axis" },
      series,
    };
    const notes = param(spec, "showMedian", false) ? [...medians] : [];
    if (v.logRank) notes.push(`Log-rank p = ${pValue(num(logRank.pValue))}`);
    if (notes.length) {
      option.graphic = {
        // Top right: survival curves start at 1 on the left and fall, so this corner is free.
        type: "text",
        right: 32,
        top: groups.length > 1 ? 52 : 24,
        silent: true,
        style: {
          text: notes.join("\n"),
          textAlign: "right",
          fill: theme.textMuted,
          font: `12px ${theme.fontFamily}`,
        },
      };
    }
    return applyStyle(option, spec, theme, ctx, { orientable: false, defaultTrigger: "axis" });
  },
  recommend(columns) {
    const time = hasName(columns, /time|duration|months|days|weeks|years|tenure/i);
    const event = hasName(columns, /event|status|censor|died|death|churn/i);
    return time && event ? 0.6 : 0;
  },
});
