import type { Column, PipelineInspectResult, PipelineStep, SchemaField } from "../../api/types";

/** A server schema field as a Column (profile counts are not part of a pipeline schema). */
export function fieldToColumn(field: SchemaField): Column {
  return {
    name: field.name,
    type: field.type,
    semantic: field.semantic,
    nullCount: 0,
    approxDistinct: 0,
  };
}

/**
 * The columns visible to the step at `stepId` (or after the last step when null), starting from
 * the source dataset's columns. This is the single seam for schema propagation.
 *
 * With a server inspect result (transform's `POST /api/pipelines/inspect`) the answer is exact:
 * each step's output columns come from its StepSchema, and a disabled or failed step leaves the
 * columns unchanged. Without one (flag off, request pending) a client-side estimate is used.
 */
export function columnsAt(
  source: Column[],
  steps: PipelineStep[],
  stepId: string | null = null,
  inspect?: PipelineInspectResult | null,
): Column[] {
  let columns = inspect ? inspect.sourceColumns.map(fieldToColumn) : source;
  for (const step of steps) {
    if (step.id === stepId) break;
    if (!step.enabled) continue;
    const schema = inspect?.steps.find((s) => s.stepId === step.id);
    if (inspect && schema) {
      if (schema.status === "ok") columns = schema.columns.map(fieldToColumn);
    } else if (!inspect) {
      columns = applyStep(columns, step);
    }
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
