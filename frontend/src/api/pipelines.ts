import type { ColumnarResult } from "./arrow";
import { getJson, postArrow, postJson } from "./http";
import type {
  FormulaCheckRequest,
  FormulaCheckResult,
  FormulaFunction,
  MaterializeRequest,
  MaterializeResult,
  PipelineInspectRequest,
  PipelineInspectResult,
  PipelinePreviewMeta,
  PipelinePreviewRequest,
} from "./types";

// transform endpoints (flag `pipelineApi`). All are stateless: the pipeline and every project
// pipeline it references travel in the request, so unsaved edits are inspectable immediately.

/** POST /api/pipelines/inspect */
export const inspectPipeline = (body: PipelineInspectRequest, signal?: AbortSignal) =>
  postJson<PipelineInspectRequest, PipelineInspectResult>("/api/pipelines/inspect", body, signal);

/** POST /api/pipelines/preview -> Arrow, meta = PipelinePreviewMeta */
export const previewPipeline = (
  body: PipelinePreviewRequest,
  signal?: AbortSignal,
): Promise<ColumnarResult<PipelinePreviewMeta>> =>
  postArrow<PipelinePreviewRequest, PipelinePreviewMeta>("/api/pipelines/preview", body, signal);

/** POST /api/pipelines/formula/check */
export const checkFormula = (body: FormulaCheckRequest, signal?: AbortSignal) =>
  postJson<FormulaCheckRequest, FormulaCheckResult>("/api/pipelines/formula/check", body, signal);

/** GET /api/pipelines/formula/functions */
export const listFormulaFunctions = (signal?: AbortSignal) =>
  getJson<FormulaFunction[]>("/api/pipelines/formula/functions", signal);

/** POST /api/pipelines/materialize -> {jobs: {stepId: jobId}, ready} */
export const materializePipeline = (body: MaterializeRequest, signal?: AbortSignal) =>
  postJson<MaterializeRequest, MaterializeResult>("/api/pipelines/materialize", body, signal);
