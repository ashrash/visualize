import { GraphChart, SankeyChart } from "echarts/charts";
import { echarts } from "../../core/echarts";

// Specialized-family series types not registered by core/echarts.ts. gantt, pareto, error_bar,
// lollipop and dumbbell reuse bar/line/scatter/custom (already registered globally); ridgeline
// and raincloud build on the offset-KDE primitive's line series. Only sankey/alluvial (sankey)
// and network (graph) need their own series types.
echarts.use([SankeyChart, GraphChart]);
