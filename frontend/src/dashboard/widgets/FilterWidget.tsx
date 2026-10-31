import { useDashboardEnv } from "../env";
import type { FilterWidgetValue } from "../types";
import { FilterControl } from "./FilterControl";

/** A filter placed on the grid: its label and control. The filter itself lives on the dashboard. */
export function FilterWidget({ widget }: { widget: FilterWidgetValue }) {
  const env = useDashboardEnv();
  const filter =
    env.dashboard.filters?.find((f) => f.id === widget.filterId) ??
    env.globalFilters.find((f) => f.id === widget.filterId);
  if (!filter) {
    return <p className="p-3 text-sm text-muted">This filter no longer exists.</p>;
  }
  const scope = env.dashboard.filters?.some((f) => f.id === filter.id) ? "dashboard" : "global";
  return (
    <div className="flex h-full flex-col justify-center gap-1 p-3">
      <p className="truncate text-xs font-medium text-muted">{filter.label}</p>
      <FilterControl filter={filter} control={widget.control} scope={scope} />
    </div>
  );
}
