import type { components } from "./schema";

type Schemas = components["schemas"];

export type Dataset = Schemas["Dataset"];
export type DatasetVersion = Schemas["DatasetVersion"];
export type Column = Schemas["Column"];
export type ColumnSemantic = Schemas["ColumnSemantic"];
export type ChartSpec = Schemas["ChartSpec"];
export type ChartStyle = Schemas["ChartStyle"];
export type Encoding = Schemas["Encoding"];
export type AggFn = Schemas["AggFn"];
export type PlotType = Schemas["PlotType"];
export type PlotFamily = Schemas["PlotFamily"];
export type ChartDataRequest = Schemas["ChartDataRequest"];
export type ChartDataMeta = Schemas["ChartDataMeta"];
export type RowsPageMeta = Schemas["RowsPageMeta"];
export type Project = Schemas["Project"];
export type ProjectSummary = Schemas["ProjectSummary"];
export type Predicate =
  | Schemas["InPredicate"]
  | Schemas["EqPredicate"]
  | Schemas["RangePredicate"]
  | Schemas["IsNullPredicate"];
