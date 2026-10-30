import { SlidersHorizontal } from "lucide-react";
import { useId, useState } from "react";
import type { ChartSpec, Column } from "../../api/types";
import { ChartConfigPanel, type StyleTemplate } from "./ChartConfigPanel";

interface ChartBuilderProps {
  spec: ChartSpec;
  columns: Column[];
  onChange: (spec: ChartSpec) => void;
  templates?: StyleTemplate[];
  onSaveTemplate?(name: string, style: NonNullable<ChartSpec["style"]>): void;
}

/**
 * The chart builder shown above a chart: a collapsible config panel generated from the plot's
 * registry entry. Shells that have room for a sidebar can mount `ChartConfigPanel` directly.
 */
export function ChartBuilder({
  spec,
  columns,
  onChange,
  templates,
  onSaveTemplate,
}: ChartBuilderProps) {
  const [open, setOpen] = useState(true);
  const panelId = useId();
  return (
    <section aria-label="Chart settings" className="flex flex-col gap-2">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
        className="inline-flex w-fit items-center gap-1 rounded-md border border-line bg-bg px-2 py-1 text-sm text-fg hover:bg-surface focus-visible:outline-2 focus-visible:outline-accent"
      >
        <SlidersHorizontal size={14} aria-hidden="true" />
        {open ? "Hide chart settings" : "Chart settings"}
      </button>
      <div id={panelId} hidden={!open} className="max-h-[50vh] overflow-y-auto pr-1">
        {open ? (
          <ChartConfigPanel
            spec={spec}
            columns={columns}
            onChange={onChange}
            templates={templates}
            onSaveTemplate={onSaveTemplate}
          />
        ) : null}
      </div>
    </section>
  );
}
