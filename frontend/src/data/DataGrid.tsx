import { useMemo, useState } from "react";
import { postRows } from "../api/datasetRows";
import { fetchColumnValues } from "../api/datasets";
import type { Dataset, ExportRequest, RowsPageMeta, SortKey } from "../api/types";
import { Button } from "../components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "../components/ui/dropdown-menu";
import type { LoadValues } from "./ColumnFilter";
import { describeFilter, type FilterMap, filterList, setFilter } from "./filters";
import { type FetchPage, GridView, useGridHead } from "./GridView";
import { useDebounced } from "./pipeline/usePipelineApi";
import { useExport } from "./useExport";

export function DataGrid({
  dataset,
  initialFilters,
}: {
  dataset: Dataset;
  /** Filters applied on mount (e.g. a dashboard drill-through). */
  initialFilters?: FilterMap;
}) {
  const version = dataset.versions?.at(-1);
  const columns = useMemo(() => version?.columns ?? [], [version]);
  const [sort, setSort] = useState<SortKey[]>([]);
  const [filters, setFilters] = useState<FilterMap>(initialFilters ?? {});
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set());
  const [searchText, setSearchText] = useState("");
  const search = useDebounced(searchText.trim(), 300) || null;
  const exporter = useExport();

  const fetchPage: FetchPage = ({ offset, limit, signal }) =>
    postRows(
      dataset.id,
      {
        offset,
        limit,
        sort,
        filters: filterList(filters),
        search,
        columns: null,
        version: version?.n ?? null,
      },
      signal,
    );

  // The search text is part of the cache key: a different search is a different set of rows.
  const gridKey = `${dataset.id}:${version?.n}:${search ?? ""}`;
  const head = useGridHead({ gridKey, sort, filters, fetchPage });
  // Until the first page arrives, show the dataset's own count instead of "0 of N".
  const totalRows =
    (head.data?.meta as RowsPageMeta | undefined)?.totalRows ?? version?.rowCount ?? 0;
  const datasetRows = version?.rowCount ?? 0;

  const valuesLoader = (field: string): LoadValues => {
    const others = filterList(setFilter(filters, field, null));
    const load: LoadValues = (search, signal) =>
      fetchColumnValues(
        dataset.id,
        { field, search: search || null, filters: others, limit: 100 },
        signal,
      );
    // Cached values are only valid for this dataset version and these other filters.
    load.scope = [dataset.id, version?.n, others];
    return load;
  };

  const exportRequest = (): ExportRequest => ({
    source: { kind: "dataset", id: dataset.id },
    format: "csv",
    pipelines: [],
    version: version?.n ?? null,
    filters: filterList(filters),
    sort,
    columns: hidden.size > 0 ? columns.map((c) => c.name).filter((n) => !hidden.has(n)) : null,
    fileName: dataset.name,
    delimiter: null,
  });

  const active = filterList(filters);
  const toolbar = (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <span className="text-muted" aria-live="polite">
        {totalRows !== datasetRows
          ? `${totalRows.toLocaleString()} of ${datasetRows.toLocaleString()} rows`
          : `${datasetRows.toLocaleString()} rows`}
      </span>
      <input
        type="search"
        aria-label="Search rows"
        placeholder="Search…"
        value={searchText}
        onChange={(e) => setSearchText(e.target.value)}
        className="h-7 w-44 rounded-md border border-line bg-bg px-2 text-sm"
      />
      {active.map((p) => (
        <span
          key={`${p.field}:${p.op}`}
          className="flex items-center gap-1 rounded-full bg-surface-2 px-2 py-0.5 text-xs"
        >
          {describeFilter(p)}
          <button
            type="button"
            aria-label={`Remove filter on ${p.field}`}
            className="text-muted hover:text-fg"
            onClick={() => setFilters((f) => setFilter(f, p.field, null))}
          >
            ×
          </button>
        </span>
      ))}
      <div className="ml-auto flex items-center gap-2">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button size="sm">Columns</Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent className="max-h-72 overflow-y-auto">
            {columns.map((c) => (
              <DropdownMenuCheckboxItem
                key={c.name}
                checked={!hidden.has(c.name)}
                // Keep at least one column visible.
                disabled={!hidden.has(c.name) && columns.length - hidden.size <= 1}
                onSelect={(e) => e.preventDefault()}
                onCheckedChange={(on) =>
                  setHidden((h) => {
                    const next = new Set(h);
                    if (on) next.delete(c.name);
                    else next.add(c.name);
                    return next;
                  })
                }
              >
                {c.name}
              </DropdownMenuCheckboxItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
        {
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button size="sm" disabled={exporter.busy}>
                {exporter.busy ? "Exporting…" : "Export"}
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              <DropdownMenuItem onSelect={() => void exporter.run(exportRequest(), "csv")}>
                Export CSV
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => void exporter.run(exportRequest(), "xlsx")}>
                Export Excel
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        }
      </div>
    </div>
  );

  return (
    <GridView
      gridKey={gridKey}
      label={dataset.name}
      columns={columns}
      hidden={hidden}
      totalRows={totalRows}
      fetchPage={fetchPage}
      sort={sort}
      onSortChange={setSort}
      filters={filters}
      onFilterChange={(field, p) => setFilters((f) => setFilter(f, field, p))}
      valuesLoader={valuesLoader}
      controlsEnabled
      toolbar={toolbar}
    />
  );
}
