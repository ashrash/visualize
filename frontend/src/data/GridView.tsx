// biome-ignore-all lint/a11y/useSemanticElements: explicit ARIA roles on divs, because display:grid on real table elements drops their native roles in WebKit.
// biome-ignore-all lint/a11y/useFocusableInteractive: the rows and headers are structural; interaction happens in the buttons inside them.
import { keepPreviousData, useQueries, useQuery } from "@tanstack/react-query";
import {
  columnResizingFeature,
  columnSizingFeature,
  createColumnHelper,
  tableFeatures,
  useTable,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ArrowUpDown, Filter } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import type { ColumnarResult } from "../api/arrow";
import { formatValue } from "../api/format";
import type { Column, Predicate, SortKey } from "../api/types";
import { Button } from "../components/ui/button";
import { Tooltip } from "../components/ui/tooltip";
import { ColumnFilter, type LoadValues } from "./ColumnFilter";
import type { FilterMap } from "./filters";
import { filterList } from "./filters";
import { lastFirstRow, scrollTopForRow, scrollWindow } from "./scrollMap";

// Header model, column sizing and resizing come from TanStack Table; the body is windowed by
// `scrollWindow` (scaled scrolling for 10M rows) with 200-row pages fetched as they scroll into view, so the table itself never
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
    placeholderData: keepPreviousData,
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
  /** Tooltip for disabled sort/filter buttons. */
  controlsTip?: string;
  /** False for a read-only view: no sort/filter buttons and no resize handles in the tab order. */
  interactive?: boolean;
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
  controlsTip = SORT_TIP,
  interactive = true,
  toolbar,
}: GridViewProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const headerRef = useRef<HTMLDivElement>(null);
  const anchorRef = useRef<{ top: number; row: number } | null>(null);
  const [scroll, setScroll] = useState<{
    top: number;
    viewport: number;
    anchor: { top: number; row: number } | null;
  }>({ top: 0, viewport: 600, anchor: null });
  // Latest values for the native wheel/key handlers below.
  const live = useRef({ scaled: false, firstVisible: 0, totalRows });
  const [goTo, setGoTo] = useState("");
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

  // Track the scroll position and the body viewport (the scroller minus the sticky header). The
  // body is windowed by `scrollWindow`, which also maps the scrollbar onto the row index when
  // the dataset is too tall for one element.
  const readScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const header = headerRef.current?.offsetHeight ?? 0;
    setScroll({
      top: el.scrollTop,
      viewport: Math.max((el.clientHeight || 600) - header, ROW_HEIGHT),
      anchor: anchorRef.current,
    });
  };
  // biome-ignore lint/correctness/useExhaustiveDependencies: readScroll only touches refs and setState.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    readScroll();
    el.addEventListener("scroll", readScroll, { passive: true });
    const observer = new ResizeObserver(readScroll);
    observer.observe(el);
    return () => {
      el.removeEventListener("scroll", readScroll);
      observer.disconnect();
    };
  }, []);

  /** Make `row` the first visible row, exactly (also works when one pixel is many rows). */
  const moveTo = (row: number) => {
    const el = scrollRef.current;
    if (!el) return;
    const input = { viewportPx: scroll.viewport, totalRows, rowHeight: ROW_HEIGHT };
    const target = Math.min(
      Math.max(Math.round(row), 0),
      lastFirstRow(totalRows, scroll.viewport, ROW_HEIGHT),
    );
    el.scrollTop = scrollTopForRow(target, input);
    anchorRef.current = { top: el.scrollTop, row: target };
    readScroll();
  };
  // A new dataset, sort or filter changes what "row N" means: forget the exact position.
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset on identity changes only.
  useEffect(() => {
    anchorRef.current = null;
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
    readScroll();
  }, [gridKey, totalRows, filterPredicates, sort]);
  const moveToRef = useRef(moveTo);
  moveToRef.current = moveTo;

  const win = scrollWindow({
    scrollTop: scroll.top,
    viewportPx: scroll.viewport,
    totalRows,
    rowHeight: ROW_HEIGHT,
    anchor: scroll.anchor,
  });
  live.current = { scaled: win.scaled, firstVisible: win.firstVisible, totalRows };

  // In scaled mode one wheel notch or arrow press would skip ~100 rows, so move by whole rows.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    // Fractional wheel deltas (trackpads) accumulate here until they add up to a whole row.
    let remainder = 0;
    /** The row the user is on: the exact anchor while it is still in effect, else the mapped one. */
    const currentRow = () => {
      const anchor = anchorRef.current;
      return anchor && Math.abs(anchor.top - el.scrollTop) < 1
        ? anchor.row
        : live.current.firstVisible;
    };
    const pageRows = () => Math.max(Math.floor(el.clientHeight / ROW_HEIGHT) - 2, 1);
    const onWheel = (event: WheelEvent) => {
      // Ctrl+wheel and pinch are browser zoom; horizontal scrolling stays native.
      if (
        event.ctrlKey ||
        !live.current.scaled ||
        Math.abs(event.deltaX) > Math.abs(event.deltaY)
      ) {
        return;
      }
      event.preventDefault();
      const perUnit =
        event.deltaMode === 2 ? pageRows() : event.deltaMode === 1 ? 1 : 1 / ROW_HEIGHT;
      remainder += event.deltaY * perUnit;
      const rows = Math.trunc(remainder);
      remainder -= rows;
      if (rows !== 0) moveToRef.current(currentRow() + rows);
    };
    const onKey = (event: KeyboardEvent) => {
      if (!live.current.scaled || event.target !== el || event.ctrlKey || event.metaKey) return;
      let next: number | undefined;
      switch (event.key) {
        case "ArrowDown":
          next = currentRow() + 1;
          break;
        case "ArrowUp":
          next = currentRow() - 1;
          break;
        case "PageDown":
          next = currentRow() + pageRows();
          break;
        case "PageUp":
          next = currentRow() - pageRows();
          break;
        case " ":
          next = currentRow() + (event.shiftKey ? -pageRows() : pageRows());
          break;
        case "Home":
          next = 0;
          break;
        case "End":
          next = live.current.totalRows;
          break;
        default:
          return;
      }
      event.preventDefault();
      moveToRef.current(next);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    el.addEventListener("keydown", onKey);
    return () => {
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("keydown", onKey);
    };
  }, []);
  const rowIndexes = Array.from(
    { length: Math.max(win.end - win.start, 0) },
    (_, k) => win.start + k,
  );
  const firstPage = Math.floor(win.start / PAGE_SIZE);
  const lastPage = Math.floor(Math.max(win.end - 1, 0) / PAGE_SIZE);
  const pages = Array.from({ length: lastPage - firstPage + 1 }, (_, i) => firstPage + i);

  const results = useQueries({
    queries: pages.map((page) => ({
      queryKey: pageKey(gridKey, sort, filterPredicates, page),
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        fetchPage({ offset: page * PAGE_SIZE, limit: PAGE_SIZE, signal }),
      staleTime: Number.POSITIVE_INFINITY,
      enabled: totalRows > 0,
      // Keep showing the previous rows while a new sort/filter loads (no empty flash).
      placeholderData: keepPreviousData,
    })),
  });
  const pageData = new Map(pages.map((page, i) => [page, results[i]?.data]));
  const failed = results.find((r) => r.isError);
  const loading = results.some((r) => r.isPending && r.fetchStatus !== "idle");

  const toggleSort = (field: string) => {
    const existing = sort.find((s) => s.field === field);
    if (!existing) onSortChange?.([{ field, descending: false, nullsLast: true }]);
    else if (!existing.descending) onSortChange?.([{ ...existing, descending: true }]);
    else onSortChange?.([]);
  };

  const jumpToRow = () => {
    const row = Number(goTo);
    if (Number.isFinite(row)) moveTo(row - 1);
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
        {/* Divs with explicit roles: display:grid on real table elements loses their roles in WebKit. */}
        <div
          role="table"
          className="font-mono text-xs"
          style={{ width }}
          aria-label={`${label}: ${totalRows.toLocaleString()} rows`}
          aria-rowcount={totalRows + 1}
          aria-colcount={visible.length + 1}
          aria-busy={loading}
        >
          <div
            role="rowgroup"
            ref={headerRef}
            className="sticky top-0 z-10 border-b border-line bg-surface"
            style={{ width }}
          >
            <div
              role="row"
              className="grid"
              style={{ gridTemplateColumns: gridColumns }}
              aria-rowindex={1}
            >
              <div role="columnheader" className="px-2 py-1.5 text-right font-normal text-muted">
                #
              </div>
              {headers.map((header) => {
                const column = visible.find((c) => c.name === header.column.id);
                const active = sort.find((s) => s.field === header.column.id);
                const SortIcon = active ? (active.descending ? ArrowDown : ArrowUp) : ArrowUpDown;
                return (
                  <div
                    role="columnheader"
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
                    {interactive ? (
                      <>
                        <HeaderButton
                          tip={controlsTip}
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
                            tip={controlsTip}
                            enabled={false}
                            label={`Filter ${header.column.id}`}
                            onClick={() => undefined}
                          >
                            <Filter size={13} aria-hidden="true" />
                          </HeaderButton>
                        )}
                      </>
                    ) : null}
                    <div
                      role="separator"
                      aria-orientation="vertical"
                      aria-label={`Resize ${header.column.id}`}
                      aria-valuenow={header.getSize()}
                      aria-valuemin={60}
                      aria-valuemax={800}
                      tabIndex={interactive ? 0 : -1}
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
                  </div>
                );
              })}
            </div>
          </div>
          <div role="rowgroup" className="relative" style={{ height: win.totalPx }}>
            {rowIndexes.map((index) => {
              const page = pageData.get(Math.floor(index / PAGE_SIZE));
              const offset = index % PAGE_SIZE;
              return (
                <div
                  role="row"
                  key={index}
                  aria-rowindex={index + 2}
                  className="absolute left-0 grid border-b border-line/50"
                  style={{
                    gridTemplateColumns: gridColumns,
                    height: ROW_HEIGHT,
                    transform: `translateY(${win.startTop + (index - win.start) * ROW_HEIGHT}px)`,
                    width,
                  }}
                >
                  <div role="cell" className="px-2 py-1.5 text-right text-muted">
                    {(index + 1).toLocaleString()}
                  </div>
                  {visible.map((column) => {
                    const kind = page?.kinds[column.name];
                    const value = page?.columns[column.name]?.[offset];
                    const isNull = page !== undefined && (value === null || value === undefined);
                    const text = page && !isNull ? formatValue(value, kind, column.type) : "";
                    return (
                      <div
                        role="cell"
                        key={column.name}
                        className={`truncate px-2 py-1.5 ${kind === "number" ? "text-right" : ""}`}
                        title={text}
                      >
                        {isNull ? <span className="text-muted">null</span> : text}
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
        </div>
      </section>
      {win.scaled || totalRows > 1000 ? (
        <form
          className="flex items-center gap-1 text-xs text-muted"
          onSubmit={(event) => {
            event.preventDefault();
            jumpToRow();
          }}
        >
          <label htmlFor={`${gridKey}-goto`}>Go to row</label>
          <input
            id={`${gridKey}-goto`}
            type="number"
            min={1}
            max={totalRows}
            value={goTo}
            onChange={(event) => setGoTo(event.target.value)}
            className="h-6 w-28 rounded border border-line bg-bg px-1 text-fg"
          />
          <Button size="sm" type="submit" disabled={goTo === ""}>
            Go
          </Button>
        </form>
      ) : null}
    </div>
  );
}

/** Header button: focusable and announced as disabled (with a tooltip) when not available. */
function HeaderButton({
  tip,
  label,
  enabled,
  onClick,
  children,
}: {
  tip: string;
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
  return enabled ? button : <Tooltip content={tip}>{button}</Tooltip>;
}
