import "./echarts";
import type { Column } from "../../../api/types";
import type { Obj } from "../../core/series";
import { applyStyle } from "../../core/style";
import { type ChannelSchema, type ChartData, definePlot, type ParamSchema } from "../../core/types";
import { graphLinks, graphNodes } from "./statValues";

const GRAPH_PARAMS: ParamSchema[] = [
  {
    name: "maxNodes",
    label: "Max nodes (rest grouped as Other)",
    affectsData: true,
    type: "int",
    min: 2,
    max: 500,
    default: 40,
  },
];

const sourceTargetChannels: ChannelSchema[] = [
  {
    name: "source",
    label: "Source",
    required: true,
    multiple: false,
    accepts: ["nominal", "ordinal"],
    aggregate: false,
  },
  {
    name: "target",
    label: "Target",
    required: true,
    multiple: false,
    accepts: ["nominal", "ordinal"],
    aggregate: false,
  },
  {
    name: "size",
    label: "Value",
    required: false,
    multiple: false,
    accepts: ["quantitative"],
    aggregate: true,
    hint: "Leave empty to count rows.",
  },
];

function hasTwoOrMoreCategorical(columns: Column[]): boolean {
  return columns.filter((c) => c.semantic === "nominal" || c.semantic === "ordinal").length >= 2;
}

export const sankey = definePlot({
  key: "sankey",
  label: "Sankey diagram",
  family: "specialized",
  description: "Flows between two sets of categories, sized by value.",
  encodingsSchema: sourceTargetChannels,
  paramsSchema: GRAPH_PARAMS,
  styleSections: ["title", "tooltip", "palette"],
  defaults: { params: { maxNodes: 40 } },
  render(spec, data: ChartData, theme, ctx) {
    const nodes = graphNodes(data.stat?.values);
    const links = graphLinks(data.stat?.values);
    const option: Obj = {
      tooltip: { trigger: "item" },
      series: [
        {
          type: "sankey",
          emphasis: { focus: "adjacency" },
          data: nodes.map((n) => ({ name: n.name })),
          links,
          label: { color: theme.text },
          lineStyle: { color: "gradient", opacity: 0.4, curveness: 0.5 },
          itemStyle: { borderColor: theme.background ?? "transparent" },
        },
      ],
    };
    return applyStyle(option, spec, theme, ctx, { defaultTrigger: "item" });
  },
  warnings: (_spec, data) => data.meta.warnings ?? [],
  recommend: (columns) => (hasTwoOrMoreCategorical(columns) ? 0.25 : 0.03),
});

export const alluvial = definePlot({
  key: "alluvial",
  label: "Alluvial diagram",
  family: "specialized",
  description: "Flows across three or more stages, sized by value.",
  encodingsSchema: [
    {
      name: "label",
      label: "Stages",
      required: true,
      multiple: true,
      accepts: ["nominal", "ordinal"],
      aggregate: false,
      hint: "Two or more fields, from first to last stage.",
    },
    {
      name: "size",
      label: "Value",
      required: false,
      multiple: false,
      accepts: ["quantitative"],
      aggregate: true,
      hint: "Leave empty to count rows.",
    },
  ],
  paramsSchema: GRAPH_PARAMS,
  styleSections: ["title", "tooltip", "palette"],
  defaults: { params: { maxNodes: 40 } },
  render(spec, data: ChartData, theme, ctx) {
    const nodes = graphNodes(data.stat?.values);
    const links = graphLinks(data.stat?.values);
    const option: Obj = {
      tooltip: { trigger: "item" },
      series: [
        {
          type: "sankey",
          emphasis: { focus: "trajectory" },
          data: nodes.map((n) => ({
            name: n.name,
            depth: n.depth,
            label: { formatter: n.label ?? n.name },
          })),
          links,
          label: {
            color: theme.text,
            formatter: (p: { data: { label?: string; name: string } }) =>
              p.data.label ?? p.data.name,
          },
          lineStyle: { color: "gradient", opacity: 0.4, curveness: 0.5 },
          itemStyle: { borderColor: theme.background ?? "transparent" },
        },
      ],
    };
    return applyStyle(option, spec, theme, ctx, { defaultTrigger: "item" });
  },
  warnings: (_spec, data) => data.meta.warnings ?? [],
  recommend: (columns) =>
    columns.filter((c) => c.semantic === "nominal" || c.semantic === "ordinal").length >= 3
      ? 0.25
      : 0.02,
});

export const network = definePlot({
  key: "network",
  label: "Network graph",
  family: "specialized",
  description: "Nodes and edges, laid out by force.",
  encodingsSchema: sourceTargetChannels,
  paramsSchema: GRAPH_PARAMS,
  styleSections: ["title", "tooltip", "palette"],
  defaults: { params: { maxNodes: 40 } },
  render(spec, data: ChartData, theme, ctx) {
    const nodes = graphNodes(data.stat?.values);
    const links = graphLinks(data.stat?.values);
    const maxValue = Math.max(1, ...nodes.map((n) => n.value));
    const option: Obj = {
      tooltip: { trigger: "item" },
      series: [
        {
          type: "graph",
          layout: "force",
          roam: true,
          draggable: true,
          force: { repulsion: 120, edgeLength: [30, 120] },
          data: nodes.map((n) => ({
            name: n.name,
            value: n.value,
            symbolSize: 8 + 24 * Math.sqrt(n.value / maxValue),
          })),
          edges: links.map((l) => ({ source: l.source, target: l.target, value: l.value })),
          label: { show: true, color: theme.text, position: "right" },
          lineStyle: { color: "source", opacity: 0.4, curveness: 0.1 },
          itemStyle: { borderColor: theme.background ?? "transparent" },
          emphasis: { focus: "adjacency" },
        },
      ],
    };
    return applyStyle(option, spec, theme, ctx, { defaultTrigger: "item" });
  },
  warnings: (_spec, data) => data.meta.warnings ?? [],
  recommend: (columns) => (hasTwoOrMoreCategorical(columns) ? 0.2 : 0.03),
});
