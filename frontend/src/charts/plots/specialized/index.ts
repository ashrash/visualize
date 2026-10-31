import "./echarts";
import type { PlotEntry } from "../../core/types";
import { dumbbell } from "./dumbbell";
import { errorBar } from "./errorBar";
import { gantt } from "./gantt";
import { alluvial, network, sankey } from "./graph";
import { lollipop } from "./lollipop";
import { pareto } from "./pareto";
import { raincloud } from "./raincloud";
import { ridgeline } from "./ridgeline";

// Plots.md §9 (Specialized): sankey, network, alluvial, gantt, pareto, error bar, lollipop,
// dumbbell, ridgeline, raincloud.
const entries: PlotEntry[] = [
  sankey,
  network,
  alluvial,
  gantt,
  pareto,
  errorBar,
  lollipop,
  dumbbell,
  ridgeline,
  raincloud,
];
export default entries;
