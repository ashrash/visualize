import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { checkFormula, inspectPipeline, listFormulaFunctions } from "../../api/pipelines";
import type { Pipeline } from "../../api/types";

export function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

/**
 * Pipelines this one references (join/union sources of kind "pipeline"), transitively. Every
 * pipeline request is stateless, so these travel along with the pipeline itself.
 */
export function referencedPipelines(pipeline: Pipeline, all: Pipeline[]): Pipeline[] {
  const byId = new Map(all.map((p) => [p.id, p]));
  const seen = new Set<string>([pipeline.id]);
  const out: Pipeline[] = [];
  const visit = (p: Pipeline) => {
    const refs = [
      p.source,
      ...(p.steps ?? []).flatMap((s) =>
        s.op === "join" ? [s.right] : s.op === "union" ? s.others : [],
      ),
    ];
    for (const ref of refs) {
      if (ref.kind !== "pipeline" || seen.has(ref.id)) continue;
      const target = byId.get(ref.id);
      seen.add(ref.id);
      if (target) {
        out.push(target);
        visit(target);
      }
    }
  };
  visit(pipeline);
  return out;
}

/** Server schema of every step (status, columns, errors), re-fetched as the pipeline is edited. */
export function usePipelineInspect(pipeline: Pipeline | undefined, all: Pipeline[]) {
  const body = useDebounced(
    JSON.stringify(
      pipeline
        ? { pipeline, pipelines: referencedPipelines(pipeline, all), includeSql: false }
        : null,
    ),
    400,
  );
  return useQuery({
    queryKey: ["pipeline-inspect", body],
    queryFn: ({ signal }) => inspectPipeline(JSON.parse(body), signal),
    // `body` lags the pipeline by the debounce; never send the placeholder.
    enabled: pipeline !== undefined && body !== "null",
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
}

export function useFormulaFunctions() {
  return useQuery({
    queryKey: ["formula-functions"],
    queryFn: ({ signal }) => listFormulaFunctions(signal),
    staleTime: Number.POSITIVE_INFINITY,
  });
}

/** Debounced server check of one formula, in the context of the steps before it. */
export function useFormulaCheck(args: {
  expression: string;
  pipeline: Pipeline | undefined;
  pipelines: Pipeline[];
  beforeStepId: string | null;
}) {
  const { pipeline } = args;
  const body = useDebounced(
    JSON.stringify({
      expression: args.expression,
      pipeline,
      pipelines: pipeline ? referencedPipelines(pipeline, args.pipelines) : [],
      beforeStepId: args.beforeStepId,
    }),
    400,
  );
  const query = useQuery({
    queryKey: ["formula-check", body],
    queryFn: ({ signal }) => checkFormula(JSON.parse(body), signal),
    enabled: pipeline !== undefined && args.expression.trim() !== "",
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
  // The verdict only applies to the text it was computed for (keepPreviousData can hand back
  // the result of an earlier expression).
  const forExpression = (JSON.parse(body) as { expression: string }).expression;
  const current = forExpression === args.expression && !query.isPlaceholderData;
  return { ...query, data: current ? query.data : undefined };
}
