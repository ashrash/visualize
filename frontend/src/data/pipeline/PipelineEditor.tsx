import { useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { materializePipeline } from "../../api/pipelines";
import type { Column, Dataset, Pipeline, StepSchema } from "../../api/types";
import { Button } from "../../components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "../../components/ui/dropdown-menu";
import { toast } from "../../components/ui/toast";
import { useJobsStore } from "../../state/jobs";
import { PipelinePreview } from "./PipelinePreview";
import { StepForm } from "./StepForm";
import { columnsAt, describeStepError } from "./schema";
import { createStep, type PipelineAction, STEP_META, STEP_OPS, summarizeStep } from "./stepReducer";
import { referencedPipelines, usePipelineInspect } from "./usePipelineApi";

export function PipelineEditor({
  pipeline,
  sourceColumns,
  datasets,
  pipelines = [],
  dispatch,
}: {
  pipeline: Pipeline;
  sourceColumns: Column[];
  datasets: Dataset[];
  /** All project pipelines (join/union can reference them); sent with every pipeline request. */
  pipelines?: Pipeline[];
  dispatch: (action: PipelineAction) => void;
}) {
  const steps = pipeline.steps ?? [];
  const inspectQuery = usePipelineInspect(pipeline, pipelines);
  const inspect = inspectQuery.data ?? null;
  const checking = inspectQuery.isPlaceholderData || inspectQuery.isFetching;
  const schemaOf = (id: string): StepSchema | undefined =>
    inspect?.steps.find((s) => s.stepId === id);
  const queryClient = useQueryClient();
  const [materializing, setMaterializing] = useState(false);
  // Stop following materialize jobs when the editor goes away.
  const materializeStops = useRef<(() => void)[]>([]);
  useEffect(
    () => () => {
      for (const stop of materializeStops.current) stop();
      materializeStops.current = [];
    },
    [],
  );

  const materializeNow = async () => {
    setMaterializing(true);
    try {
      const result = await materializePipeline({
        pipeline,
        pipelines: referencedPipelines(pipeline, pipelines),
      });
      const ids = Object.values(result.jobs);
      if (ids.length === 0) {
        toast("Nothing to materialize", { description: "All materialized steps are ready." });
        return;
      }
      let left = ids.length;
      for (const id of ids) {
        const stop = useJobsStore.getState().trackId(id, (job) => {
          if (job.status !== "succeeded") {
            toast("Materialization failed", {
              variant: "error",
              description: job.error ?? undefined,
            });
          }
          left -= 1;
          if (left === 0) {
            setMaterializing(false);
            void queryClient.invalidateQueries({ queryKey: ["pipeline-inspect"] });
          }
        });
        materializeStops.current.push(stop);
      }
      return;
    } catch (error) {
      toast("Couldn't start materialization", {
        variant: "error",
        description: error instanceof Error ? error.message : undefined,
      });
    }
    setMaterializing(false);
  };
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const listRef = useRef<HTMLOListElement>(null);
  const addRef = useRef<HTMLButtonElement>(null);
  // Where focus should land after the next render (moving or deleting re-renders the list).
  const pendingFocus = useRef<{ id: string; act: string } | { index: number } | null>(null);

  useEffect(() => {
    const target = pendingFocus.current;
    if (!target) return;
    pendingFocus.current = null;
    const items = Array.from(
      listRef.current?.querySelectorAll<HTMLElement>("[data-step-id]") ?? [],
    );
    if ("index" in target) {
      // After a delete: the step now at that position, else the one before, else the add button.
      const next = items[Math.min(target.index, items.length - 1)];
      (next?.querySelector<HTMLElement>("[data-act=select]") ?? addRef.current)?.focus();
      return;
    }
    const item = items.find((el) => el.dataset.stepId === target.id);
    (
      item?.querySelector<HTMLElement>(`[data-act=${target.act}]`) ??
      item?.querySelector<HTMLElement>("[data-act=select]")
    )?.focus();
  });
  const selected = steps.find((s) => s.id === selectedId) ?? steps.at(-1);

  const add = (op: (typeof STEP_OPS)[number]) => {
    const step = createStep(op, columnsAt(sourceColumns, steps, null, inspect));
    dispatch({ type: "add", step });
    setSelectedId(step.id);
  };

  return (
    <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
      <section aria-labelledby="steps-heading" className="flex min-h-0 flex-col gap-2">
        <div className="flex items-center justify-between">
          <h3
            id="steps-heading"
            className="text-xs font-semibold uppercase tracking-wide text-muted"
          >
            Steps
          </h3>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button ref={addRef} size="sm" variant="primary">
                <Plus size={14} aria-hidden="true" />
                Add step
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent className="max-h-80 overflow-y-auto">
              {STEP_OPS.map((op) => (
                <DropdownMenuItem key={op} onSelect={() => add(op)}>
                  <span>
                    {STEP_META[op].label}
                    <span className="block text-xs text-muted">{STEP_META[op].description}</span>
                  </span>
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        {steps.length === 0 ? (
          <p className="text-sm text-muted">No steps yet. Add one to transform the data.</p>
        ) : (
          <ol ref={listRef} className="flex flex-col gap-1 overflow-y-auto">
            {steps.map((step, index) => {
              const active = step.id === selected?.id;
              return (
                <li
                  key={step.id}
                  data-step-id={step.id}
                  className={`flex items-center gap-1 rounded-md border p-1 ${active ? "border-accent bg-surface-2" : "border-line"}`}
                >
                  <input
                    type="checkbox"
                    className="accent-accent"
                    checked={step.enabled}
                    aria-label={`Enable step ${index + 1}, ${STEP_META[step.op].label}`}
                    onChange={() => dispatch({ type: "toggle", id: step.id })}
                  />
                  <button
                    type="button"
                    data-act="select"
                    aria-current={active ? "step" : undefined}
                    onClick={() => setSelectedId(step.id)}
                    className={`min-w-0 flex-1 rounded px-1 text-left ${step.enabled ? "" : "opacity-50"}`}
                  >
                    <span className="block truncate text-sm font-medium">
                      {index + 1}. {STEP_META[step.op].label}
                    </span>
                    <span className="block truncate text-xs text-muted">{summarizeStep(step)}</span>
                    {schemaOf(step.id)?.status === "error" ? (
                      <span className="block text-xs text-danger" data-step-status="error">
                        {describeStepError(
                          schemaOf(step.id)?.error ?? {
                            message: "This step has an error",
                            field: null,
                            start: null,
                            end: null,
                          },
                        )}
                      </span>
                    ) : null}
                    {schemaOf(step.id)?.status === "blocked" ? (
                      <span className="block text-xs text-warning" data-step-status="blocked">
                        Blocked by an earlier step
                      </span>
                    ) : null}
                    {schemaOf(step.id)?.materialized === "ready" ? (
                      <span className="block text-xs text-success">Materialized</span>
                    ) : null}
                    {schemaOf(step.id)?.materialized === "pending" ? (
                      <span className="block text-xs text-muted">Materialization pending</span>
                    ) : null}
                  </button>
                  <Button
                    size="icon"
                    variant="ghost"
                    data-act="up"
                    aria-label={`Move step ${index + 1} up`}
                    aria-disabled={index === 0}
                    onClick={() => {
                      if (index === 0) return;
                      pendingFocus.current = { id: step.id, act: "up" };
                      dispatch({ type: "move", id: step.id, to: index - 1 });
                    }}
                  >
                    <ArrowUp size={14} aria-hidden="true" />
                  </Button>
                  <Button
                    size="icon"
                    variant="ghost"
                    data-act="down"
                    aria-label={`Move step ${index + 1} down`}
                    aria-disabled={index === steps.length - 1}
                    onClick={() => {
                      if (index === steps.length - 1) return;
                      pendingFocus.current = { id: step.id, act: "down" };
                      dispatch({ type: "move", id: step.id, to: index + 1 });
                    }}
                  >
                    <ArrowDown size={14} aria-hidden="true" />
                  </Button>
                  <Button
                    size="icon"
                    variant="ghost"
                    data-act="delete"
                    aria-label={`Delete step ${index + 1}`}
                    onClick={() => {
                      pendingFocus.current = { index };
                      dispatch({ type: "remove", id: step.id });
                    }}
                  >
                    <Trash2 size={14} aria-hidden="true" />
                  </Button>
                </li>
              );
            })}
          </ol>
        )}
      </section>

      <section
        aria-label="Step settings and preview"
        className="flex min-h-0 min-w-0 flex-col gap-3 overflow-y-auto"
      >
        {selected ? (
          <>
            <div>
              <h3 className="text-sm font-semibold">{STEP_META[selected.op].label}</h3>
              <p className="text-xs text-muted">{STEP_META[selected.op].description}</p>
            </div>
            <StepForm
              // Remount per step so uncontrolled editors (formula) start from this step's text.
              key={selected.id}
              step={selected}
              columns={columnsAt(sourceColumns, steps, selected.id, inspect)}
              datasets={datasets}
              pipeline={pipeline}
              pipelines={pipelines}
              stepError={schemaOf(selected.id)?.error ?? null}
              onChange={(step) => dispatch({ type: "replace", step })}
            />
            {selected.op === "join" || selected.op === "pivot" ? (
              <div className="flex flex-wrap items-center gap-3 text-sm">
                <label className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    className="accent-accent"
                    checked={selected.materialize}
                    onChange={(e) =>
                      dispatch({
                        type: "replace",
                        step: { ...selected, materialize: e.target.checked },
                      })
                    }
                  />
                  Materialize (write this step to Parquet)
                </label>
                {steps.some((s) => s.materialize) ? (
                  <Button size="sm" onClick={() => void materializeNow()} disabled={materializing}>
                    {materializing ? "Materializing…" : "Materialize now"}
                  </Button>
                ) : null}
              </div>
            ) : null}
            <section aria-label="Preview" className="flex flex-col gap-1">
              <h4 className="text-xs font-semibold uppercase tracking-wide text-muted">
                Preview after this step
              </h4>
              <PipelinePreview
                key={selected.id}
                pipeline={pipeline}
                pipelines={pipelines}
                stepId={selected.id}
                schema={schemaOf(selected.id)}
                checking={checking}
              />
            </section>
          </>
        ) : (
          <p className="text-sm text-muted">Select or add a step to edit it.</p>
        )}
      </section>
    </div>
  );
}
