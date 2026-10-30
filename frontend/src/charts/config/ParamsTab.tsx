import type { ChartSpec, Column } from "../../api/types";
import type { ParamSchema, PlotEntry, ShowIf } from "../core/types";
import { CheckboxField, ColorField, NumberField, SelectField, TextField } from "./controls";
import { setParam } from "./spec";

/** The current value of a param: what the spec holds, else the schema default. */
export function paramValue(spec: ChartSpec, schema: ParamSchema): unknown {
  const value = spec.params?.[schema.name];
  if (value !== undefined) return value;
  return "default" in schema ? schema.default : undefined;
}

function holds(spec: ChartSpec, entry: PlotEntry, cond: ShowIf): boolean {
  const other = entry.paramsSchema.find((p) => p.name === cond.param);
  const bound = (spec.encodings?.[cond.param] ?? []).filter((e) => e.field || e.agg);
  if ("atLeast" in cond) return bound.length >= cond.atLeast;
  if (other) return paramValue(spec, other) === cond.equals;
  return bound.length > 0 === cond.equals;
}

/** Whether a param is shown: no `showIf`, or any of its conditions holds (see `ShowIf`). */
export function isVisible(spec: ChartSpec, entry: PlotEntry, schema: ParamSchema): boolean {
  const cond = schema.showIf;
  if (!cond) return true;
  return (Array.isArray(cond) ? cond : [cond]).some((c) => holds(spec, entry, c));
}

const clamp = (n: number, min?: number, max?: number) =>
  Math.min(max ?? Number.POSITIVE_INFINITY, Math.max(min ?? Number.NEGATIVE_INFINITY, n));

function ParamControl({
  schema,
  value,
  columns,
  onChange,
}: {
  schema: ParamSchema;
  value: unknown;
  columns: Column[];
  onChange(value: unknown): void;
}) {
  switch (schema.type) {
    case "number":
    case "int":
      return (
        <NumberField
          label={schema.label}
          hint={schema.help}
          value={typeof value === "number" ? value : null}
          min={schema.min}
          max={schema.max}
          step={schema.type === "int" ? 1 : schema.step}
          onChange={(n) =>
            onChange(
              n === null
                ? schema.default
                : clamp(schema.type === "int" ? Math.round(n) : n, schema.min, schema.max),
            )
          }
        />
      );
    case "boolean":
      return (
        <CheckboxField
          label={schema.label}
          hint={schema.help}
          checked={value === true}
          onChange={onChange}
        />
      );
    case "select":
      return (
        <SelectField
          label={schema.label}
          hint={schema.help}
          value={typeof value === "string" ? value : schema.default}
          options={schema.options}
          onChange={onChange}
        />
      );
    case "text":
      return (
        <TextField
          label={schema.label}
          hint={schema.help}
          value={typeof value === "string" ? value : ""}
          onChange={onChange}
        />
      );
    case "color":
      return (
        <ColorField
          label={schema.label}
          value={typeof value === "string" ? value : schema.default}
          onChange={(c) => onChange(c ?? schema.default)}
        />
      );
    case "column":
      return (
        <SelectField
          label={schema.label}
          hint={schema.help}
          value={typeof value === "string" ? value : ""}
          options={[
            { value: "", label: "(none)" },
            ...columns
              .filter((c) => schema.accepts.includes(c.semantic))
              .map((c) => ({ value: c.name, label: c.name })),
          ]}
          onChange={onChange}
        />
      );
    case "range": {
      const [lo, hi] = Array.isArray(value) ? (value as [number, number]) : schema.default;
      return (
        <div className="flex gap-2">
          <NumberField
            label={`${schema.label} (from)`}
            value={lo}
            min={schema.min}
            max={schema.max}
            step={schema.step}
            onChange={(n) => onChange([n ?? schema.default[0], hi])}
          />
          <NumberField
            label={`${schema.label} (to)`}
            value={hi}
            min={schema.min}
            max={schema.max}
            step={schema.step}
            onChange={(n) => onChange([lo, n ?? schema.default[1]])}
          />
        </div>
      );
    }
  }
}

/** The Chart tab: controls generated from the plot's `paramsSchema`. */
export function ParamsTab({
  spec,
  entry,
  columns,
  onChange,
}: {
  spec: ChartSpec;
  entry: PlotEntry;
  columns: Column[];
  onChange(spec: ChartSpec): void;
}) {
  const visible = entry.paramsSchema.filter((p) => isVisible(spec, entry, p));
  if (visible.length === 0) {
    return <p className="text-sm text-muted">This chart type has no extra settings.</p>;
  }
  return (
    <div className="flex flex-col gap-3">
      {visible.map((schema) => (
        <ParamControl
          key={schema.name}
          schema={schema}
          value={paramValue(spec, schema)}
          columns={columns}
          onChange={(value) => onChange(setParam(spec, schema.name, value))}
        />
      ))}
    </div>
  );
}
