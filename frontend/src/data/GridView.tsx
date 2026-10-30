import { useQueries, useQuery } from "@tanstack/react-query";
import {
  columnResizingFeature,
  columnSizingFeature,
  createColumnHelper,
  tableFeatures,
  useTable,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowDown, ArrowUp, ArrowUpDown, Filter } from "lucide-react";
import { type ReactNode, useMemo, useRef } from "react";
import type { ColumnarResult } from "../api/arrow";
import { formatValue } from "../api/format";
import type { Column, Predicate, SortKey } from "../api/types";
import { Tooltip } from "../components/ui/tooltip";
import { ColumnFilter, type LoadValues } from "./ColumnFilter";
import type { FilterMap } from "./filters";
import { filterList } from "./filters";

// Header model, column sizing and resizing come from TanStack Table; the body is windowed by
// TanStack Virtual with 200-row pages fetched as they scroll into view, so the table itself never
// holds the data (`data` is empty). The parent decides where pages come from (dataset rows,
// pipeline preview, an import preview), so this one grid serves all three.

export const PAGE_SIZE = 200;
const ROW_HEIGHT = 28;
const INDEX_WIDTH = 64;
const features = tableFeatures({ columnSizingFeature, columnResizingFeature });
type Row = Record<string, unknown>;
const helper = createColumnHelper<typeof features, Row>();
const NO_ROWS: Row[] = [];
const SORT_TIP = "Needs server sort/filter (not available yet)";

export type FetchPage = (args: {
  offset: number;
  limit: number;
  signal: AbortSignal;
}) => Promise<ColumnarResult<unknown>>;

/** Shared by the grid and its parent so page 0 is fetched once. */
export const pageKey = (
  gridKey: string,
  sort: readonly SortKey[],
  filters: readonly Predicate[],
  page: number,
) => ["grid-page", gridKey, sort, filters, page] as const;

/** Page 0 of a grid: gives the parent the filtered row count (and the first rows to the grid). */
export function useGridHead(args: {
  gridKey: string;
  sort: SortKey[];
  filters: FilterMap;
  fetchPage: FetchPage;
  enabled?: boolean;
}) {
  const filters = filterList(args.filters);
  return useQuery({
    queryKey: pageKey(args.gridKey, args.sort, filters, 0),
    queryFn: ({ signal }) => args.fetchPage({ offset: 0, limit: PAGE_SIZE, signal }),
    enabled: args.enabled ?? true,
    staleTime: Number.POSITIVE_INFINITY,
  });
}

export interface GridViewProps {
  gridKey: string;
  label: string;
  columns: Column[];
  hidden?: ReadonlySet<string>;
  totalRows: number;
  fetchPage: FetchPage;
  sort: SortKey[];
  onSortChange?: (sort: SortKey[]) => void;
  filters: FilterMap;
  onFilterChange?: (field: string, predicate: Predicate | null) => void;
  /** Builds the values loader for a column's filter popover. */
  valuesLoader?: (field: string) => LoadValues;
  /** When false, sort and filter buttons are shown but disabled with a tooltip. */
  controlsEnabled: boolean;
  toolbar?: ReactNode;
}

const NO_HIDDEN: ReadonlySet<string> = new Set();

export function GridView({
  gridKey,
  label,
  columns,
  hidden = NO_HIDDEN,
  totalRows,
  fetchPage,
  sort,
  onSortChange,
  filters,
  onFilterChange,
  valuesLoader,
  controlsEnabled,
  toolbar,
}: GridViewProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const visible = useMemo(() => columns.filter((c) => !hidden.has(c.name)), [columns, hidden]);
  const filterPredicates = useMemo(() => filterList(filters), [filters]);

  const columnDefs = useMemo(
    () =>
      helper.columns(
        visible.map((c) =>
          helper.display({ id: c.name, header: c.name, size: 160, minSize: 60, maxSize: 800 }),
        ),
      ),
    [visible],
  );
  const table = useTable({
    features,
    columns: columnDefs,
    data: NO_ROWS,
    columnResizeMode: "onChange",
  });
  const headers = table.getFlatHeaders();
  const widths = headers.map((h) => h.getSize());
  const gridColumns = `${INDEX_WIDTH}px ${widths.map((w) => `${w}px`).join(" ")}`;
  const width = INDEX_WIDTH + widths.reduce((a, b) => a + b, 0);

  const virtualizer = useVirtualizer({
    count: totalRows,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 20,
  });
  const items = virtualizer.getVirtualItems();
  const firstPage = Math.floor((items[0]?.index ?? 0) / PAGE_SIZE);
  const lastPage = Math.floor((items.at(-1)?.index ?? 0) / PAGE_SIZE);
  const pages = Array.from({ length: lastPage - firstPage + 1 }, (_, i) => firstPage + i);

  const results = useQueries({
    queries: pages.map((page) => ({
      queryKey: pageKey(gridKey, sort, filterPredicates, page),
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        fetchPage({ offset: page * PAGE_SIZE, limit: PAGE_SIZE, signal }),
      staleTime: Number.POSITIVE_INFINITY,
      enabled: totalRows > 0,
    })),
  });
  const pageData = new Map(pages.map((page, i) => [page, results[i]?.data]));
  const failed = results.find((r) => r.isError);

  const toggleSort = (field: string) => {
    const existing = sort.find((s) => s.field === field);
    if (!existing) onSortChange?.([{ field, descending: false, nullsLast: true }]);
    else if (!existing.descending) onSortChange?.([{ ...existing, descending: true }]);
    else onSortChange?.([]);
  };

  return (
    <div className="flex h-full min-h-0 flex-col gap-1">
      {toolbar}
      {failed?.error ? (
        <p role="alert" className="text-sm text-danger">
          {failed.error.message}
        </p>
      ) : null}
      <section
        ref={scrollRef}
        // biome-ignore lint/a11y/noNoninteractiveTabindex: a scroll container must be focusable for keyboard scrolling.
        tabIndex={0}
        aria-label={`${label} data grid`}
        className="min-h-0 flex-1 overflow-auto rounded-md border border-line"
      >
        <table
          className="grid font-mono text-xs"
          style={{ width }}
          aria-label={`${label}: ${totalRows.toLocaleString()} rows`}
          aria-rowcount={totalRows + 1}
        >
          <thead className="sticky top-0 z-10 grid border-b border-line bg-surface">
            <tr className="grid" style={{ gridTemplateColumns: gridColumns }} aria-rowindex={1}>
              <th className="px-2 py-1.5 text-right font-normal text-muted">#</th>
              {headers.map((header) => {
                const column = visible.find((c) => c.name === header.column.id);
                const active = sort.find((s) => s.field === header.column.id);
                const SortIcon = active ? (active.descending ? ArrowDown : ArrowUp) : ArrowUpDown;
                return (
                  <th
                    key={header.id}
                    className="relative flex min-w-0 items-center gap-1 px-2 py-1 text-left font-sans"
                    aria-sort={
                      active ? (active.descending ? "descending" : "ascending") : undefined
                    }
                  >
                    <span
                      className="min-w-0 flex-1 truncate"
                      title={`${header.column.id} · ${column?.type}`}
                    >
                      <span className="font-semibold">{header.column.id}</span>
                      <span className="ml-1 font-normal text-muted">
                        {column?.type.toLowerCase()}
                      </span>
                    </span>
                    <HeaderButton
                      enabled={controlsEnabled && onSortChange !== undefined}
                      label={`Sort by ${header.column.id}`}
                      onClick={() => toggleSort(header.column.id)}
                    >
                      <SortIcon size={13} aria-hidden="true" />
                    </HeaderButton>
                    {controlsEnabled && column && onFilterChange && valuesLoader ? (
                      <ColumnFilter
                        column={column}
                        predicate={filters[column.name]}
                        loadValues={valuesLoader(column.name)}
                        onApply={(p) => onFilterChange(column.name, p)}
                      />
                    ) : (
                      <HeaderButton
                        enabled={false}
                        label={`Filter ${header.column.id}`}
                        onClick={() => undefined}
                      >
                        <Filter size={13} aria-hidden="true" />
                      </HeaderButton>
                    )}
                    {/* biome-ignore lint/a11y/useSemanticElements: a focusable separator is the ARIA window-splitter pattern. */}
                    <div
                      role="separator"
                      aria-orientation="vertical"
                      aria-label={`Resize ${header.column.id}`}
                      aria-valuenow={header.getSize()}
                      aria-valuemin={60}
                      aria-valuemax={800}
                      tabIndex={0}
                      onMouseDown={header.getResizeHandler()}
                      onTouchStart={header.getResizeHandler()}
                      onDoubleClick={() => header.column.resetSize()}
                      onKeyDown={(event) => {
                        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
                        event.preventDefault();
                        const delta = event.key === "ArrowRight" ? 16 : -16;
                        const next = Math.min(800, Math.max(60, header.getSize() + delta));
                        table.setColumnSizing((old) => ({ ...old, [header.column.id]: next }));
                      }}
                      className="absolute top-0 right-0 h-full w-1.5 cursor-col-resize select-none hover:bg-accent focus-visible:bg-accent"
                    />
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody className="relative grid" style={{ height: virtualizer.getTotalSize() }}>
            {items.map((item) => {
              const page = pageData.get(Math.floor(item.index / PAGE_SIZE));
              const offset = item.index % PAGE_SIZE;
              return (
                <tr
                  key={item.key}
                  aria-rowindex={item.index + 2}
                  className="absolute left-0 grid border-b border-line/50"
                  style={{
                    gridTemplateColumns: gridColumns,
                    height: ROW_HEIGHT,
                    transform: `translateY(${item.start}px)`,
                    width,
                  }}
                >
                  <td className="px-2 py-1.5 text-right text-muted">
                    {(item.index + 1).toLocaleString()}
                  </td>
                  {visible.map((column) => {
                    const kind = page?.kinds[column.name];
                    const value = page?.columns[column.name]?.[offset];
                    const isNull = page !== undefined && (value === null || value === undefined);
                    const text = page && !isNull ? formatValue(value, kind) : "";
                    return (
                      <td
                        key={column.name}
                        className={`truncate px-2 py-1.5 ${kind === "number" ? "text-right" : ""}`}
                        title={text}
                      >
                        {isNull ? <span className="text-muted">null</span> : text}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
    </div>
  );
}

/** Header button: focusable and announced as disabled (with a tooltip) when not available. */
function HeaderButton({
  label,
  enabled,
  onClick,
  children,
}: {
  label: string;
  enabled: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  const button = (
    <button
      type="button"
      aria-label={label}
      aria-disabled={!enabled}
      onClick={enabled ? onClick : undefined}
      className="shrink-0 rounded p-0.5 text-muted hover:bg-surface-2 aria-disabled:cursor-not-allowed aria-disabled:opacity-40"
    >
      {children}
    </button>
  );
  return enabled ? button : <Tooltip content={SORT_TIP}>{button}</Tooltip>;
}
