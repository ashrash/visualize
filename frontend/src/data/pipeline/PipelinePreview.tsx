import { useMemo, useState } from "react";
import { previewPipeline } from "../../api/pipelinePreview";
import type { Pipeline, PipelinePreviewMeta, SortKey } from "../../api/types";
import { type FetchPage, GridView, useGridHead } from "../GridView";
import { fieldToColumn } from "./schema";
import { referencedPipelines, useDebounced } from "./usePipelineApi";

/** Rows after one step (or the whole pipeline), in the same grid used for datasets. */
export function PipelinePreview({
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
