import { offsetKde } from "../../core/primitives/offsetKde";
import type { Obj } from "../../core/series";
import { applyStyle } from "../../core/style";
import { resolveTheme } from "../../core/theme";
import { type ChartData, type ChartTheme, definePlot, type StyleSection } from "../../core/types";
import { type RaincloudGroup, raincloudGroups } from "./statValues";

const SECTIONS: StyleSection[] = ["title", "axes", "tooltip", "palette"];

interface RenderApi {
  coord(point: [number, number]): [number, number];
}

interface BoxItem {
  offset: number;
  box: RaincloudGroup["box"];
}

/** A small deterministic PRNG (mulberry32) so the jittered strip is stable across re-renders. */
function rng(seed: number): () => number {
  let a = seed;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export const raincloud = definePlot({
  key: "raincloud",
  label: "Raincloud plot",
  family: "specialized",
  description: "A half violin (density), a box and a jittered strip of points, per group.",
  encodingsSchema: [
    {
      name: "value",
      label: "Value",
      required: true,
      multiple: false,
      accepts: ["quantitative"],
      aggregate: false,
    },
    {
      name: "group",
      label: "Group by",
      required: false,
      multiple: false,
      accepts: ["nominal", "ordinal"],
      aggregate: false,
    },
  ],
  paramsSchema: [
    {
      name: "bandwidth",
      label: "Bandwidth rule",
      affectsData: true,
      type: "select",
      default: "silverman",
      options: [
        { value: "silverman", label: "Silverman" },
        { value: "scott", label: "Scott" },
      ],
    },
    {
      name: "adjust",
      label: "Bandwidth adjustment",
      affectsData: true,
      type: "number",
      min: 0.1,
      max: 5,
      step: 0.1,
      default: 1,
    },
    {
      name: "gridSize",
      label: "Curve resolution",
      affectsData: true,
      type: "int",
      min: 32,
      max: 1024,
      default: 200,
    },
    {
      name: "pointSample",
      label: "Points sampled per group",
      affectsData: true,
      type: "int",
      min: 10,
      max: 5000,
      default: 300,
    },
    {
      name: "jitter",
      label: "Jitter",
      affectsData: false,
      type: "number",
      min: 0,
      max: 1,
      step: 0.05,
      default: 0.15,
    },
  ],
  styleSections: SECTIONS,
  defaults: {
    params: { bandwidth: "silverman", adjust: 1, gridSize: 200, pointSample: 300, jitter: 0.15 },
  },
  render(spec, data: ChartData, theme: ChartTheme, ctx) {
    const groups = raincloudGroups(data.stat?.values);
    const n = Math.max(1, groups.length);
    const valueLabel = data.meta.fields.value ?? "Value";
    const kdeGroups = groups.map((g) => ({
      name: g.key ?? (groups.length === 1 ? valueLabel : "(null)"),
      x: g.density.x,
      density: g.density.density,
    }));
    const {
      series: kdeSeries,
      yAxis,
      step,
    } = offsetKde({
      groups: kdeGroups,
      overlap: 0,
      colors: theme.palette,
      theme,
    });
    const t = resolveTheme(theme);
    const band = step * 0.45;
    const boxItems: BoxItem[] = groups.map((g, i) => ({ offset: (n - 1 - i) * step, box: g.box }));

    const boxSeries: Obj = {
      type: "custom",
      name: "box",
      silent: true,
      z: 6,
      data: boxItems.map((_b, i) => i),
      renderItem(params: { dataIndex: number }, api: RenderApi) {
        const item = boxItems[params.dataIndex];
        if (!item || item.box.q1 === null || item.box.q3 === null) {
          return { type: "group", children: [] };
        }
        const topY = item.offset - band * 0.15;
        const bottomY = item.offset - band * 0.55;
        const [xq1, y1] = api.coord([item.box.q1, topY]);
        const [xq3, y2] = api.coord([item.box.q3, bottomY]);
        const mid = (y1 + y2) / 2;
        const children: Obj[] = [];
        if (item.box.lo !== null && item.box.hi !== null) {
          const [xLo] = api.coord([item.box.lo, topY]);
          const [xHi] = api.coord([item.box.hi, topY]);
          children.push({
            type: "line",
            shape: { x1: xLo, y1: mid, x2: xHi, y2: mid },
            style: { stroke: t.textMuted, lineWidth: 1 },
          });
        }
        children.push({
          type: "rect",
          shape: {
            x: Math.min(xq1, xq3),
            y: Math.min(y1, y2),
            width: Math.abs(xq3 - xq1),
            height: Math.abs(y2 - y1),
          },
          style: { fill: t.background ?? "#fff", stroke: t.text, lineWidth: 1.5 },
        });
        if (item.box.median !== null) {
          const [xMed] = api.coord([item.box.median, topY]);
          children.push({
            type: "line",
            shape: { x1: xMed, y1: Math.min(y1, y2), x2: xMed, y2: Math.max(y1, y2) },
            style: { stroke: t.text, lineWidth: 2 },
          });
        }
        return { type: "group", children };
      },
    };

    const rnd = rng(1234567);
    const points: [number, number][] = [];
    groups.forEach((g, i) => {
      const base = (n - 1 - i) * step - band * 0.7;
      for (const v of g.points) points.push([v, base - rnd() * band * 0.5]);
    });
    const pointsSeries: Obj = {
      type: "scatter",
      name: "points",
      symbolSize: 4,
      data: points,
      itemStyle: { color: theme.textMuted, opacity: 0.45 },
      tooltip: { show: false },
    };

    const option: Obj = {
      xAxis: { type: "value", name: data.meta.fields.value ?? undefined },
      yAxis,
      series: [...kdeSeries, boxSeries, pointsSeries],
      tooltip: { trigger: "item" },
    };
    return applyStyle(option, spec, theme, ctx, { axes: false, defaultTrigger: "item" });
  },
  recommend(columns) {
    const hasGroup = columns.some((c) => c.semantic === "nominal" || c.semantic === "ordinal");
    const measure = columns.some((c) => c.semantic === "quantitative");
    return measure ? (hasGroup ? 0.25 : 0.12) : 0.02;
  },
});
