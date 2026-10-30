import { useMemo } from "react";
import type { ChartSpec, Predicate } from "../../api/types";
import { useChartData } from "./data";
import { EChart } from "./EChart";
import { getPlot } from "./registry";
import { useChartTheme } from "./theme";

interface ChartViewProps {
  spec: ChartSpec;
  filters?: Predicate[];
}

function Notice({ children, tone = "muted" }: { children: string; tone?: "muted" | "danger" }) {
  const color = tone === "danger" ? "text-danger" : "text-muted";
  return (
    <div className={`flex h-full items-center justify-center text-sm ${color}`}>{children}</div>
  );
}

export function ChartView({ spec, filters }: ChartViewProps) {
  const entry = getPlot(spec.plotType);
  const theme = useChartTheme();
  const query = useChartData(spec, filters, entry !== undefined);

  const option = useMemo(
    () => (entry && query.data ? entry.render(spec, query.data, theme) : null),
    [entry, query.data, spec, theme],
  );

  if (!entry) return <Notice>{`"${spec.plotType}" charts aren't available yet.`}</Notice>;
  if (query.error) return <Notice tone="danger">{query.error.message}</Notice>;
  if (!option || !query.data) return <Notice>Loading…</Notice>;

  const { meta } = query.data;
  const label = spec.style?.title ?? `${entry.label}: ${Object.values(meta.fields).join(", ")}`;
  return (
    <div className="flex h-full flex-col">
      <EChart option={option} className="min-h-0 flex-1" ariaLabel={label} />
      <p className="px-2 pt-1 text-xs text-muted">
        {`${meta.reduction === "none" ? "Showing" : "Aggregated from"} ${meta.rowsScanned.toLocaleString()} rows · ${meta.ms} ms`}
        {meta.warnings.length > 0 ? ` · ${meta.warnings.join(" ")}` : ""}
      </p>
    </div>
  );
}
