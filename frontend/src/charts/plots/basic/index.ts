import type { PlotEntry } from "../../core/types";
import { barEntries } from "./bar";
import { area, line } from "./line";
import { donut, pie } from "./pie";

// Plots.md §1: bar (+ horizontal, grouped, stacked), line, area, pie, donut.
const entries: PlotEntry[] = [...barEntries, line, area, pie, donut];
export default entries;
