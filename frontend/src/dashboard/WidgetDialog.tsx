import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import type { Encoding } from "../api/types";
import { FORMAT_PRESETS } from "../charts";
import { Button } from "../components/ui/button";
import { Dialog, DialogClose, DialogContent } from "../components/ui/dialog";
import { Field, Input, Select } from "../components/ui/input";
import { useDatasets } from "../data/hooks";
import { assetUrl, IMAGE_ACCEPT, uploadImage } from "./assetsApi";
import { useDashboardEnv } from "./env";
import { newId } from "./model";
import type { DashboardFilter, FilterControl, Widget } from "./types";

export type NewWidgetKind = "kpi" | "text" | "image" | "filter";

/** What the dialog hands back: a widget, and a filter to add to the dashboard with it. */
export interface NewWidget {
  widget: Widget;
  filter?: DashboardFilter;
}

const AGGS: NonNullable<Encoding["agg"]>[] = [
  "sum",
  "mean",
  "median",
  "min",
  "max",
  "count",
  "count_distinct",
  "std",
  "var",
];

const TITLES: Record<NewWidgetKind, string> = {
  kpi: "Add KPI card",
  text: "Add text",
  image: "Add image",
  filter: "Add filter",
};

interface Props {
  kind: NewWidgetKind | null;
  onClose: () => void;
  onAdd: (value: NewWidget) => void;
}

export function WidgetDialog({ kind, onClose, onAdd }: Props) {
  return (
    <Dialog open={kind !== null} onOpenChange={(open) => !open && onClose()}>
      {kind ? (
        <DialogContent title={TITLES[kind]}>
          <WidgetForm
            kind={kind}
            onSubmit={(value) => {
              onAdd(value);
              onClose();
            }}
          />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

/** The columns of the project's charts' sources, for field suggestions. */
function useFieldSuggestions(sourceKey: string | null): string[] {
  const env = useDashboardEnv();
  const [kind, id] = sourceKey?.split(":") ?? [];
  const sources = sourceKey
    ? [{ id: id ?? "", kind }]
    : env.charts.map((c) => ({ id: c.source.id, kind: c.source.kind }));
  const names = new Set<string>();
  for (const source of sources) for (const name of env.columnsOf(source.id) ?? []) names.add(name);
  return [...names];
}

function WidgetForm({ kind, onSubmit }: { kind: NewWidgetKind; onSubmit: (v: NewWidget) => void }) {
  const env = useDashboardEnv();
  const datasets = useDatasets().data ?? [];
  const sourceOptions = [
    ...datasets.map((d) => ({ value: `dataset:${d.id}`, label: d.name })),
    ...env.pipelines.map((p) => ({ value: `pipeline:${p.id}`, label: `${p.name} (pipeline)` })),
  ];

  const [title, setTitle] = useState("");
  const [source, setSource] = useState(sourceOptions[0]?.value ?? "");
  const [field, setField] = useState("");
  const [agg, setAgg] = useState<NonNullable<Encoding["agg"]>>("sum");
  const [timeField, setTimeField] = useState("");
  const [comparison, setComparison] = useState<"none" | "previous_period" | "target">(
    "previous_period",
  );
  const [target, setTarget] = useState("");
  const [format, setFormat] = useState("compact:1");
  const [markdown, setMarkdown] = useState("## Heading\n\nWrite **Markdown** here.");
  const [assetId, setAssetId] = useState("");
  const upload = useMutation({
    mutationFn: (file: File) => uploadImage(file),
    onSuccess: (asset, file) => {
      setAssetId(asset.id);
      // A starting point for the alt text, which the user should still describe properly.
      setAlt((current) => current || file.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " "));
    },
  });
  const [alt, setAlt] = useState("");
  const [fit, setFit] = useState<"contain" | "cover">("contain");
  const [label, setLabel] = useState("");
  const [control, setControl] = useState<FilterControl>("dropdown");
  const suggestions = useFieldSuggestions(kind === "kpi" ? source : null);
  const listId = `fields-${kind}`;

  const submit = () => {
    if (kind === "text") return onSubmit({ widget: { kind: "text", markdown } });
    if (kind === "image") {
      return onSubmit({ widget: { kind: "image", assetId, alt, fit } });
    }
    if (kind === "filter") {
      const filter: DashboardFilter = {
        id: newId(),
        label: label || field,
        field,
        fieldMap: {},
        predicate: null,
        control,
      };
      return onSubmit({ widget: { kind: "filter", filterId: filter.id, control }, filter });
    }
    const [sourceKind, sourceId] = source.split(":");
    onSubmit({
      widget: {
        kind: "kpi",
        title: title || field || "KPI",
        source: { kind: sourceKind === "pipeline" ? "pipeline" : "dataset", id: sourceId ?? "" },
        measure: field ? { field, agg } : { agg: "count" },
        time: timeField ? { field: timeField } : null,
        comparison: timeField || comparison === "target" ? comparison : "none",
        target: comparison === "target" && target !== "" ? Number(target) : null,
        format,
      },
    });
  };

  const valid =
    kind === "text"
      ? markdown.trim() !== ""
      : kind === "image"
        ? assetId.trim() !== "" && alt.trim() !== ""
        : kind === "filter"
          ? field.trim() !== ""
          : source !== "" && (field !== "" || agg === "count");

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid) submit();
      }}
    >
      <datalist id={listId}>
        {suggestions.map((name) => (
          <option key={name} value={name} />
        ))}
      </datalist>
      {kind === "kpi" ? (
        <>
          <Field label="Title">
            {(p) => <Input {...p} value={title} onChange={(e) => setTitle(e.target.value)} />}
          </Field>
          <Field label="Source">
            {(p) => (
              <Select
                {...p}
                value={source}
                onChange={(e) => setSource(e.target.value)}
                options={sourceOptions}
              />
            )}
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Measure field" hint="Leave empty to count rows">
              {(p) => (
                <Input
                  {...p}
                  list={listId}
                  value={field}
                  onChange={(e) => setField(e.target.value)}
                />
              )}
            </Field>
            <Field label="Aggregate">
              {(p) => (
                <Select
                  {...p}
                  value={agg}
                  onChange={(e) => setAgg(e.target.value as typeof agg)}
                  options={AGGS.map((a) => ({ value: a, label: a }))}
                />
              )}
            </Field>
          </div>
          <Field label="Time field" hint="Adds a sparkline and period comparison">
            {(p) => (
              <Input
                {...p}
                list={listId}
                value={timeField}
                onChange={(e) => setTimeField(e.target.value)}
              />
            )}
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Compare with">
              {(p) => (
                <Select
                  {...p}
                  value={comparison}
                  onChange={(e) => setComparison(e.target.value as typeof comparison)}
                  options={[
                    { value: "none", label: "Nothing" },
                    {
                      value: "previous_period",
                      label: "Previous period",
                      disabled: timeField === "",
                    },
                    { value: "target", label: "Target" },
                  ]}
                />
              )}
            </Field>
            <Field label="Target">
              {(p) => (
                <Input
                  {...p}
                  type="number"
                  disabled={comparison !== "target"}
                  value={target}
                  onChange={(e) => setTarget(e.target.value)}
                />
              )}
            </Field>
          </div>
          <Field label="Number format">
            {(p) => (
              <Select
                {...p}
                value={format}
                onChange={(e) => setFormat(e.target.value)}
                options={FORMAT_PRESETS.filter((f) => f.group === "number").map((f) => ({
                  value: f.value,
                  label: f.label,
                }))}
              />
            )}
          </Field>
        </>
      ) : null}
      {kind === "text" ? (
        <Field
          label="Markdown"
          hint="Links, images and formatting are allowed; scripts are removed."
        >
          {(p) => (
            <textarea
              {...p}
              rows={8}
              value={markdown}
              onChange={(e) => setMarkdown(e.target.value)}
              className="w-full rounded-md border border-line bg-bg p-2 font-mono text-sm text-fg"
            />
          )}
        </Field>
      ) : null}
      {kind === "image" ? (
        <>
          <Field label="Image" hint="PNG, JPEG, GIF, WebP or SVG, up to 10 MB">
            {(p) => (
              <input
                {...p}
                type="file"
                accept={IMAGE_ACCEPT}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) upload.mutate(file);
                }}
                className="text-sm text-fg file:mr-2 file:rounded-md file:border file:border-line file:bg-bg file:px-2 file:py-1"
              />
            )}
          </Field>
          {upload.isPending ? (
            <p role="status" className="text-xs text-muted">
              Uploading…
            </p>
          ) : null}
          {upload.isError ? (
            <p role="alert" className="text-sm text-danger">
              {upload.error.message}
            </p>
          ) : null}
          {assetId ? (
            <img
              src={assetUrl(assetId)}
              alt="Preview of the upload"
              className="max-h-40 w-auto self-start rounded-md border border-line object-contain"
            />
          ) : null}
          <Field label="Alt text" hint="Describes the image for screen readers">
            {(p) => <Input {...p} value={alt} onChange={(e) => setAlt(e.target.value)} />}
          </Field>
          <Field label="Fit">
            {(p) => (
              <Select
                {...p}
                value={fit}
                onChange={(e) => setFit(e.target.value as typeof fit)}
                options={[
                  { value: "contain", label: "Contain" },
                  { value: "cover", label: "Cover" },
                ]}
              />
            )}
          </Field>
        </>
      ) : null}
      {kind === "filter" ? (
        <>
          <Field label="Label">
            {(p) => <Input {...p} value={label} onChange={(e) => setLabel(e.target.value)} />}
          </Field>
          <Field label="Field" hint="Applies to every chart whose data has this field">
            {(p) => (
              <Input
                {...p}
                list={listId}
                value={field}
                onChange={(e) => setField(e.target.value)}
              />
            )}
          </Field>
          <Field label="Control">
            {(p) => (
              <Select
                {...p}
                value={control}
                onChange={(e) => setControl(e.target.value as FilterControl)}
                options={[
                  { value: "dropdown", label: "Dropdown (pick values)" },
                  { value: "date_range", label: "Date range" },
                  { value: "slider", label: "Slider (numeric range)" },
                ]}
              />
            )}
          </Field>
        </>
      ) : null}
      <div className="flex justify-end gap-2">
        <DialogClose asChild>
          <Button type="button">Cancel</Button>
        </DialogClose>
        <Button type="submit" variant="primary" disabled={!valid}>
          Add
        </Button>
      </div>
    </form>
  );
}
