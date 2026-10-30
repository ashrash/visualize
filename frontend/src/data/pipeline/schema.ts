import type { Column, PipelineStep } from "../../api/types";

/**
 * The columns visible to the step at `stepId` (or after the last step when null), starting from
 * the source dataset's columns.
 *
 * This is the single seam for schema propagation. It is a best-effort client-side estimate.
 * TODO(transform): replace with the pipeline schema endpoint so types and join/pivot outputs
 * are exact, e.g. `POST /api/pipelines/schema {pipeline, upToStepId} -> Column[]`.
 */
export function columnsAt(
  source: Column[],
  steps: PipelineStep[],
  stepId: string | null = null,
): Column[] {
  let columns = source;
  for (const step of steps) {
    if (step.id === stepId) break;
    if (step.enabled) columns = applyStep(columns, step);
  }
  return columns;
}

function synthetic(name: string, type: string, semantic: Column["semantic"]): Column {
  return { name, type, semantic, nullCount: 0, approxDistinct: 0 };
}

const byName = (columns: Column[]) => new Map(columns.map((c) => [c.name, c]));

function applyStep(columns: Column[], step: PipelineStep): Column[] {
  switch (step.op) {
    case "select": {
      const index = byName(columns);
      return step.fields.flatMap((f) => {
        const c = index.get(f);
        return c ? [c] : [];
      });
    }
    case "rename":
      return columns.map((c) => {
        const next = step.mapping[c.name];
        return next ? { ...c, name: next } : c;
      });
    case "calc":
      return step.name
        ? [
            ...columns.filter((c) => c.name !== step.name),
            synthetic(step.name, "DOUBLE", "quantitative"),
          ]
        : columns;
    case "group_aggregate": {
      const index = byName(columns);
      const keys = step.by.flatMap((f) => {
        const c = index.get(f);
        return c ? [c] : [];
      });
      return [
        ...keys,
        ...step.aggregations.map((a) => synthetic(a.asName, "DOUBLE", "quantitative")),
      ];
    }
    case "cast": {
      const casts = new Map(step.casts.map((c) => [c.field, c.to]));
      return columns.map((c) => {
        const to = casts.get(c.name);
        if (!to) return c;
        const semantic: Column["semantic"] =
          to === "integer" || to === "float"
            ? "quantitative"
            : to === "date" || to === "timestamp"
              ? "temporal"
              : "nominal";
        return { ...c, type: to.toUpperCase(), semantic };
      });
    }
    case "pivot":
      // Output columns depend on the data; only the index columns are known without the server.
      return columns.filter((c) => step.index.includes(c.name));
    case "unpivot": {
      const ids = columns.filter((c) => step.idFields.includes(c.name));
      return [
        ...ids,
        synthetic(step.nameField, "VARCHAR", "nominal"),
        synthetic(step.valueField, "DOUBLE", "quantitative"),
      ];
    }
    default:
      // filter, sort, fill/drop missing, dedupe, union, normalize keep the shape; join's right-hand
      // columns are unknown until the server reports them.
      return columns;
  }
}
