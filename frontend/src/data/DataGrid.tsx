import { useQueries } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useRef } from "react";
import { formatValue } from "../api/format";
import type { Dataset } from "../api/types";
import { fetchRowsPage } from "./hooks";

// Phase 0 preview: a read-only table with virtual rows; pages are fetched from the server as
// they scroll into view. ui-data adds TanStack Table (sorting, resizing, server-side filters).

const PAGE_SIZE = 200;
const ROW_HEIGHT = 28;
const COLUMN_WIDTH = 160;
const INDEX_WIDTH = 64;

export function DataGrid({ dataset }: { dataset: Dataset }) {
  const version = dataset.versions?.at(-1);
  const columns = version?.columns ?? [];
  const totalRows = version?.rowCount ?? 0;
  const scrollRef = useRef<HTMLDivElement>(null);

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
      queryKey: ["rows", dataset.id, version?.n, page],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        fetchRowsPage(dataset.id, page * PAGE_SIZE, PAGE_SIZE, signal),
      staleTime: Number.POSITIVE_INFINITY,
      enabled: totalRows > 0,
    })),
  });
  const pageData = new Map(pages.map((page, i) => [page, results[i]?.data]));
  const width = INDEX_WIDTH + columns.length * COLUMN_WIDTH;
  const gridColumns = `${INDEX_WIDTH}px repeat(${columns.length}, ${COLUMN_WIDTH}px)`;

  return (
    <div ref={scrollRef} className="h-full overflow-auto rounded-md border border-line">
      <table
        className="grid font-mono text-xs"
        style={{ width }}
        aria-label={`${dataset.name}: ${totalRows.toLocaleString()} rows`}
        aria-rowcount={totalRows + 1}
      >
        <thead className="sticky top-0 z-10 grid border-b border-line bg-surface">
          <tr className="grid" style={{ gridTemplateColumns: gridColumns }} aria-rowindex={1}>
            <th className="px-2 py-1.5 text-right font-normal text-muted">#</th>
            {columns.map((column) => (
              <th
                key={column.name}
                className="truncate px-2 py-1.5 text-left font-sans"
                title={`${column.name} · ${column.type}`}
              >
                <span className="font-semibold">{column.name}</span>
                <span className="ml-1 font-normal text-muted">{column.type.toLowerCase()}</span>
              </th>
            ))}
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
    </div>
  );
}
