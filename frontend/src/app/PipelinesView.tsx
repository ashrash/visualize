import { useEffect } from "react";
import { Button } from "../components/ui/button";
import { Field, Select } from "../components/ui/input";
import { PipelineEditor } from "../data/pipeline/PipelineEditor";
import { createPipeline, type PipelineAction, pipelineReducer } from "../data/pipeline/stepReducer";
import { useActiveDataset } from "../data/useActiveDataset";
import { useProjectStore } from "../state/projectStore";
import { useSelection } from "../state/selection";

export default function PipelinesView() {
  const { dataset, datasets, columns, loading } = useActiveDataset();
  const project = useProjectStore((s) => s.project);
  const update = useProjectStore((s) => s.update);
  const pipelineId = useSelection((s) => s.pipelineId);
  const selectPipeline = useSelection((s) => s.selectPipeline);

  const forDataset = (project?.pipelines ?? []).filter((p) => p.source.id === dataset?.id);
  const pipeline = forDataset.find((p) => p.id === pipelineId) ?? forDataset[0];

  useEffect(() => {
    if (pipeline && pipeline.id !== pipelineId) selectPipeline(pipeline.id);
  }, [pipeline, pipelineId, selectPipeline]);

  if (!dataset) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-muted">
        {loading ? "Loading…" : "Import a dataset to build a pipeline."}
      </div>
    );
  }

  const create = () => {
    const next = createPipeline(dataset);
    update((p) => ({ ...p, pipelines: [...(p.pipelines ?? []), next] }));
    selectPipeline(next.id);
  };
  const dispatch = (action: PipelineAction) => {
    if (!pipeline) return;
    update((p) => ({
      ...p,
      pipelines: (p.pipelines ?? []).map((x) =>
        x.id === pipeline.id ? pipelineReducer(x, action) : x,
      ),
    }));
  };

  return (
    <>
      <div className="flex flex-wrap items-end gap-3">
        <h2 className="text-base font-semibold">Pipeline for {dataset.name}</h2>
        {forDataset.length > 1 ? (
          <Field label="Pipeline" className="w-56">
            {(p) => (
              <Select
                {...p}
                value={pipeline?.id ?? ""}
                options={forDataset.map((x) => ({ value: x.id, label: x.name }))}
                onChange={(e) => selectPipeline(e.target.value)}
              />
            )}
          </Field>
        ) : null}
        {pipeline ? <Button onClick={create}>New pipeline</Button> : null}
      </div>
      {pipeline ? (
        <PipelineEditor
          key={pipeline.id}
          pipeline={pipeline}
          sourceColumns={columns}
          datasets={datasets.filter((d) => d.id !== dataset.id)}
          pipelines={project?.pipelines ?? []}
          dispatch={dispatch}
        />
      ) : (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 text-sm text-muted">
          <p>This dataset has no pipeline yet.</p>
          <Button variant="primary" onClick={create}>
            Create a pipeline
          </Button>
        </div>
      )}
    </>
  );
}
