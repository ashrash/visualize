import type { ECharts } from "echarts/core";
import { echarts } from "./echarts";

/**
 * Linked charts: charts of one `interactions.linkGroup` share ECharts' `connect` group, so
 * tooltips (axis pointer), zoom, legend toggles and highlights follow across them. Zoom is synced
 * by percentage, so it is only meaningful for charts sharing an x domain. This is purely visual;
 * which rows are filtered is the dashboard's cross-filter bus.
 */
const members = new Map<string, Set<ECharts>>();

/** Add a chart to a link group. Returns the function that removes it again. */
export function joinLinkGroup(chart: ECharts, group: string): () => void {
  let set = members.get(group);
  if (!set) {
    set = new Set();
    members.set(group, set);
  }
  set.add(chart);
  chart.group = group;
  // Idempotent: marks the group connected, whether this is its first or its tenth chart.
  echarts.connect(group);
  let left = false;
  return () => {
    if (left) return;
    left = true;
    set.delete(chart);
    // A disposed chart ignores this; a live one stops syncing with the group.
    chart.group = "";
    if (set.size === 0) {
      members.delete(group);
      echarts.disconnect(group);
    }
  };
}

/** Charts currently in a group (for tests and diagnostics). */
export function linkGroupSize(group: string): number {
  return members.get(group)?.size ?? 0;
}
