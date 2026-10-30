import type {
  AggFn,
  Column,
  Dataset,
  DataTypeName,
  FillStrategy,
  Pipeline,
  PipelineStep,
  Predicate,
  StepError,
  StepOf,
} from "../../api/types";
import { Field, Input, Select } from "../../components/ui/input";
import { FormulaField } from "./FormulaField";
import { ColumnChecklist, ColumnSelect, RowList, removeAt, replaceAt } from "./fields";
import {
  changePredicateOp,
  createPredicate,
  formatScalar,
  PREDICATE_OPS,
  type PredicateOp,
} from "./predicates";
import { ScalarInput } from "./ScalarInput";

export interface FormContext {
  /** Columns visible to this step (after the steps above it). */
  columns: Column[];
  /** Other datasets, for join and union. */
  datasets: Dataset[];
  /** The pipeline being edited and all project pipelines, for server formula checks. */
  pipeline?: Pipeline;
  pipelines?: Pipeline[];
  /** The server's error for this step (from inspect), if any. */
  stepError?: StepError | null;
}

type FormProps<Op extends PipelineStep["op"]> = FormContext & {
  step: StepOf<Op>;
  onChange: (step: StepOf<Op>) => void;
};

const AGG_OPTIONS: { value: AggFn; label: string }[] = [
  { value: "count", label: "Count" },
  { value: "count_distinct", label: "Distinct count" },
  { value: "sum", label: "Sum" },
  { value: "mean", label: "Mean" },
  { value: "median", label: "Median" },
  { value: "min", label: "Min" },
  { value: "max", label: "Max" },
  { value: "std", label: "Std dev" },
  { value: "var", label: "Variance" },
];

const TYPE_OPTIONS: { value: DataTypeName; label: string }[] = [
  { value: "integer", label: "Integer" },
  { value: "float", label: "Decimal" },
  { value: "string", label: "Text" },
  { value: "boolean", label: "Boolean" },
  { value: "date", label: "Date" },
  { value: "timestamp", label: "Date and time" },
];

const FILL_OPTIONS: { value: FillStrategy; label: string }[] = [
  { value: "value", label: "Fixed value" },
  { value: "mean", label: "Mean" },
  { value: "median", label: "Median" },
  { value: "mode", label: "Most common" },
  { value: "forward", label: "Previous value" },
  { value: "backward", label: "Next value" },
];

const colOf = (columns: Column[], name: string) => columns.find((c) => c.name === name);

function PredicateRow({
  predicate,
  columns,
  onChange,
}: {
  predicate: Predicate;
  columns: Column[];
  onChange: (p: Predicate) => void;
}) {
  const column = colOf(columns, predicate.field);
  return (
    <>
      <ColumnSelect
        label="Column"
        columns={columns}
        value={predicate.field}
        onChange={(field) => onChange({ ...predicate, field })}
      />
      <Field label="Condition">
        {(p) => (
          <Select
            {...p}
            value={predicate.op}
            options={PREDICATE_OPS}
            onChange={(e) => onChange(changePredicateOp(predicate, e.target.value as PredicateOp))}
          />
        )}
      </Field>
      {predicate.op === "eq" ? (
        <Field label="Value">
          {(p) => (
            <ScalarInput
              id={p.id}
              aria-describedby={p["aria-describedby"]}
              column={column}
              value={predicate.value}
              onChange={(value) => onChange({ ...predicate, value })}
            />
          )}
        </Field>
      ) : null}
      {predicate.op === "in" || predicate.op === "not_in" ? (
        <Field label="Values" hint="Separate with commas">
          {(p) => (
            <ScalarInput
              list
              id={p.id}
              aria-describedby={p["aria-describedby"]}
              column={column}
              value={predicate.values}
              onChange={(values) => onChange({ ...predicate, values })}
            />
          )}
        </Field>
      ) : null}
      {predicate.op === "range" || predicate.op === "between_dates"
        ? (["min", "max"] as const).map((bound) => (
            <Field
              key={bound}
              label={bound === "min" ? "From (blank = open)" : "To (blank = open)"}
            >
              {(p) => (
                <Input
                  {...p}
                  type={predicate.op === "between_dates" ? "date" : "number"}
                  value={formatScalar(predicate[bound])}
                  onChange={(e) =>
                    onChange({
                      ...predicate,
                      [bound]:
                        e.target.value === ""
                          ? null
                          : predicate.op === "range"
                            ? Number(e.target.value)
                            : e.target.value,
                    })
                  }
                />
              )}
            </Field>
          ))
        : null}
      {predicate.op === "is_null" ? (
        <Field label="Keep rows that are">
          {(p) => (
            <Select
              {...p}
              value={predicate.isNull ? "null" : "notnull"}
              options={[
                { value: "null", label: "empty" },
                { value: "notnull", label: "not empty" },
              ]}
              onChange={(e) => onChange({ ...predicate, isNull: e.target.value === "null" })}
            />
          )}
        </Field>
      ) : null}
    </>
  );
}

function FilterForm(props: FormProps<"filter">) {
  const { step, columns, onChange } = props;
  const ctx: FormContext = props;
  const predicates = step.predicates ?? [];
  return (
    <>
      <RowList
        legend="Conditions"
        rows={predicates}
        addLabel="Add condition"
        onAdd={() =>
          onChange({
            ...step,
            predicates: [...predicates, createPredicate("eq", columns[0]?.name ?? "")],
          })
        }
        onRemove={(i) => onChange({ ...step, predicates: removeAt(predicates, i) })}
        renderRow={(predicate, i) => (
          <PredicateRow
            predicate={predicate}
            columns={columns}
            onChange={(next) => onChange({ ...step, predicates: replaceAt(predicates, i, next) })}
          />
        )}
      />
      <Field
        label="Formula (optional)"
        hint="Rows must also satisfy this expression, e.g. units > 10 AND region = 'West'"
      >
        {(p) => (
          <FormulaField
            id={p.id}
            describedBy={p["aria-describedby"]}
            stepId={step.id}
            ctx={ctx}
            label="Filter formula"
            value={step.expression ?? ""}
            onChange={(expression) => onChange({ ...step, expression: expression || null })}
          />
        )}
      </Field>
    </>
  );
}

function SortForm({ step, columns, onChange }: FormProps<"sort">) {
  return (
    <RowList
      legend="Sort keys"
      rows={step.keys}
      addLabel="Add key"
      onAdd={() =>
        onChange({
          ...step,
          keys: [
            ...step.keys,
            { field: columns[0]?.name ?? "", descending: false, nullsLast: true },
          ],
        })
      }
      onRemove={(i) => onChange({ ...step, keys: removeAt(step.keys, i) })}
      renderRow={(key, i) => (
        <>
          <ColumnSelect
            label="Column"
            columns={columns}
            value={key.field}
            onChange={(field) =>
              onChange({ ...step, keys: replaceAt(step.keys, i, { ...key, field }) })
            }
          />
          <Field label="Direction">
            {(p) => (
              <Select
                {...p}
                value={key.descending ? "desc" : "asc"}
                options={[
                  { value: "asc", label: "Ascending" },
                  { value: "desc", label: "Descending" },
                ]}
                onChange={(e) =>
                  onChange({
                    ...step,
                    keys: replaceAt(step.keys, i, {
                      ...key,
                      descending: e.target.value === "desc",
                    }),
                  })
                }
              />
            )}
          </Field>
          <Field label="Empty values">
            {(p) => (
              <Select
                {...p}
                value={key.nullsLast ? "last" : "first"}
                options={[
                  { value: "last", label: "Last" },
                  { value: "first", label: "First" },
                ]}
                onChange={(e) =>
                  onChange({
                    ...step,
                    keys: replaceAt(step.keys, i, { ...key, nullsLast: e.target.value === "last" }),
                  })
                }
              />
            )}
          </Field>
        </>
      )}
    />
  );
}

function SelectForm({ step, columns, onChange }: FormProps<"select">) {
  return (
    <ColumnChecklist
      legend="Columns to keep"
      columns={columns}
      value={step.fields}
      ordered
      onChange={(fields) => onChange({ ...step, fields })}
    />
  );
}

function RenameForm({ step, columns, onChange }: FormProps<"rename">) {
  const entries = Object.entries(step.mapping);
  const write = (next: [string, string][]) =>
    onChange({ ...step, mapping: Object.fromEntries(next) });
  return (
    <RowList
      legend="Renames"
      rows={entries}
      addLabel="Add rename"
      onAdd={() => {
        const free = columns.find((c) => !(c.name in step.mapping));
        if (free) write([...entries, [free.name, free.name]]);
      }}
      onRemove={(i) => write(removeAt(entries, i))}
      renderRow={([from, to], i) => (
        <>
          <ColumnSelect
            label="Column"
            columns={columns}
            value={from}
            exclude={entries.map(([name]) => name).filter((_, j) => j !== i)}
            onChange={(next) => write(replaceAt(entries, i, [next, to]))}
          />
          <Field label="New name">
            {(p) => (
              <Input
                {...p}
                value={to}
                onChange={(e) => write(replaceAt(entries, i, [from, e.target.value]))}
              />
            )}
          </Field>
        </>
      )}
    />
  );
}

function CastForm({ step, columns, onChange }: FormProps<"cast">) {
  return (
    <RowList
      legend="Conversions"
      rows={step.casts}
      addLabel="Add conversion"
      onAdd={() =>
        onChange({
          ...step,
          casts: [...step.casts, { field: columns[0]?.name ?? "", to: "string" }],
        })
      }
      onRemove={(i) => onChange({ ...step, casts: removeAt(step.casts, i) })}
      renderRow={(cast, i) => (
        <>
          <ColumnSelect
            label="Column"
            columns={columns}
            value={cast.field}
            onChange={(field) =>
              onChange({ ...step, casts: replaceAt(step.casts, i, { ...cast, field }) })
            }
          />
          <Field label="Convert to">
            {(p) => (
              <Select
                {...p}
                value={cast.to}
                options={TYPE_OPTIONS}
                onChange={(e) =>
                  onChange({
                    ...step,
                    casts: replaceAt(step.casts, i, {
                      ...cast,
                      to: e.target.value as DataTypeName,
                    }),
                  })
                }
              />
            )}
          </Field>
          {cast.to === "date" || cast.to === "timestamp" ? (
            <Field label="Text format" hint="e.g. %d/%m/%Y; blank = ISO">
              {(p) => (
                <Input
                  {...p}
                  value={cast.format ?? ""}
                  onChange={(e) =>
                    onChange({
                      ...step,
                      casts: replaceAt(step.casts, i, { ...cast, format: e.target.value || null }),
                    })
                  }
                />
              )}
            </Field>
          ) : null}
        </>
      )}
    />
  );
}

function FillMissingForm({ step, columns, onChange }: FormProps<"fill_missing">) {
  return (
    <RowList
      legend="Fills"
      rows={step.fills}
      addLabel="Add fill"
      onAdd={() =>
        onChange({
          ...step,
          fills: [...step.fills, { field: columns[0]?.name ?? "", strategy: "value", value: null }],
        })
      }
      onRemove={(i) => onChange({ ...step, fills: removeAt(step.fills, i) })}
      renderRow={(fill, i) => (
        <>
          <ColumnSelect
            label="Column"
            columns={columns}
            value={fill.field}
            onChange={(field) =>
              onChange({ ...step, fills: replaceAt(step.fills, i, { ...fill, field }) })
            }
          />
          <Field label="Fill with">
            {(p) => (
              <Select
                {...p}
                value={fill.strategy}
                options={FILL_OPTIONS}
                onChange={(e) =>
                  onChange({
                    ...step,
                    fills: replaceAt(step.fills, i, {
                      ...fill,
                      strategy: e.target.value as FillStrategy,
                    }),
                  })
                }
              />
            )}
          </Field>
          {fill.strategy === "value" ? (
            <Field label="Value">
              {(p) => (
                <ScalarInput
                  id={p.id}
                  aria-describedby={p["aria-describedby"]}
                  column={colOf(columns, fill.field)}
                  value={fill.value ?? null}
                  onChange={(value) =>
                    onChange({ ...step, fills: replaceAt(step.fills, i, { ...fill, value }) })
                  }
                />
              )}
            </Field>
          ) : null}
        </>
      )}
    />
  );
}

/** "All columns" or a chosen subset; shared by drop-missing and dedupe. */
function OptionalColumns({
  legend,
  columns,
  value,
  onChange,
}: {
  legend: string;
  columns: Column[];
  value: string[] | null | undefined;
  onChange: (value: string[] | null) => void;
}) {
  return (
    <div className="flex flex-col gap-2">
      <label className="flex items-center gap-1.5 text-sm">
        <input
          type="checkbox"
          className="accent-accent"
          checked={value === null || value === undefined}
          onChange={(e) => onChange(e.target.checked ? null : columns.map((c) => c.name))}
        />
        All columns
      </label>
      {value ? (
        <ColumnChecklist legend={legend} columns={columns} value={value} onChange={onChange} />
      ) : null}
    </div>
  );
}

function DropMissingForm({ step, columns, onChange }: FormProps<"drop_missing">) {
  return (
    <>
      <OptionalColumns
        legend="Check these columns"
        columns={columns}
        value={step.fields}
        onChange={(fields) => onChange({ ...step, fields })}
      />
      <Field label="Drop a row when">
        {(p) => (
          <Select
            {...p}
            value={step.how}
            options={[
              { value: "any", label: "any checked column is empty" },
              { value: "all", label: "all checked columns are empty" },
            ]}
            onChange={(e) => onChange({ ...step, how: e.target.value as "any" | "all" })}
          />
        )}
      </Field>
    </>
  );
}

function DedupeForm({ step, columns, onChange }: FormProps<"dedupe">) {
  return (
    <>
      <OptionalColumns
        legend="Duplicate if these match"
        columns={columns}
        value={step.fields}
        onChange={(fields) => onChange({ ...step, fields })}
      />
      <Field label="Keep">
        {(p) => (
          <Select
            {...p}
            value={step.keep}
            options={[
              { value: "first", label: "First occurrence" },
              { value: "last", label: "Last occurrence" },
            ]}
            onChange={(e) => onChange({ ...step, keep: e.target.value as "first" | "last" })}
          />
        )}
      </Field>
    </>
  );
}

function CalcForm(props: FormProps<"calc">) {
  const { step, onChange } = props;
  const ctx: FormContext = props;
  return (
    <>
      <Field label="New column name">
        {(p) => (
          <Input
            {...p}
            value={step.name}
            onChange={(e) => onChange({ ...step, name: e.target.value })}
          />
        )}
      </Field>
      <Field
        label="Formula"
        hint="DuckDB expression, e.g. units * unit_price. Type to complete column names."
      >
        {(p) => (
          <FormulaField
            id={p.id}
            describedBy={p["aria-describedby"]}
            stepId={step.id}
            ctx={ctx}
            label="Formula"
            value={step.expression}
            onChange={(expression) => onChange({ ...step, expression })}
          />
        )}
      </Field>
    </>
  );
}

function GroupAggregateForm({ step, columns, onChange }: FormProps<"group_aggregate">) {
  return (
    <>
      <ColumnChecklist
        legend="Group by"
        columns={columns}
        value={step.by}
        onChange={(by) => onChange({ ...step, by })}
      />
      <RowList
        legend="Measures"
        rows={step.aggregations}
        addLabel="Add measure"
        onAdd={() =>
          onChange({
            ...step,
            aggregations: [
              ...step.aggregations,
              { field: null, fn: "count", asName: `measure_${step.aggregations.length + 1}` },
            ],
          })
        }
        onRemove={(i) => onChange({ ...step, aggregations: removeAt(step.aggregations, i) })}
        renderRow={(agg, i) => {
          const set = (patch: Partial<typeof agg>) =>
            onChange({
              ...step,
              aggregations: replaceAt(step.aggregations, i, { ...agg, ...patch }),
            });
          return (
            <>
              <Field label="Function">
                {(p) => (
                  <Select
                    {...p}
                    value={agg.fn}
                    options={AGG_OPTIONS}
                    onChange={(e) => {
                      const fn = e.target.value as AggFn;
                      // count works on rows; every other function needs a column.
                      const needsField = fn !== "count" && agg.field === null;
                      const pick = columns.find((c) => c.semantic === "quantitative") ?? columns[0];
                      set({ fn, field: needsField ? (pick?.name ?? null) : agg.field });
                    }}
                  />
                )}
              </Field>
              <ColumnSelect
                label="Column"
                columns={columns}
                empty={agg.fn === "count" ? "(rows)" : "Choose…"}
                value={agg.field ?? ""}
                onChange={(field) => set({ field: field || null })}
              />
              <Field label="Output name">
                {(p) => (
                  <Input
                    {...p}
                    value={agg.asName}
                    onChange={(e) => set({ asName: e.target.value })}
                  />
                )}
              </Field>
            </>
          );
        }}
      />
    </>
  );
}

function SourceSelect({
  label,
  datasets,
  value,
  onChange,
}: {
  label: string;
  datasets: Dataset[];
  value: string;
  onChange: (id: string) => void;
}) {
  return (
    <Field label={label}>
      {(p) => (
        <Select
          {...p}
          value={value}
          options={[
            { value: "", label: "Choose a dataset…" },
            ...datasets.map((d) => ({ value: d.id, label: d.name })),
          ]}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </Field>
  );
}

function JoinForm({ step, columns, datasets, onChange }: FormProps<"join">) {
  const rightColumns =
    datasets.find((d) => d.id === step.right.id)?.versions?.at(-1)?.columns ?? [];
  return (
    <>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <SourceSelect
          label="Join with"
          datasets={datasets}
          value={step.right.id}
          onChange={(id) => onChange({ ...step, right: { kind: "dataset", id } })}
        />
        <Field label="Join type">
          {(p) => (
            <Select
              {...p}
              value={step.how}
              options={[
                { value: "left", label: "Left (keep all current rows)" },
                { value: "inner", label: "Inner (only matches)" },
                { value: "right", label: "Right (keep all other rows)" },
                { value: "full", label: "Full (keep everything)" },
              ]}
              onChange={(e) => onChange({ ...step, how: e.target.value as typeof step.how })}
            />
          )}
        </Field>
        <Field label="Suffix for clashing columns">
          {(p) => (
            <Input
              {...p}
              value={step.suffix}
              onChange={(e) => onChange({ ...step, suffix: e.target.value })}
            />
          )}
        </Field>
      </div>
      <RowList
        legend="Match on"
        rows={step.on}
        addLabel="Add key"
        onAdd={() =>
          onChange({
            ...step,
            on: [...step.on, { left: columns[0]?.name ?? "", right: rightColumns[0]?.name ?? "" }],
          })
        }
        onRemove={(i) => onChange({ ...step, on: removeAt(step.on, i) })}
        renderRow={(key, i) => (
          <>
            <ColumnSelect
              label="Current column"
              columns={columns}
              value={key.left}
              onChange={(left) =>
                onChange({ ...step, on: replaceAt(step.on, i, { ...key, left }) })
              }
            />
            <ColumnSelect
              label="Other column"
              columns={rightColumns}
              value={key.right}
              onChange={(right) =>
                onChange({ ...step, on: replaceAt(step.on, i, { ...key, right }) })
              }
            />
          </>
        )}
      />
    </>
  );
}

function UnionForm({ step, datasets, onChange }: FormProps<"union">) {
  return (
    <>
      <RowList
        legend="Append rows from"
        rows={step.others}
        addLabel="Add dataset"
        onAdd={() => onChange({ ...step, others: [...step.others, { kind: "dataset", id: "" }] })}
        onRemove={(i) => onChange({ ...step, others: removeAt(step.others, i) })}
        renderRow={(ref, i) => (
          <SourceSelect
            label="Dataset"
            datasets={datasets}
            value={ref.id}
            onChange={(id) =>
              onChange({ ...step, others: replaceAt(step.others, i, { kind: "dataset", id }) })
            }
          />
        )}
      />
      <label className="flex items-center gap-1.5 text-sm">
        <input
          type="checkbox"
          className="accent-accent"
          checked={step.byName}
          onChange={(e) => onChange({ ...step, byName: e.target.checked })}
        />
        Match columns by name (otherwise by position)
      </label>
    </>
  );
}

function PivotForm({ step, columns, onChange }: FormProps<"pivot">) {
  return (
    <>
      <ColumnChecklist
        legend="Row labels (index)"
        columns={columns}
        value={step.index}
        onChange={(index) => onChange({ ...step, index })}
      />
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        <ColumnSelect
          label="Values become columns"
          columns={columns}
          value={step.columns}
          onChange={(c) => onChange({ ...step, columns: c })}
        />
        <ColumnSelect
          label="Cell values"
          columns={columns}
          value={step.values}
          onChange={(values) => onChange({ ...step, values })}
        />
        <Field label="Combine with">
          {(p) => (
            <Select
              {...p}
              value={step.fn}
              options={AGG_OPTIONS}
              onChange={(e) => onChange({ ...step, fn: e.target.value as AggFn })}
            />
          )}
        </Field>
      </div>
    </>
  );
}

function UnpivotForm({ step, columns, onChange }: FormProps<"unpivot">) {
  return (
    <>
      <ColumnChecklist
        legend="Keep as identifiers"
        columns={columns}
        value={step.idFields}
        onChange={(idFields) => onChange({ ...step, idFields })}
      />
      <ColumnChecklist
        legend="Columns to turn into rows"
        columns={columns}
        value={step.valueFields}
        onChange={(valueFields) => onChange({ ...step, valueFields })}
      />
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <Field label="Name column">
          {(p) => (
            <Input
              {...p}
              value={step.nameField}
              onChange={(e) => onChange({ ...step, nameField: e.target.value })}
            />
          )}
        </Field>
        <Field label="Value column">
          {(p) => (
            <Input
              {...p}
              value={step.valueField}
              onChange={(e) => onChange({ ...step, valueField: e.target.value })}
            />
          )}
        </Field>
      </div>
    </>
  );
}

function NormalizeForm({ step, columns, onChange }: FormProps<"normalize">) {
  return (
    <>
      <ColumnChecklist
        legend="Numeric columns"
        columns={columns.filter((c) => c.semantic === "quantitative")}
        value={step.fields}
        onChange={(fields) => onChange({ ...step, fields })}
      />
      <Field label="Method">
        {(p) => (
          <Select
            {...p}
            value={step.method}
            options={[
              { value: "zscore", label: "Z-score (mean 0, std 1)" },
              { value: "minmax", label: "Min-max (0 to 1)" },
              { value: "robust", label: "Robust (median and IQR)" },
            ]}
            onChange={(e) => onChange({ ...step, method: e.target.value as typeof step.method })}
          />
        )}
      </Field>
    </>
  );
}

/** The form for whichever step type is selected. */
export function StepForm({
  step,
  onChange,
  ...context
}: FormContext & { step: PipelineStep; onChange: (step: PipelineStep) => void }) {
  // Each case narrows `step`; the cast on onChange is safe because the form only emits its own op.
  switch (step.op) {
    case "filter":
      return <FilterForm step={step} onChange={onChange} {...context} />;
    case "sort":
      return <SortForm step={step} onChange={onChange} {...context} />;
    case "select":
      return <SelectForm step={step} onChange={onChange} {...context} />;
    case "rename":
      return <RenameForm step={step} onChange={onChange} {...context} />;
    case "cast":
      return <CastForm step={step} onChange={onChange} {...context} />;
    case "fill_missing":
      return <FillMissingForm step={step} onChange={onChange} {...context} />;
    case "drop_missing":
      return <DropMissingForm step={step} onChange={onChange} {...context} />;
    case "dedupe":
      return <DedupeForm step={step} onChange={onChange} {...context} />;
    case "calc":
      return <CalcForm step={step} onChange={onChange} {...context} />;
    case "group_aggregate":
      return <GroupAggregateForm step={step} onChange={onChange} {...context} />;
    case "join":
      return <JoinForm step={step} onChange={onChange} {...context} />;
    case "union":
      return <UnionForm step={step} onChange={onChange} {...context} />;
    case "pivot":
      return <PivotForm step={step} onChange={onChange} {...context} />;
    case "unpivot":
      return <UnpivotForm step={step} onChange={onChange} {...context} />;
    case "normalize":
      return <NormalizeForm step={step} onChange={onChange} {...context} />;
  }
}
