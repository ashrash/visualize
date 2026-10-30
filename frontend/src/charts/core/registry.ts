import type { PlotType } from "../../api/types";
import type { PlotEntry } from "./types";

// Each plot family registers itself: plots/<family>/index.ts default-exports its entries.
// Plot agents add a folder; nobody edits this file to register a plot.
const modules = import.meta.glob<{ default: PlotEntry[] }>("../plots/*/index.ts", {
  eager: true,
});

const entries = new Map<PlotType, PlotEntry>();
for (const [path, module] of Object.entries(modules)) {
  for (const entry of module.default) {
    if (entries.has(entry.key)) throw new Error(`plot "${entry.key}" registered twice (${path})`);
    entries.set(entry.key, entry);
  }
}

export function getPlot(key: PlotType): PlotEntry | undefined {
  return entries.get(key);
}

export function listPlots(): PlotEntry[] {
  return [...entries.values()];
}
