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

// Registered once, here: the boxplot series (box). Violin draws its own custom `renderItem`
// (see violin.ts for why it does not use the official `@echarts-x/custom-violin` series), which
// needs no separate registration beyond the `CustomChart` ui-charts already registers. Other
// families register their own series types the same way (charts/core/echarts.ts).
echarts.use(BoxplotChart);

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
