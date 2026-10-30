import "./echarts";
import type { PlotEntry } from "../../core/types";
import { funnel } from "./funnel";
import { sunburst, treemap } from "./hierarchy";
import { marimekko, mosaic } from "./mosaic";
import { percentStackedBar, stackedArea } from "./stackedArea";
import { waterfall } from "./waterfall";

// Plots.md §5 (Comparison & Composition): stacked area, 100% stacked bar, waterfall, funnel,
// treemap, sunburst, mosaic, marimekko.
const entries: PlotEntry[] = [
  stackedArea,
  percentStackedBar,
  waterfall,
  funnel,
  treemap,
  sunburst,
  mosaic,
  marimekko,
];
export default entries;
