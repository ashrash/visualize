import { ciBand } from "../../core/primitives/ciBand";
import { param } from "../../core/series";
import { applyStyle } from "../../core/style";
import { definePlot } from "../../core/types";
import {
  colorsFor,
  count,
  EMPTY,
  fmt,
  num,
  nums,
  objs,
  QUANT,
  statValues,
  str,
  strs,
} from "../multivariate/stat";

type Obj = Record<string, unknown>;

/** Panel rectangles (percent of the chart) for a rows x cols grid of small multiples. */
export function panelLayout(
  rows: number,
  cols: number,
  o: { top: number; bottom: number; left: number; right: number; gap: number },
): { left: number; top: number; width: number; height: number }[] {
  const width = (100 - o.left - o.right - o.gap * (cols - 1)) / cols;
  const height = (100 - o.top - o.bottom - o.gap * (rows - 1)) / rows;
  const out = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      out.push({
        left: o.left + c * (width + o.gap),
        top: o.top + r * (height + o.gap),
        width,
        height,
      });
    }
  }
  return out;
}

const pct = (v: number) => `${v.toFixed(2)}%`;

export const impulseResponse = definePlot({
  key: "impulse_response",
  label: "Impulse response (VAR)",
  family: "econometric",
  description: "How each series responds over time to a shock in another (vector autoregression).",
  encodingsSchema: [
    {
      name: "y",
      label: "Series (2-6, in causal order)",
      required: true,
      multiple: true,
      accepts: QUANT,
      aggregate: false,
    },
    {
      name: "time",
      label: "Time",
      required: true,
      multiple: false,
      accepts: ["temporal", "quantitative"],
      aggregate: false,
    },
  ],
  paramsSchema: [
    {
      name: "lags",
      affectsData: true,
      type: "int",
      label: "Lags (0 = choose by AIC)",
      min: 0,
      max: 24,
      default: 0,
    },
    {
      name: "periods",
      affectsData: true,
      type: "int",
      label: "Periods ahead",
      min: 1,
      max: 60,
      default: 12,
    },
    {
      name: "orthogonalized",
      affectsData: true,
      type: "boolean",
      label: "Orthogonalized (Cholesky) shocks",
      default: true,
    },
    {
      name: "ciLevel",
      affectsData: true,
      type: "number",
      label: "Band level",
      min: 0.5,
      max: 0.999,
      step: 0.01,
      default: 0.95,
    },
    {
      name: "impulse",
      affectsData: false,
      type: "column",
      label: "Only shocks to (blank = all)",
      accepts: ["quantitative"],
    },
  ],
  styleSections: ["title", "tooltip", "palette"],
  defaults: { params: { lags: 0, periods: 12, orthogonalized: true, ciLevel: 0.95 } },
  render(spec, data, theme, ctx) {
    const v = statValues(data);
    if (!v) return applyStyle(EMPTY, spec, theme, ctx);
    const variables = strs(v.variables).map((s) => s ?? "");
    const chosen = param(spec, "impulse", "");
    const impulses = variables.includes(chosen) ? [chosen] : variables;
    const responses = objs(v.responses).filter((r) => impulses.includes(str(r.impulse) ?? ""));
    const cols = impulses.length === 1 ? Math.ceil(Math.sqrt(variables.length)) : impulses.length;
    const rows = Math.ceil(responses.length / cols);
    // The vertical gap leaves room for the x tick labels above and the next panel's title.
    const panels = panelLayout(rows, cols, { top: 8, bottom: 5, left: 7, right: 3, gap: 9 });
    const colors = colorsFor(spec, theme, variables.length);
    const grid: Obj[] = [];
    const xAxis: Obj[] = [];
    const yAxis: Obj[] = [];
    const series: Obj[] = [];
    const graphic: Obj[] = [];
    // With every impulse shown, panel (row = response, column = impulse), as statsmodels plots.
    const ordered =
      impulses.length === 1
        ? responses
        : variables.flatMap((resp) =>
            impulses.map((imp) =>
              responses.find((r) => str(r.impulse) === imp && str(r.response) === resp),
            ),
          );
    ordered.forEach((r, i) => {
      const box = panels[i];
      if (!r || !box) return;
      const irf = nums(r.irf);
      const steps = irf.map((_, h) => h);
      const color = colors[Math.max(0, variables.indexOf(str(r.impulse) ?? ""))];
      grid.push({
        left: pct(box.left),
        top: pct(box.top),
        width: pct(box.width),
        height: pct(box.height),
      });
      xAxis.push({ type: "value", gridIndex: i, min: 0, max: steps.length - 1, minInterval: 1 });
      yAxis.push({ type: "value", gridIndex: i, scale: true, splitNumber: 3 });
      series.push(
        ...ciBand({
          x: steps,
          lo: nums(r.lower),
          hi: nums(r.upper),
          color,
          id: i,
          axis: "value",
          xAxisIndex: i,
          yAxisIndex: i,
        }),
        {
          type: "line",
          name: `${str(r.impulse)} → ${str(r.response)}`,
          xAxisIndex: i,
          yAxisIndex: i,
          symbol: "none",
          data: steps.map((h) => [h, irf[h] ?? null]),
          lineStyle: { width: 2, color },
          itemStyle: { color },
          markLine: {
            silent: true,
            symbol: "none",
            label: { show: false },
            lineStyle: { type: "dashed", color: theme.textMuted },
            data: [{ yAxis: 0 }],
          },
          tooltip: { valueFormatter: (value: unknown) => fmt(value as number) },
        },
      );
      graphic.push({
        type: "text",
        left: pct(box.left),
        top: pct(Math.max(0, box.top - 3.5)),
        silent: true,
        style: {
          text: `${str(r.impulse)} → ${str(r.response)}`,
          fill: theme.textMuted,
          font: `12px ${theme.fontFamily}`,
        },
      });
    });
    const option = {
      grid,
      xAxis,
      yAxis,
      series,
      graphic,
      tooltip: { trigger: "axis" },
      aria: {
        description: `Impulse responses of a VAR(${num(v.lags) ?? ""}) over ${num(v.periods) ?? ""} periods.`,
      },
    };
    // Each panel is titled "impulse → response", so a legend would only repeat the titles.
    return {
      ...applyStyle(option, spec, theme, ctx, { defaultTrigger: "axis" }),
      legend: undefined,
    };
  },
  recommend(columns) {
    return count(columns, "temporal") >= 1 && count(columns, "quantitative") >= 2 ? 0.15 : 0;
  },
});
