import type { AggFn, ChartSpec, Column, Encoding } from "../../api/types";
import type { ChannelSchema, PlotEntry } from "../core/types";
import { CheckboxField, SelectField, SmallButton } from "./controls";
import { setChannel } from "./spec";

const ALL_AGGS: { value: AggFn; label: string }[] = [
  { value: "sum", label: "Sum" },
  { value: "mean", label: "Mean" },
  { value: "median", label: "Median" },
  { value: "min", label: "Min" },
  { value: "max", label: "Max" },
  { value: "std", label: "Std deviation" },
  { value: "var", label: "Variance" },
  { value: "count", label: "Count" },
  { value: "count_distinct", label: "Distinct count" },
];

const TIME_UNITS = ["year", "quarter", "month", "week", "day", "hour", "minute"];

const NONE = "__none__";
const COUNT_ROWS = "__count__";

interface ChannelProps {
  channel: ChannelSchema;
  encodings: Encoding[];
  columns: Column[];
  onChange(encodings: Encoding[]): void;
}

function EncodingRow({
  channel,
  encoding,
  columns,
  label,
  onChange,
  onRemove,
}: {
  channel: ChannelSchema;
  encoding: Encoding | undefined;
  columns: Column[];
  label: string;
  onChange(encoding: Encoding | undefined): void;
  onRemove?: () => void;
}) {
  const eligible = columns.filter((c) => channel.accepts.includes(c.semantic));
  const selected = columns.find((c) => c.name === encoding?.field);
  const countRows = channel.aggregate && encoding?.agg === "count" && !encoding.field;
  const value = countRows ? COUNT_ROWS : (encoding?.field ?? NONE);
  const aggs = ALL_AGGS.filter((a) => !channel.aggs || channel.aggs.includes(a.value));
  const isMeasure = selected?.semantic === "quantitative";
  // A non-numeric column can only be counted.
  const aggOptions =
    isMeasure || !selected ? aggs : aggs.filter((a) => a.value.startsWith("count"));

  const options = [
    ...(channel.required && !channel.multiple ? [] : [{ value: NONE, label: "(none)" }]),
    ...(channel.aggregate ? [{ value: COUNT_ROWS, label: "(row count)" }] : []),
    ...eligible.map((c) => ({ value: c.name, label: c.name })),
  ];
  // A required channel with nothing chosen needs a visible placeholder.
  if (!options.some((o) => o.value === value))
    options.unshift({ value, label: encoding?.field ?? "(choose)" });

  const change = (next: string) => {
    if (next === NONE) return onChange(undefined);
    if (next === COUNT_ROWS) return onChange({ agg: "count" });
    const column = columns.find((c) => c.name === next);
    const keepAgg = encoding?.agg && encoding.agg !== "count" ? encoding.agg : "sum";
    onChange({
      ...encoding,
      field: next,
      agg: channel.aggregate
        ? column?.semantic === "quantitative"
          ? keepAgg
          : "count_distinct"
        : undefined,
    });
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-end gap-2">
        <div className="min-w-0 flex-1">
          <SelectField
            label={label}
            value={value}
            options={options}
            onChange={change}
            hint={channel.hint}
          />
        </div>
        {onRemove ? (
          <SmallButton onClick={onRemove} label={`Remove ${label}`} danger>
            Remove
          </SmallButton>
        ) : null}
      </div>
      {channel.aggregate && encoding?.field ? (
        <SelectField
          label={`${label}: aggregate`}
          value={encoding.agg ?? "sum"}
          options={aggOptions}
          onChange={(agg) => onChange({ ...encoding, agg: agg as AggFn })}
        />
      ) : null}
      {channel.timeUnit && selected?.semantic === "temporal" ? (
        <SelectField
          label={`${label}: group dates by`}
          value={encoding?.timeUnit ?? NONE}
          options={[
            { value: NONE, label: "Exact value" },
            ...TIME_UNITS.map((u) => ({ value: u, label: u[0]?.toUpperCase() + u.slice(1) })),
          ]}
          onChange={(unit) =>
            onChange({
              ...encoding,
              timeUnit: unit === NONE ? undefined : (unit as Encoding["timeUnit"]),
            })
          }
        />
      ) : null}
      {channel.sort && encoding ? (
        <SelectField
          label={`${label}: sort`}
          value={encoding.sort ?? NONE}
          options={[
            { value: NONE, label: "Data order" },
            { value: "asc", label: "Ascending" },
            { value: "desc", label: "Descending" },
          ]}
          onChange={(sort) =>
            onChange({ ...encoding, sort: sort === NONE ? undefined : (sort as "asc" | "desc") })
          }
        />
      ) : null}
    </div>
  );
}

function Channel({ channel, encodings, columns, onChange }: ChannelProps) {
  if (!channel.multiple) {
    return (
      <EncodingRow
        channel={channel}
        encoding={encodings[0]}
        columns={columns}
        label={channel.label}
        onChange={(e) => onChange(e ? [e] : [])}
      />
    );
  }
  const rows = encodings.length > 0 ? encodings : [undefined];
  return (
    <fieldset className="flex flex-col gap-3 rounded-md border border-line p-2">
      <legend className="px-1 text-xs font-medium text-muted">{channel.label}</legend>
      {rows.map((encoding, i) => (
        <EncodingRow
          // biome-ignore lint/suspicious/noArrayIndexKey: encodings have no identity beyond position
          key={i}
          channel={channel}
          encoding={encoding}
          columns={columns}
          label={`${channel.label} ${i + 1}`}
          onChange={(next) => {
            const list = [...encodings];
            if (next) list[i] = next;
            else list.splice(i, 1);
            onChange(list);
          }}
          onRemove={
            encodings.length > 1 ? () => onChange(encodings.filter((_, k) => k !== i)) : undefined
          }
        />
      ))}
      <div>
        <SmallButton
          onClick={() => onChange([...encodings, { agg: "count" }])}
          label={`Add ${channel.label}`}
        >
          + Add {channel.label.toLowerCase()}
        </SmallButton>
      </div>
    </fieldset>
  );
}

const setInteractions = (
  spec: ChartSpec,
  patch: Partial<NonNullable<ChartSpec["interactions"]>>,
): ChartSpec => ({
  ...spec,
  interactions: {
    zoom: true,
    brush: false,
    clickFilter: true,
    ...spec.interactions,
    ...patch,
  },
});

/** Ordered drill-down levels (`interactions.drillHierarchy`), coarse to fine. */
function DrillLevels({
  spec,
  columns,
  onChange,
}: {
  spec: ChartSpec;
  columns: Column[];
  onChange(spec: ChartSpec): void;
}) {
  const levels = spec.interactions?.drillHierarchy ?? [];
  const eligible = columns.filter((c) => c.semantic === "nominal" || c.semantic === "ordinal");
  const write = (next: string[]) => onChange(setInteractions(spec, { drillHierarchy: next }));
  return (
    <fieldset className="flex flex-col gap-3 rounded-md border border-line p-2">
      <legend className="px-1 text-xs font-medium text-muted">Drill-down levels</legend>
      <p className="text-xs text-muted">
        Click a bar, slice or point to show the next level, filtered to what you clicked. Needs at
        least two levels.
      </p>
      {levels.map((level, i) => {
        const options = eligible.map((c) => ({ value: c.name, label: c.name }));
        if (!options.some((o) => o.value === level))
          options.unshift({ value: level, label: level });
        return (
          <div
            // biome-ignore lint/suspicious/noArrayIndexKey: levels are positional
            key={i}
            className="flex items-end gap-2"
          >
            <div className="min-w-0 flex-1">
              <SelectField
                label={`Drill-down level ${i + 1}`}
                value={level}
                options={options}
                onChange={(next) => write(levels.map((l, k) => (k === i ? next : l)))}
              />
            </div>
            <SmallButton
              danger
              label={`Remove drill-down level ${i + 1}`}
              onClick={() => write(levels.filter((_, k) => k !== i))}
            >
              Remove
            </SmallButton>
          </div>
        );
      })}
      <div>
        <SmallButton
          label="Add drill-down level"
          onClick={() => {
            const used = new Set(levels);
            const next = eligible.find((c) => !used.has(c.name))?.name;
            if (next) write([...levels, next]);
          }}
        >
          + Add level
        </SmallButton>
      </div>
    </fieldset>
  );
}

/** The Data tab: one editor per channel of the plot's `encodingsSchema`. */
export function DataTab({
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
  return (
    <div className="flex flex-col gap-3">
      {entry.encodingsSchema.map((channel) => (
        <Channel
          key={channel.name}
          channel={channel}
          encodings={spec.encodings?.[channel.name] ?? []}
          columns={columns}
          onChange={(encodings) => onChange(setChannel(spec, channel.name, encodings))}
        />
      ))}
      {entry.drill ? <DrillLevels spec={spec} columns={columns} onChange={onChange} /> : null}
      {entry.styleSections.includes("axes") ? (
        <CheckboxField
          label="Select ranges by dragging on the chart"
          checked={spec.interactions?.brush ?? false}
          onChange={(brush) => onChange(setInteractions(spec, { brush }))}
          hint="Reports the selected range to a dashboard; drag-to-pan is off while this is on."
        />
      ) : null}
    </div>
  );
}
