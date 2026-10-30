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
export type ProjectVersion = Schemas["ProjectVersion"];
export type ImportOptions = Schemas["ImportOptions"];
export type ImportFromPathRequest = Schemas["ImportFromPathRequest"];
export type SourceKind = Schemas["SourceKind"];
export type SourceRef = Schemas["SourceRef"];
export type Pipeline = Schemas["Pipeline"];
export type PipelineStep = NonNullable<Pipeline["steps"]>[number];
export type StepOp = PipelineStep["op"];
export type StepOf<Op extends StepOp> = Extract<PipelineStep, { op: Op }>;
export type DataTypeName = Schemas["DataType"];
export type FillStrategy = Schemas["FillStrategy"];
export type StagedFile = Schemas["StagedFile"];
export type ImportPreviewRequest = Schemas["ImportPreviewRequest"];
export type ImportPreviewMeta = Schemas["ImportPreviewMeta"];
export type ImportRequest = Schemas["ImportRequest"];
export type RefreshRequest = Schemas["RefreshRequest"];
export type DatasetUpdate = Schemas["DatasetUpdate"];
export type Job = Schemas["Job"];
export type JobStatus = Schemas["JobStatus"];
export type RowsRequest = Schemas["RowsRequest"];
export type SortKey = Schemas["SortKey"];
export type ColumnValuesRequest = Schemas["ColumnValuesRequest"];
export type ColumnValues = Schemas["ColumnValues"];
export type ExportRequest = Schemas["ExportRequest"];
export type ExportFormat = Schemas["ExportFormat"];
export type SchemaField = Schemas["SchemaField"];
export type StepSchema = Schemas["StepSchema"];
export type StepError = Schemas["StepError"];
export type PipelineInspectRequest = Schemas["PipelineInspectRequest"];
export type PipelineInspectResult = Schemas["PipelineInspectResult"];
export type PipelinePreviewRequest = Schemas["PipelinePreviewRequest"];
export type PipelinePreviewMeta = Schemas["PipelinePreviewMeta"];
export type FormulaCheckRequest = Schemas["FormulaCheckRequest"];
export type FormulaCheckResult = Schemas["FormulaCheckResult"];
export type FormulaFunction = Schemas["FormulaFunction"];
export type MaterializeRequest = Schemas["MaterializeRequest"];
export type MaterializeResult = Schemas["MaterializeResult"];
export type ProjectRename = Schemas["ProjectRename"];
export type ProjectDuplicate = Schemas["ProjectDuplicate"];
export type ProjectRestore = Schemas["ProjectRestore"];
export type ProjectImportResult = Schemas["ProjectImportResult"];
