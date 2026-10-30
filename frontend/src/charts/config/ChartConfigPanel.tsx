import { type KeyboardEvent, useId, useRef, useState } from "react";
import type { ChartSpec, Column } from "../../api/types";
import { getPlot, listPlots } from "../core/registry";
import type { PlotEntry } from "../core/types";
import { Field, inputClass, SmallButton, TextField } from "./controls";
import { DataTab } from "./DataTab";
import { ParamsTab } from "./ParamsTab";
import { StyleTab } from "./StyleTab";
import { applyPlotDefaults, applyTemplate, templateStyle } from "./spec";

/** A saved style preset (the `ChartTemplate` of the project document). */
export interface StyleTemplate {
  id: string;
  name: string;
  style: NonNullable<ChartSpec["style"]>;
}

export interface ChartConfigPanelProps {
  spec: ChartSpec;
  columns: Column[];
  onChange(spec: ChartSpec): void;
  /** Saved style presets to offer (MVP §8). Omit to hide the template controls. */
  templates?: StyleTemplate[];
  /** Save the chart's current look as a new template; the owner of the project stores it. */
  onSaveTemplate?(name: string, style: NonNullable<ChartSpec["style"]>): void;
}

type Tab = "data" | "chart" | "style";
const TABS: { id: Tab; label: string }[] = [
  { id: "data", label: "Data" },
  { id: "chart", label: "Chart" },
  { id: "style", label: "Style" },
];

const FAMILY_LABELS: Record<string, string> = {
  basic: "Basic",
  distribution: "Distribution",
  relationship: "Relationship",
  timeseries: "Time series",
  composition: "Composition",
  multivariate: "Multivariate",
  geo: "Geospatial",
  econometric: "Statistical and econometric",
  specialized: "Specialized",
};

/** Plot types grouped by family, for the type picker. */
export function groupedPlots(plots: PlotEntry[]): [string, PlotEntry[]][] {
  const groups = new Map<string, PlotEntry[]>();
  for (const plot of plots) groups.set(plot.family, [...(groups.get(plot.family) ?? []), plot]);
  return [...groups.entries()];
}

function Templates({
  spec,
  templates,
  onChange,
  onSave,
}: {
  spec: ChartSpec;
  templates: StyleTemplate[];
  onChange(spec: ChartSpec): void;
  onSave?(name: string, style: NonNullable<ChartSpec["style"]>): void;
}) {
  const [name, setName] = useState("");
  return (
    <div className="flex flex-col gap-2 rounded-md border border-line p-2">
      <Field label="Style template">
        {(id) => (
          <select
            id={id}
            className={inputClass}
            value={spec.style?.templateId ?? ""}
            onChange={(e) => {
              const template = templates.find((t) => t.id === e.target.value);
              if (template) onChange(applyTemplate(spec, template.style, template.id));
            }}
          >
            <option value="">(none applied)</option>
            {templates.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        )}
      </Field>
      {onSave ? (
        <div className="flex items-end gap-2">
          <div className="min-w-0 flex-1">
            <TextField
              label="Save current look as template"
              value={name}
              onChange={setName}
              placeholder="Template name"
            />
          </div>
          <SmallButton
            onClick={() => {
              if (!name.trim()) return;
              onSave(name.trim(), templateStyle(spec.style));
              setName("");
            }}
          >
            Save
          </SmallButton>
        </div>
      ) : null}
    </div>
  );
}

/**
 * The chart config panel (MVP §4, §8), generated from the plot's registry entry: Data (channels),
 * Chart (plot params) and Style (generic sections). It knows nothing about individual plots.
 */
export function ChartConfigPanel({
  spec,
  columns,
  onChange,
  templates,
  onSaveTemplate,
}: ChartConfigPanelProps) {
  const entry = getPlot(spec.plotType);
  const [tab, setTab] = useState<Tab>("data");
  const baseId = useId();
  const tabRefs = useRef<Partial<Record<Tab, HTMLButtonElement | null>>>({});

  const onKey = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const delta = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (!delta) return;
    event.preventDefault();
    const next = TABS[(index + delta + TABS.length) % TABS.length];
    if (next) {
      setTab(next.id);
      tabRefs.current[next.id]?.focus();
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <Field label="Chart type">
        {(id) => (
          <select
            id={id}
            className={inputClass}
            value={spec.plotType}
            onChange={(e) => {
              const next = getPlot(e.target.value as ChartSpec["plotType"]);
              if (next) onChange(applyPlotDefaults(spec, next));
            }}
          >
            {groupedPlots(listPlots()).map(([family, plots]) => (
              <optgroup key={family} label={FAMILY_LABELS[family] ?? family}>
                {plots.map((plot) => (
                  <option key={plot.key} value={plot.key}>
                    {plot.label}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        )}
      </Field>
      {entry?.description ? <p className="text-xs text-muted">{entry.description}</p> : null}

      {entry ? (
        <>
          <div role="tablist" aria-label="Chart settings" className="flex border-b border-line">
            {TABS.map((t, i) => (
              <button
                key={t.id}
                ref={(el) => {
                  tabRefs.current[t.id] = el;
                }}
                type="button"
                role="tab"
                id={`${baseId}-tab-${t.id}`}
                aria-selected={tab === t.id}
                aria-controls={`${baseId}-panel-${t.id}`}
                tabIndex={tab === t.id ? 0 : -1}
                onClick={() => setTab(t.id)}
                onKeyDown={(e) => onKey(e, i)}
                className={`-mb-px border-b-2 px-3 py-1.5 text-sm focus-visible:outline-2 focus-visible:outline-accent ${tab === t.id ? "border-accent text-fg" : "border-transparent text-muted hover:text-fg"}`}
              >
                {t.label}
              </button>
            ))}
          </div>
          <div
            role="tabpanel"
            id={`${baseId}-panel-${tab}`}
            aria-labelledby={`${baseId}-tab-${tab}`}
          >
            {tab === "data" ? (
              <DataTab spec={spec} entry={entry} columns={columns} onChange={onChange} />
            ) : null}
            {tab === "chart" ? (
              <ParamsTab spec={spec} entry={entry} columns={columns} onChange={onChange} />
            ) : null}
            {tab === "style" ? (
              <div className="flex flex-col gap-3">
                {templates ? (
                  <Templates
                    spec={spec}
                    templates={templates}
                    onChange={onChange}
                    onSave={onSaveTemplate}
                  />
                ) : null}
                <StyleTab spec={spec} entry={entry} onChange={onChange} />
              </div>
            ) : null}
          </div>
        </>
      ) : (
        <p className="text-sm text-muted">{`"${spec.plotType}" charts aren't available yet.`}</p>
      )}
    </div>
  );
}
