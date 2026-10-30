import type { ColumnarResult } from "./arrow";
import { postArrow } from "./httpArrow";
import type { PipelinePreviewMeta, PipelinePreviewRequest } from "./types";

/** POST /api/pipelines/preview -> Arrow, meta = PipelinePreviewMeta */
export const previewPipeline = (
  body: PipelinePreviewRequest,
  signal?: AbortSignal,
): Promise<ColumnarResult<PipelinePreviewMeta>> =>
  postArrow<PipelinePreviewRequest, PipelinePreviewMeta>("/api/pipelines/preview", body, signal);
