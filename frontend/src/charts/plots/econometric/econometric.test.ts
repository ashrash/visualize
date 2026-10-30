import { describe, expect, it } from "vitest";
import type { ChartSpec } from "../../../api/types";
import { makeData, makeSpec, makeTheme } from "../../core/testing";
import type { ChartData, PlotEntry } from "../../core/types";
import { validateEntry } from "../../core/validate";
import entries from "./index";
import { panelLayout } from "./irf";
import { stepPoints } from "./km";

// biome-ignore lint/suspicious/noExplicitAny: tests probe deep into the ECharts option
type Opt = Record<string, any>;

const light = makeTheme();
const dark = makeTheme({
  mode: "dark",
  text: "#eeeeee",
  textMuted: "#aaaaaa",
  background: "#111111",
});

function statData(values: Record<string, unknown>): ChartData {
  return {
    ...makeData({}, {}, { meta: { reduction: "aggregated", method: "test" } }),
    stat: { values: values as never, method: "test", n: 10, sampled: false, nMissing: 0 },
  };
}

const byKey = (key: string): PlotEntry => {
  const entry = entries.find((e) => e.key === key);
  if (!entry) throw new Error(`no ${key}`);
  return entry;
};

const render = (
  key: string,
  data: ChartData,
  params: ChartSpec["params"] = {},
  theme = light,
  extra: Partial<ChartSpec> = {},
) => {
  const entry = byKey(key);
  const spec = makeSpec({
    plotType: key as never,
    params: { ...(entry.defaults.params ?? {}), ...params },
    ...extra,
  });
  return entry.render(spec, data, theme) as Opt;
};

const km = statData({
  groups: [
    {
      key: "6-MP",
      n: 21,
      events: 9,
      time: [0, 6, 7, 10],
      survival: [1, 0.857, 0.807, 0.753],
      lower: [1, 0.72, 0.653, 0.586],
      upper: [1, 1, 0.996, 0.968],
      atRisk: [21, 21, 17, 15],
      median: 23,
      medianLow: 13,
      medianHigh: null,
      censorTime: [6, 9, 35],
      censorSurvival: [0.857, 0.807, 0.753],
    },
    {
      key: "Placebo",
      n: 21,
      events: 21,
      time: [0, 1],
      survival: [1, 0.9],
      lower: [1, 0.8],
      upper: [1, 1],
      atRisk: [21, 21],
      median: 8,
      medianLow: 4,
      medianHigh: 12,
      censorTime: [],
      censorSurvival: [],
    },
  ],
  logRank: { statistic: 16.79, df: 1, pValue: 4.17e-5 },
  ciLevel: 0.95,
});

const forest = statData({
  rows: [
    { label: "A", group: "RCT", estimate: 0.8, lower: 0.6, upper: 1.1, se: 0.1, weight: 60 },
    { label: "B", group: "Obs", estimate: 0.5, lower: 0.3, upper: 0.9, se: 0.2, weight: 40 },
  ],
  pooled: {
    k: 2,
    estimate: 0.68,
    lower: 0.55,
    upper: 0.85,
    se: 0.1,
    pValue: 0.001,
    i2: 0.3,
    tau2: 0.01,
    q: 1.4,
    qDf: 1,
    qPValue: 0.2,
  },
  subgroups: [
    { key: "Obs", k: 1, estimate: 0.5, lower: 0.3, upper: 0.9, i2: null },
    { key: "RCT", k: 1, estimate: 0.8, lower: 0.6, upper: 1.1, i2: null },
  ],
  measure: "ratio",
  ciLevel: 0.95,
  nullValue: 1,
});

const coefficients = statData({
  model: "logit",
  terms: [
    {
      term: "Intercept",
      estimate: -1,
      se: 0.2,
      ciLow: -1.4,
      ciHigh: -0.6,
      pValue: 0.001,
      intercept: true,
    },
    {
      term: "age",
      estimate: 0.5,
      se: 0.1,
      ciLow: 0.3,
      ciHigh: 0.7,
      pValue: 0.01,
      intercept: false,
    },
    {
      term: "plan: Pro",
      estimate: -0.2,
      se: 0.1,
      ciLow: -0.4,
      ciHigh: 0,
      pValue: 0.05,
      intercept: false,
    },
  ],
});

const marginal = statData({
  scale: "probability",
  outcome: "churned",
  effects: [
    { term: "tenure", estimate: -0.01, se: 0.002, ciLow: -0.014, ciHigh: -0.006, pValue: 0.0001 },
    { term: "calls", estimate: 0.05, se: 0.01, ciLow: 0.03, ciHigh: 0.07, pValue: 0.0001 },
  ],
});

const rocData = statData({
  positive: "1",
  curves: [
    {
      score: "model_a",
      fpr: [0, 0, 0.5, 1],
      tpr: [0, 0.5, 1, 1],
      threshold: [null, 0.8, 0.4, 0.1],
      auc: 0.875,
      aucLow: 0.6,
      aucHigh: 1,
      best: { threshold: 0.4, fpr: 0.5, tpr: 1, youden: 0.5 },
    },
  ],
});

const prData = statData({
  prevalence: 0.5,
  curves: [
    {
      score: "s",
      recall: [1, 1, 0.5, 0.5, 0],
      precision: [0.5, 0.667, 0.5, 1, 1],
      threshold: [0.1, 0.35, 0.4, 0.8, null],
      averagePrecision: 0.833,
      best: { threshold: 0.35, precision: 0.667, recall: 1, f1: 0.8 },
    },
  ],
});

const lorenzData = statData({
  groups: [
    {
      key: "All",
      n: 5,
      gini: 0.2667,
      x: [0, 0.2, 0.4, 0.6, 0.8, 1],
      y: [0, 0.067, 0.2, 0.4, 0.667, 1],
    },
  ],
});

const quantileData = statData({
  groups: [
    { key: "All", n: 3, f: [1 / 6, 0.5, 5 / 6], value: [1, 2, 3], q1: 1.5, median: 2, q3: 2.5 },
  ],
  exact: true,
});

const irfData = statData({
  variables: ["gdp", "infl"],
  periods: 2,
  lags: 1,
  responses: [
    {
      impulse: "gdp",
      response: "gdp",
      irf: [1, 0.5, 0.25],
      lower: [0.8, 0.3, 0.1],
      upper: [1.2, 0.7, 0.4],
    },
    {
      impulse: "gdp",
      response: "infl",
      irf: [0.2, 0.1, 0.05],
      lower: [0, 0, 0],
      upper: [0.4, 0.2, 0.1],
    },
    {
      impulse: "infl",
      response: "gdp",
      irf: [0, -0.1, -0.05],
      lower: [0, -0.3, -0.2],
      upper: [0, 0.1, 0.1],
    },
    {
      impulse: "infl",
      response: "infl",
      irf: [1, 0.6, 0.3],
      lower: [0.8, 0.4, 0.1],
      upper: [1.2, 0.8, 0.5],
    },
  ],
});

const eventData = statData({
  reference: -1,
  window: [-2, 1],
  estimates: [
    {
      relTime: -2,
      estimate: 0.05,
      se: 0.1,
      ciLow: -0.15,
      ciHigh: 0.25,
      pValue: 0.6,
      n: 30,
      reference: false,
    },
    { relTime: -1, estimate: 0, se: null, ciLow: null, ciHigh: null, n: 30, reference: true },
    {
      relTime: 0,
      estimate: 0.4,
      se: 0.1,
      ciLow: 0.2,
      ciHigh: 0.6,
      pValue: 0.001,
      n: 30,
      reference: false,
    },
    {
      relTime: 1,
      estimate: 0.9,
      se: 0.1,
      ciLow: 0.7,
      ciHigh: 1.1,
      pValue: 0.001,
      n: 30,
      reference: false,
    },
  ],
  preTrend: { statistic: 0.25, df: [1, 39], pValue: 0.62 },
});

const FIXTURES: [string, ChartData][] = [
  ["kaplan_meier", km],
  ["forest", forest],
  ["coefficient", coefficients],
  ["marginal_effects", marginal],
  ["roc", rocData],
  ["precision_recall", prData],
  ["lorenz", lorenzData],
  ["quantile", quantileData],
  ["impulse_response", irfData],
  ["event_study", eventData],
];

describe("econometric registry entries", () => {
  it("registers every §8 plot, each valid", () => {
    expect(entries).toHaveLength(10);
    for (const entry of entries) {
      expect(validateEntry(entry)).toEqual([]);
      expect(entry.family).toBe("econometric");
      for (const p of entry.paramsSchema) {
        if (p.type !== "column") expect(entry.defaults.params).toHaveProperty(p.name);
      }
    }
  });

  it.each(FIXTURES)("%s renders the same option in light and dark (snapshot)", (key, data) => {
    expect(render(key, data)).toMatchSnapshot("light");
    expect(render(key, data, {}, dark)).toMatchSnapshot("dark");
  });

  it.each(FIXTURES)("%s survives an empty result", (key) => {
    expect(() => render(key, statData({}))).not.toThrow();
  });
});

describe("Kaplan-Meier", () => {
  it("draws step curves, stepped bands, censor ticks and the log-rank p", () => {
    expect(stepPoints([0, 6, 7], [1, 0.9, 0.8])).toEqual({
      x: [0, 6, 6, 7, 7],
      y: [1, 1, 0.9, 0.9, 0.8],
    });
    const o = render("kaplan_meier", km, {}, light, {
      encodings: { time: [{ field: "weeks" }], event: [{ field: "relapse" }] },
    });
    const curves = o.series.filter((s: Opt) => s.type === "line" && s.step === "end");
    expect(curves.map((s: Opt) => s.name)).toEqual(["6-MP", "Placebo"]);
    // The 6-MP curve continues to the last censoring time (35).
    expect(curves[0].data.at(-1)).toEqual([35, 0.753, null]);
    const ticks = o.series.filter((s: Opt) => s.type === "scatter");
    expect(ticks[0].data).toEqual([
      [6, 0.857],
      [9, 0.807],
      [35, 0.753],
    ]);
    expect(o.graphic.style.text).toBe("Log-rank p = < 0.001");
    expect(o.xAxis.name).toBe("weeks");
    const bare = render("kaplan_meier", km, { showCi: false, showCensored: false });
    expect(bare.series).toHaveLength(2);
    const medians = render("kaplan_meier", km, { showMedian: true });
    expect(medians.graphic.style.text).toContain("6-MP: median 23 [13, –]");
  });
});

describe("interval plots", () => {
  it("forest: studies, subtotals and overall diamond, log axis for ratios", () => {
    const o = render("forest", forest);
    expect(o.xAxis.type).toBe("log");
    // First row on top: the category axis runs bottom-up.
    expect(o.yAxis.data).toEqual(["Overall", "RCT (subtotal)", "A", "Obs (subtotal)", "B"]);
    const diamonds = o.series.filter((s: Opt) => s.type === "custom")[1];
    expect(diamonds.data).toHaveLength(3);
    expect(o.series[0].markLine.data).toEqual([{ xAxis: 1 }]);
    const sizes = o.series[0].data.map((d: Opt) => d.symbolSize);
    // Points run bottom-up too (A then B): A (60% weight) gets the bigger marker.
    expect(sizes[0]).toBeGreaterThan(sizes[1]);
  });

  it("coefficients: hides the intercept, exponentiates on request", () => {
    const o = render("coefficient", coefficients);
    expect(o.yAxis.data).toEqual(["plan: Pro", "age"]);
    expect(o.xAxis.name).toBe("Coefficient");
    const ratios = render("coefficient", coefficients, { exponentiate: true, showIntercept: true });
    expect(ratios.xAxis).toMatchObject({ type: "log", name: "Odds ratio" });
    expect(ratios.series[0].data.at(-1).value[0]).toBeCloseTo(Math.exp(-1));
    expect(ratios.series[0].markLine.data).toEqual([{ xAxis: 1 }]);
    // Row 0 (the bottom row) keeps its whisker on a log axis.
    expect(ratios.series[1].data.map((d: Opt) => d.value[0])).toContain(0);
    expect(ratios.yAxis.axisLine.onZero).toBe(false);
    const sorted = render("coefficient", coefficients, { sort: "estimate" });
    expect(sorted.yAxis.data).toEqual(["plan: Pro", "age"]);
  });

  it("marginal effects on the probability scale", () => {
    const o = render("marginal_effects", marginal);
    expect(o.xAxis.name).toBe("Change in P(churned = 1)");
    expect(o.series[1].type).toBe("custom"); // the shared error-bar primitive
    expect(o.series[1].data.map((d: Opt) => d.value)).toContainEqual([1, -0.014, -0.006]);
    expect(o.legend).toBeUndefined();
  });
});

describe("curves", () => {
  it("ROC names curves with AUC and draws the chance line", () => {
    const o = render("roc", rocData);
    expect(o.series[0].name).toBe("model_a (AUC 0.875 [0.6, 1])");
    expect(o.series[0].markPoint.data[0].coord).toEqual([0.5, 1]);
    expect(o.series.at(-1).data).toEqual([
      [0, 0],
      [1, 1],
    ]);
  });

  it("PR draws the no-skill baseline at the prevalence", () => {
    const o = render("precision_recall", prData);
    expect(o.series[0].name).toBe("s (AP 0.833)");
    expect(o.series[0].markLine.data).toEqual([{ yAxis: 0.5 }]);
  });

  it("Lorenz shows Gini and the line of equality", () => {
    const o = render("lorenz", lorenzData);
    expect(o.series[0].name).toBe("All (Gini 0.267)");
    expect(o.series[0].areaStyle).toBeDefined();
    expect(o.series).toHaveLength(2);
  });

  it("quantile plot marks the quartiles for one group", () => {
    const o = render("quantile", quantileData);
    expect(o.series[0].type).toBe("scatter");
    expect(o.series[0].markLine.data.map((d: Opt) => d.xAxis)).toEqual([0.25, 0.5, 0.75]);
  });
});

describe("impulse responses", () => {
  it("lays out a response x impulse grid, or one impulse's responses", () => {
    expect(panelLayout(1, 2, { top: 10, bottom: 10, left: 10, right: 10, gap: 10 })).toEqual([
      { left: 10, top: 10, width: 35, height: 80 },
      { left: 55, top: 10, width: 35, height: 80 },
    ]);
    const o = render("impulse_response", irfData);
    expect(o.grid).toHaveLength(4);
    expect(o.graphic.map((g: Opt) => g.style.text)).toEqual([
      "gdp → gdp",
      "infl → gdp",
      "gdp → infl",
      "infl → infl",
    ]);
    const one = render("impulse_response", irfData, { impulse: "infl" });
    expect(one.grid).toHaveLength(2);
    expect(one.series.filter((s: Opt) => s.name).map((s: Opt) => s.name)).toEqual([
      "infl → gdp",
      "infl → infl",
    ]);
  });
});

describe("event study", () => {
  it("draws estimates with error bars, the reference and the pre-trend test", () => {
    const o = render("event_study", eventData);
    expect(o.series[0].data.map((d: Opt) => d.value)).toEqual([
      [-2, 0.05],
      [-1, 0],
      [0, 0.4],
      [1, 0.9],
    ]);
    expect(o.series[1].data).toHaveLength(3);
    expect(o.series[0].markLine.data[1].xAxis).toBe(-0.5);
    expect(o.graphic.style.text).toBe("Pre-trend test: F(1, 39) = 0.25, p = 0.620");
    expect(o.xAxis).toMatchObject({ min: -2.5, max: 1.5 });
  });
});

describe("tooltips escape user data", () => {
  const XSS = '<img src=x onerror="alert(1)">';
  const safe = (html: unknown) => {
    expect(typeof html).toBe("string");
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
  };

  it("forest: study labels and subgroup keys", () => {
    const data = statData({
      rows: [
        { label: XSS, group: XSS, estimate: 0.2, lower: 0.1, upper: 0.3, se: 0.05, weight: 100 },
      ],
      pooled: { k: 1, estimate: 0.2, lower: 0.1, upper: 0.3, se: 0.05, pValue: 0.01 },
      subgroups: [{ key: XSS, k: 1, estimate: 0.2, lower: 0.1, upper: 0.3 }],
      measure: "difference",
      nullValue: 0,
    });
    const o = render("forest", data);
    safe(o.series[0].data[0].tooltip);
    safe(o.series[0].tooltip.formatter({ data: o.series[0].data[0] }));
    const diamonds = o.series.filter((s: Opt) => s.type === "custom")[1];
    const subtotal = diamonds.data.find((d: Opt) => String(d.tooltip).includes("img"));
    safe(subtotal.tooltip);
  });

  it("coefficient and marginal effects: categorical levels in term names", () => {
    const terms = [
      { term: `plan: ${XSS}`, estimate: 0.1, se: 0.05, ciLow: 0, ciHigh: 0.2, pValue: 0.04 },
    ];
    const coef = render("coefficient", statData({ model: "ols", terms }));
    safe(coef.series[0].data[0].tooltip);
    const me = render("marginal_effects", statData({ scale: "outcome", effects: terms }));
    safe(me.series[0].data[0].tooltip);
  });

  it("ROC and PR best-point tooltips", () => {
    const rocOption = render("roc", rocData);
    safe(rocOption.series[0].markPoint.tooltip.formatter({ name: XSS, value: "0.5" }));
    const pr = render("precision_recall", prData);
    safe(pr.series[0].markPoint.tooltip.formatter({ name: XSS, value: "0.5" }));
  });
});
