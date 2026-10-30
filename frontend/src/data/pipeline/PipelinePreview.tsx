import { useMemo, useState } from "react";
import { previewPipeline } from "../../api/pipelinePreview";
import type { Pipeline, PipelinePreviewMeta, SortKey, StepSchema } from "../../api/types";
import { type FetchPage, GridView, useGridHead } from "../GridView";
import { describeStepError, fieldToColumn } from "./schema";
import { referencedPipelines, useDebounced } from "./usePipelineApi";

/** Rows after one step (or the whole pipeline), in the same grid used for datasets. */
function PreviewGrid({
  pipeline,
  pipelines,
  stepId,
}: {
  pipeline: Pipeline;
  pipelines: Pipeline[];
  stepId: string | null;
}) {
  const [sort, setSort] = useState<SortKey[]>([]);
  // The preview follows edits, but not every keystroke.
  const payload = useDebounced(
    JSON.stringify({ pipeline, pipelines: referencedPipelines(pipeline, pipelines), stepId }),
    500,
  );
  const gridKey = `pipeline:${payload}`;

  const fetchPage: FetchPage = ({ offset, limit, signal }) => {
    const base = JSON.parse(payload) as {
      pipeline: Pipeline;
      pipelines: Pipeline[];
      stepId: string | null;
    };
    return previewPipeline({ ...base, offset, limit, sort, filters: [] }, signal);
  };
  const head = useGridHead({ gridKey, sort, filters: {}, fetchPage });
  const meta = head.data?.meta as PipelinePreviewMeta | undefined;
  const columns = useMemo(() => (meta?.columns ?? []).map(fieldToColumn), [meta]);

  if (head.isError) {
    return (
      <p role="alert" className="text-sm text-danger">
        {head.error.message}
      </p>
    );
  }
  if (!meta) return <p className="text-sm text-muted">Loading preview…</p>;
  return (
    <div className="flex h-72 flex-col gap-1">
      <p className="text-xs text-muted" aria-live="polite">
        {meta.totalRows.toLocaleString()} rows after {stepId ? "this step" : "all steps"} ·{" "}
        {Math.round(meta.ms)} ms
      </p>
      {meta.warnings.map((w) => (
        <p key={w} className="text-xs text-warning">
          {w}
        </p>
      ))}
      <div className="min-h-0 flex-1">
        <GridView
          gridKey={gridKey}
          label="Pipeline preview"
          columns={columns}
          totalRows={meta.totalRows}
          fetchPage={fetchPage}
          sort={sort}
          onSortChange={setSort}
          filters={{}}
          controlsEnabled
          controlsTip="Filtering isn't available in the preview"
        />
      </div>
    </div>
  );
}

/**
 * Rows after one step. The server's inspect result decides first: a step with an error (or one
 * blocked by an earlier error) is explained instead of previewed, so editing never fires
 * requests that are known to fail.
 */
export function PipelinePreview({
  pipeline,
  pipelines,
  stepId,
  schema,
  checking,
}: {
  pipeline: Pipeline;
  pipelines: Pipeline[];
  stepId: string | null;
  schema: StepSchema | undefined;
  /** The inspect result on screen is stale or still loading. */
  checking: boolean;
}) {
  if (checking || !schema) return <p className="text-sm text-muted">Checking the pipeline…</p>;
  if (schema.status === "error") {
    return (
      <p role="status" className="text-sm text-danger">
        Preview unavailable:{" "}
        {schema.error ? describeStepError(schema.error) : "this step has an error"}
      </p>
    );
  }
  if (schema.status === "blocked") {
    return <p className="text-sm text-muted">Preview unavailable: an earlier step has an error.</p>;
  }
  if (schema.status === "disabled") {
    return <p className="text-sm text-muted">This step is turned off.</p>;
  }
  return <PreviewGrid pipeline={pipeline} pipelines={pipelines} stepId={stepId} />;
}
