import { HeatmapChart } from "echarts/charts";
import { VisualMapComponent } from "echarts/components";
import { echarts } from "../../core/echarts";
import type { PlotEntry } from "../../core/types";
import { contour } from "./contour";
import { correlationHeatmap, correlogram } from "./correlation";
import { hexbin } from "./hexbin";
import { pair } from "./pair";
import { regression, residual } from "./regression";
import { bubble, scatter } from "./scatter";

// Registered once, here: the heatmap series, used by both correlation_heatmap and contour (see
// contour.ts for why it does not use the official custom-contour series). VisualMapComponent is
// already registered by ui-charts, but `echarts.use` de-dupes, so listing it again here is
// harmless and keeps this module self-contained.
echarts.use([HeatmapChart, VisualMapComponent]);

// Plots.md §3: scatter, bubble, pair, correlation heatmap, correlogram, hexbin, contour,
// regression, residual.
const entries: PlotEntry[] = [
  scatter,
  bubble,
  pair,
  correlationHeatmap,
  correlogram,
  hexbin,
  contour,
  regression,
  residual,
];
export default entries;
