import { HeatmapChart, MapChart } from "echarts/charts";
import { GeoComponent } from "echarts/components";
import { echarts } from "../../core/echarts";

// The geo family's own series/components (CLAUDE.md core/echarts.ts: "plot families with
// other series types ... register them in their own modules"). ScatterChart and
// VisualMapComponent are already registered globally.
echarts.use([MapChart, HeatmapChart, GeoComponent]);

export { echarts };
