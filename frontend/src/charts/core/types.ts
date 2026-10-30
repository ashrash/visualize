import type { EChartsCoreOption } from "echarts/core";
import type { ColumnarResult } from "../../api/arrow";
import type {
  ChartDataMeta,
  ChartSpec,
  Column,
  ColumnSemantic,
  PlotFamily,
  PlotType,
} from "../../api/types";

/** A channel a plot accepts (x, y, color, size, …). The config panel is generated from these. */
export interface ChannelSchema {
  name: string;
  label: string;
  required: boolean;
  multiple: boolean;
  accepts: ColumnSemantic[];
  /** Whether the channel takes an aggregate (sum, mean, …). */
  aggregate: boolean;
}

export type ChartData = ColumnarResult<ChartDataMeta>;

/** Resolved design tokens for charts (read from CSS variables). */
export interface ChartTheme {
  palette: string[];
  text: string;
  textMuted: string;
  border: string;
  fontFamily: string;
}

export interface PlotEntry {
  key: PlotType;
  label: string;
  family: PlotFamily;
  channels: ChannelSchema[];
  /** Pure: same inputs, same option. */
  render(spec: ChartSpec, data: ChartData, theme: ChartTheme): EChartsCoreOption;
  /** 0–1 suitability for the given columns (chart recommendations). */
  recommend?(columns: Column[]): number;
}
