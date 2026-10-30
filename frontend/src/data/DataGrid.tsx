import { useQueries } from "@tanstack/react-query";
import {
  columnResizingFeature,
  columnSizingFeature,
  createColumnHelper,
  tableFeatures,
  useTable,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowDown, ArrowUp, ArrowUpDown, Filter } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { fetchRowsPage } from "../api/datasets";
import { formatValue } from "../api/format";
import type { Dataset } from "../api/types";
import { Tooltip } from "../components/ui/tooltip";
import { buildRowsQuery, SERVER_SORT_FILTER_SUPPORTED, type SortSpec } from "./requests";

// Header model, column sizing and resizing come from TanStack Table; the body is windowed by
// TanStack Virtual with 200-row pages fetched from the server as they scroll into view, so the
// table itself never holds the dataset (`data` is empty).

const PAGE_SIZE = 200;
const ROW_HEIGHT = 28;
const INDEX_WIDTH = 64;
const features = tableFeatures({ columnSizingFeature, columnResizingFeature });
type Row = Record<string, unknown>;
const helper = createColumnHelper<typeof features, Row>();
const NO_ROWS: Row[] = [];
const SORT_TIP = "Needs server sort/filter (not available yet)";

export function DataGrid({ dataset }: { dataset: Dataset }) {
  const version = dataset.versions?.at(-1);
  const columns = useMemo(() => version?.columns ?? [], [version]);
  const totalRows = version?.rowCount ?? 0;
  const scrollRef = useRef<HTMLDivElement>(null);
  const [sort, setSort] = useState<SortSpec[]>([]);

  const columnDefs = useMemo(
    () =>
      helper.columns(
        columns.map((c) =>
          helper.display({ id: c.name, header: c.name, size: 160, minSize: 60, maxSize: 800 }),
        ),
      ),
    [columns],
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

  const effectiveSort = SERVER_SORT_FILTER_SUPPORTED ? sort : [];
  const results = useQueries({
    queries: pages.map((page) => ({
      queryKey: ["rows", dataset.id, version?.n, effectiveSort, page],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        fetchRowsPage(
          dataset.id,
          buildRowsQuery({ offset: page * PAGE_SIZE, limit: PAGE_SIZE, sort: effectiveSort }),
          signal,
        ),
      staleTime: Number.POSITIVE_INFINITY,
      enabled: totalRows > 0,
    })),
  });
  const pageData = new Map(pages.map((page, i) => [page, results[i]?.data]));

  const toggleSort = (field: string) =>
    setSort((current) => {
      const existing = current.find((s) => s.field === field);
      if (!existing) return [{ field, descending: false }];
      return existing.descending ? [] : [{ field, descending: true }];
    });

  return (
    <section
      ref={scrollRef}
      // biome-ignore lint/a11y/noNoninteractiveTabindex: a scroll container must be focusable for keyboard scrolling.
      tabIndex={0}
      aria-label={`${dataset.name} data grid`}
      className="h-full overflow-auto rounded-md border border-line"
    >
      <table
        className="grid font-mono text-xs"
        style={{ width }}
        aria-label={`${dataset.name}: ${totalRows.toLocaleString()} rows`}
        aria-rowcount={totalRows + 1}
      >
        <thead className="sticky top-0 z-10 grid border-b border-line bg-surface">
          <tr className="grid" style={{ gridTemplateColumns: gridColumns }} aria-rowindex={1}>
            <th className="px-2 py-1.5 text-right font-normal text-muted">#</th>
            {headers.map((header) => {
              const column = columns.find((c) => c.name === header.column.id);
              const active = sort.find((s) => s.field === header.column.id);
              const SortIcon = active ? (active.descending ? ArrowDown : ArrowUp) : ArrowUpDown;
              return (
                <th
                  key={header.id}
                  className="relative flex min-w-0 items-center gap-1 px-2 py-1 text-left font-sans"
                  aria-sort={active ? (active.descending ? "descending" : "ascending") : undefined}
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
                  <GridAction
                    label={`Sort by ${header.column.id}`}
                    onClick={() => toggleSort(header.column.id)}
                  >
                    <SortIcon size={13} aria-hidden="true" />
                  </GridAction>
                  <GridAction label={`Filter ${header.column.id}`} onClick={() => undefined}>
                    <Filter size={13} aria-hidden="true" />
                  </GridAction>
                  {/* biome-ignore lint/a11y/useSemanticElements: a separator that can be focused and moved is the ARIA window-splitter pattern. */}
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
                {columns.map((column) => {
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
  );
}

/** Sort/filter button: focusable and announced as disabled until the server supports it. */
function GridAction({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  const enabled = SERVER_SORT_FILTER_SUPPORTED;
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
