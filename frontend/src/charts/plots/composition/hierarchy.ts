import "./echarts";
import type { ChartSpec, Column } from "../../../api/types";
import { hierarchyToTree } from "../../core/primitives/hierarchy";
import { applyStyle } from "../../core/style";
import {
  type ChannelSchema,
  type ChartData,
  type ChartTheme,
  definePlot,
  type ParamSchema,
  type StyleSection,
} from "../../core/types";

const SECTIONS: StyleSection[] = ["title", "tooltip", "numberFormat", "palette"];

const encodingsSchema: ChannelSchema[] = [
  {
    name: "label",
    label: "Hierarchy levels",
    required: true,
    multiple: true,
    accepts: ["nominal", "ordinal"],
    aggregate: false,
    hint: "One field per level, from outermost to innermost.",
  },
  {
    name: "size",
    label: "Size",
    required: false,
    multiple: false,
    accepts: ["quantitative"],
    aggregate: true,
    hint: "Leave empty to count rows.",
  },
];

const paramsSchema: ParamSchema[] = [
  {
    name: "maxLeaves",
    label: "Max leaves (rest grouped as Other)",
    affectsData: true,
    type: "int",
    min: 1,
    max: 2000,
    default: 50,
  },
];

function levelFields(data: ChartData): string[] {
  return Object.keys(data.columns)
    .filter((key) => /^label\d+$/.test(key))
    .sort((a, b) => Number(a.slice(5)) - Number(b.slice(5)));
}

function rows(data: ChartData): Record<string, unknown>[] {
  const levels = levelFields(data);
  const size = data.columns.size ?? [];
  const n = data.rowCount;
  const out: Record<string, unknown>[] = [];
  for (let i = 0; i < n; i++) {
    const row: Record<string, unknown> = { size: size[i] };
    for (const level of levels) row[level] = data.columns[level]?.[i];
    out.push(row);
  }
  return out;
}

function buildOption(
  kind: "treemap" | "sunburst",
  spec: ChartSpec,
  data: ChartData,
  theme: ChartTheme,
): Record<string, unknown> {
  const levels = levelFields(data);
  const tree = hierarchyToTree({ rows: rows(data), levels, value: "size" });
  const base = {
    data: tree,
    label: { color: theme.text },
    itemStyle: { borderColor: theme.background ?? "transparent" },
  };
  const series =
    kind === "treemap"
      ? [
          {
            type: "treemap",
            roam: false,
            breadcrumb: { show: tree.length > 0, itemStyle: { color: theme.grid } },
            label: { show: true, color: theme.text },
            upperLabel: { show: levels.length > 1, height: 22, color: theme.text },
            itemStyle: {
              borderColor: theme.background ?? "transparent",
              borderWidth: 2,
              gapWidth: 1,
            },
            data: tree,
          },
        ]
      : [
          {
            type: "sunburst",
            radius: [0, "90%"],
            ...base,
            label: { color: theme.text, rotate: "radial" },
          },
        ];
  const option = { tooltip: { trigger: "item" }, series };
  return applyStyle(option, spec, theme, undefined, { defaultTrigger: "item" });
}

function recommend(columns: Column[]): number {
  const levels = columns.filter((c) => c.semantic === "nominal" || c.semantic === "ordinal").length;
  const measure = columns.some((c) => c.semantic === "quantitative");
  return levels >= 2 && measure ? 0.35 : levels >= 1 ? 0.1 : 0.02;
}

export const treemap = definePlot({
  key: "treemap",
  label: "Treemap",
  family: "composition",
  description: "A hierarchy as nested rectangles sized by value.",
  encodingsSchema,
  paramsSchema,
  styleSections: SECTIONS,
  defaults: { params: { maxLeaves: 50 } },
  render: (spec, data, theme) => buildOption("treemap", spec, data, theme),
  recommend,
});

export const sunburst = definePlot({
  key: "sunburst",
  label: "Sunburst",
  family: "composition",
  description: "A hierarchy as nested rings sized by value.",
  encodingsSchema,
  paramsSchema,
  styleSections: SECTIONS,
  defaults: { params: { maxLeaves: 50 } },
  render: (spec, data, theme) => buildOption("sunburst", spec, data, theme),
  recommend,
});
