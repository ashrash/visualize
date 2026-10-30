import violinInstaller from "@echarts-x/custom-violin";
import { BoxplotChart } from "echarts/charts";
import { echarts } from "../../core/echarts";
import type { PlotEntry } from "../../core/types";
import { box } from "./box";
import { density } from "./density";
import { ecdf } from "./ecdf";
import { frequencyPolygon, histogram } from "./histogram";
import { qq } from "./qq";
import { strip, swarm } from "./strip";
import { violin } from "./violin";

// Registered once, here: the boxplot series (box) and the official custom-violin renderer
// (violin). Other families register their own series types the same way (charts/core/echarts.ts).
echarts.use(BoxplotChart);
echarts.use(violinInstaller);

// Plots.md §2: histogram, density, box, violin, strip, swarm, Q-Q, ECDF, frequency polygon.
const entries: PlotEntry[] = [
  histogram,
  density,
  box,
  violin,
  strip,
  swarm,
  qq,
  ecdf,
  frequencyPolygon,
];
export default entries;
