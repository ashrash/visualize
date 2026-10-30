import { api, unwrap } from "./client";
import type {
  FormulaCheckRequest,
  FormulaCheckResult,
  FormulaFunction,
  MaterializeRequest,
  MaterializeResult,
  PipelineInspectRequest,
  PipelineInspectResult,
} from "./types";

// transform endpoints, on the typed client. All are stateless: the pipeline and every project
// pipeline it references travel in the request, so unsaved edits are inspectable immediately.
// Previews return Arrow and live in pipelinePreview.ts.

/** POST /api/pipelines/inspect: per-step schema, status and errors. */
export async function inspectPipeline(
  body: PipelineInspectRequest,
  signal?: AbortSignal,
): Promise<PipelineInspectResult> {
  return unwrap(await api.POST("/api/pipelines/inspect", { body, signal }));
}

/** POST /api/pipelines/formula/check */
export async function checkFormula(
  body: FormulaCheckRequest,
  signal?: AbortSignal,
): Promise<FormulaCheckResult> {
  return unwrap(await api.POST("/api/pipelines/formula/check", { body, signal }));
}

/** GET /api/pipelines/formula/functions */
export async function listFormulaFunctions(signal?: AbortSignal): Promise<FormulaFunction[]> {
  return unwrap(await api.GET("/api/pipelines/formula/functions", { signal }));
}

/** POST /api/pipelines/materialize -> {jobs: {stepId: jobId}, ready} */
export async function materializePipeline(
  body: MaterializeRequest,
  signal?: AbortSignal,
): Promise<MaterializeResult> {
  return unwrap(await api.POST("/api/pipelines/materialize", { body, signal }));
}
