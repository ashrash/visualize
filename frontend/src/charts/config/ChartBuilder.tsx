import { useId } from "react";
import type { AggFn, ChartSpec, Column, Encoding } from "../../api/types";
import { listPlots } from "../core/registry";

// Phase 0 builder: plot type, x, y and aggregate. ui-charts replaces this with a config panel
// generated from each plot's channel and param schemas (MVP §4).

const AGGREGATES: { value: AggFn; label: string }[] = [
  { value: "sum", label: "Sum" },
  { value: "mean", label: "Mean" },
  { value: "median", label: "Median" },
  { value: "min", label: "Min" },
  { value: "max", label: "Max" },
  { value: "count", label: "Count" },
  { value: "count_distinct", label: "Distinct count" },
];

const COUNT_ROWS = "__count__";

interface ChartBuilderProps {
  spec: ChartSpec;
  columns: Column[];
  onChange: (spec: ChartSpec) => void;
}

function Field({ label, children }: { label: string; children: (id: string) => React.ReactNode }) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium text-muted">
        {label}
      </label>
      {children(id)}
    </div>
  );
}

const selectClass =
  "rounded-md border border-line bg-bg px-2 py-1 text-sm text-fg focus-visible:outline-2 focus-visible:outline-accent";

export function ChartBuilder({ spec, columns, onChange }: ChartBuilderProps) {
  const encodings = spec.encodings ?? {};
  const x = encodings.x?.[0];
  const y = encodings.y?.[0];
  const measures = columns.filter((c) => c.semantic === "quantitative");

  const setEncoding = (channel: string, encoding: Encoding | undefined) => {
    const next = { ...encodings };
    if (encoding) next[channel] = [encoding];
    else delete next[channel];
    onChange({ ...spec, encodings: next });
  };

  return (
    <div className="flex flex-wrap items-end gap-3">
      <Field label="Chart type">
        {(id) => (
          <select
            id={id}
            className={selectClass}
            value={spec.plotType}
            onChange={(e) =>
              onChange({ ...spec, plotType: e.target.value as ChartSpec["plotType"] })
            }
          >
            {listPlots().map((plot) => (
              <option key={plot.key} value={plot.key}>
                {plot.label}
              </option>
            ))}
          </select>
        )}
      </Field>
      <Field label="Category (x)">
        {(id) => (
          <select
            id={id}
            className={selectClass}
            value={x?.field ?? ""}
            onChange={(e) => setEncoding("x", { field: e.target.value })}
          >
            {columns.map((c) => (
              <option key={c.name} value={c.name}>
                {c.name}
              </option>
            ))}
          </select>
        )}
      </Field>
      <Field label="Value (y)">
        {(id) => (
          <select
            id={id}
            className={selectClass}
            value={y?.field ?? COUNT_ROWS}
            onChange={(e) =>
              setEncoding(
                "y",
                e.target.value === COUNT_ROWS
                  ? { agg: "count" }
                  : { field: e.target.value, agg: y?.agg === "count" ? "sum" : (y?.agg ?? "sum") },
              )
            }
          >
            <option value={COUNT_ROWS}>(row count)</option>
            {measures.map((c) => (
              <option key={c.name} value={c.name}>
                {c.name}
              </option>
            ))}
          </select>
        )}
      </Field>
      {y?.field ? (
        <Field label="Aggregate">
          {(id) => (
            <select
              id={id}
              className={selectClass}
              value={y.agg ?? "sum"}
              onChange={(e) => setEncoding("y", { ...y, agg: e.target.value as AggFn })}
            >
              {AGGREGATES.map((a) => (
                <option key={a.value} value={a.value}>
                  {a.label}
                </option>
              ))}
            </select>
          )}
        </Field>
      ) : null}
    </div>
  );
}
