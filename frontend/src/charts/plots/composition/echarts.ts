import { FunnelChart, SunburstChart, TreemapChart } from "echarts/charts";
import { echarts } from "../../core/echarts";

// Composition-family series types not registered by core/echarts.ts. bar/line/area reuse the
// basic family's cartesian renderer, and mosaic/marimekko draw "custom" rects (already
// registered globally), so only funnel/treemap/sunburst need their own series types here.
echarts.use([FunnelChart, TreemapChart, SunburstChart]);
