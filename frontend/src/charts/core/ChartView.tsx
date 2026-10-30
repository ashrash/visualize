import type { ECharts } from "echarts/core";
import { Table2 } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { components } from "../../api/schema";
import type { ChartDataMeta, ChartSpec, Predicate } from "../../api/types";
import { ExportMenu } from "../../export/ExportMenu";
import { type ExportFormat, saveChart } from "../../export/saveChart";
import { dataIdentity, isSuperseded, useChartData, useChartOverlays } from "./data";
import { EChart, type XRange } from "./EChart";
import { chartXDomain } from "./primitives/extra";
import { getPlot } from "./registry";
import { numericExtent } from "./series";
import { buildTable } from "./table";
import { buildEChartsTheme, useChartTheme } from "./theme";
import type { ChartData, RenderContext } from "./types";

interface ChartViewProps {
  spec: ChartSpec;
  filters?: Predicate[];
  /** Pipelines a pipeline source refers to, from the project doc (unsaved ones are fine). */
  pipelines?: components["schemas"]["Pipeline"][];
  /** Show the table / export toolbar. Default true. */
  toolbar?: boolean;
}

function Notice({ children, tone = "muted" }: { children: string; tone?: "muted" | "danger" }) {
  const color = tone === "danger" ? "text-danger" : "text-muted";
  return (
    <div className={`flex h-full items-center justify-center text-sm ${color}`}>{children}</div>
  );
}

/** Text for the "this is not every row" badge, or null when the chart shows the raw data. */
export function reductionBadge(meta: ChartDataMeta): string | null {
  const scanned = meta.rowsScanned.toLocaleString();
  const returned = meta.rowsReturned.toLocaleString();
  switch (meta.reduction) {
    case "aggregated":
      return `Aggregated from ${scanned} rows`;
    case "binned":
      return `Binned from ${scanned} rows`;
    case "downsampled":
      return `Downsampled: ${returned} of ${scanned} points`;
    case "sampled":
      return `Sampled: ${returned} of ${scanned} rows`;
    default:
      return null;
  }
}

function DataTable({ data }: { data: ChartData }) {
  const table = useMemo(() => buildTable(data), [data]);
  if (data.stat) {
    return (
      <pre className="h-full overflow-auto p-2 text-xs text-fg">
        {JSON.stringify(data.stat.values, null, 2)}
      </pre>
    );
  }
  return (
    <div className="h-full overflow-auto">
      <table className="w-full border-collapse text-left text-xs text-fg">
        <caption className="sr-only">Chart data</caption>
        <thead className="sticky top-0 bg-surface">
          <tr>
            {table.headers.map((header) => (
              <th key={header} scope="col" className="border-b border-line px-2 py-1 font-medium">
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: rows have no identity; the table is static
            <tr key={i}>
              {row.map((cell, j) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: cells are positional
                <td key={j} className="border-b border-line/50 px-2 py-0.5 tabular-nums">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {table.truncated > 0 ? (
        <p className="px-2 py-1 text-xs text-muted">
          {`Showing the first ${table.rows.length.toLocaleString()} rows; ${table.truncated.toLocaleString()} more not shown.`}
        </p>
      ) : null}
    </div>
  );
}

export function ChartView({ spec, filters, pipelines, toolbar = true }: ChartViewProps) {
  const entry = getPlot(spec.plotType);
  const theme = useChartTheme();
  const [zoom, setZoom] = useState<XRange | null>(null);
  const [asTable, setAsTable] = useState(false);
  const chartRef = useRef<ECharts | null>(null);
  const frameRef = useRef<HTMLDivElement>(null);
  const sizeRef = useRef<{ widthPx: number; heightPx: number } | null>(null);
  const extentRef = useRef<[number, number] | null>(null);

  // Detail on demand: a new spec (not a zoom) starts from the full range again.
  const identity = JSON.stringify(dataIdentity(spec));
  // biome-ignore lint/correctness/useExhaustiveDependencies: `identity` is the trigger
  useEffect(() => {
    setZoom(null);
    extentRef.current = null;
  }, [identity]);

  useLayoutEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    const measure = () => {
      const { width, height } = frame.getBoundingClientRect();
      sizeRef.current =
        width > 0 ? { widthPx: Math.round(width), heightPx: Math.round(height) } : null;
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(frame);
    return () => observer.disconnect();
  }, []);

  const options = { xRange: zoom, size: () => sizeRef.current, pipelines };
  const query = useChartData(spec, filters, entry !== undefined, options);
  const overlays = useChartOverlays(spec, filters, entry !== undefined, options);

  // Lazy plot assets (maps, custom series) load before the first render.
  const [preparedKey, setPreparedKey] = useState<string | null>(null);
  useEffect(() => {
    if (!entry?.prepare) return;
    let live = true;
    entry.prepare(spec).then(() => live && setPreparedKey(entry.key));
    return () => {
      live = false;
    };
  }, [entry, spec]);
  const prepared = !entry?.prepare || preparedKey === entry.key;

  const data = query.data;
  const fullExtent = useMemo(() => {
    if (!data) return undefined;
    const domain = chartXDomain(data.meta);
    if (domain) return domain;
    return zoom
      ? (extentRef.current ?? undefined)
      : (numericExtent(data.columns.x ?? []) ?? undefined);
  }, [data, zoom]);
  if (!zoom && fullExtent) extentRef.current = fullExtent;

  const overlayData = overlays.data;
  const option = useMemo(() => {
    if (!entry || !data || !prepared) return null;
    const ctx: RenderContext = {
      overlays: overlayData,
      viewport: zoom ? { fullExtent, xRange: zoom } : undefined,
    };
    return entry.render(spec, data, theme, ctx);
  }, [entry, data, prepared, spec, theme, overlayData, zoom, fullExtent]);

  const onReady = useCallback((chart: ECharts | null) => {
    chartRef.current = chart;
  }, []);

  const doExport = useCallback(
    async (format: ExportFormat) => {
      const chart = chartRef.current;
      if (!chart) throw new Error("The chart is not ready yet.");
      const page = getComputedStyle(document.documentElement).getPropertyValue("--viz-bg").trim();
      await saveChart(chart, {
        format,
        filename: spec.style?.title || spec.name || spec.plotType,
        title: spec.style?.title ?? undefined,
        background: page || "#ffffff",
        theme: buildEChartsTheme(theme),
      });
    },
    [spec, theme],
  );

  const framed = (child: React.ReactNode) => (
    <div ref={frameRef} className="h-full">
      {child}
    </div>
  );
  if (!entry) return framed(<Notice>{`"${spec.plotType}" charts are not available yet.`}</Notice>);
  if (query.error && !isSuperseded(query.error) && !data) {
    return framed(<Notice tone="danger">{query.error.message}</Notice>);
  }
  if (!data || !option) return framed(<Notice>Loading…</Notice>);

  const { meta } = data;
  const badge = reductionBadge(meta);
  const label = spec.style?.title ?? `${entry.label}: ${Object.values(meta.fields).join(", ")}`;
  const failed = query.error && !isSuperseded(query.error) ? query.error.message : null;

  return framed(
    <div className="flex h-full flex-col">
      {toolbar ? (
        <div className="flex items-center justify-end gap-2 px-1 pb-1">
          <button
            type="button"
            aria-pressed={asTable}
            onClick={() => setAsTable((value) => !value)}
            className="inline-flex items-center gap-1 rounded-md border border-line bg-bg px-2 py-1 text-xs text-fg hover:bg-surface focus-visible:outline-2 focus-visible:outline-accent aria-pressed:bg-surface-2"
          >
            <Table2 size={14} aria-hidden="true" />
            View as table
          </button>
          <ExportMenu onExport={doExport} disabled={asTable} />
        </div>
      ) : null}
      <div className="min-h-0 flex-1">
        {asTable ? (
          <DataTable data={data} />
        ) : (
          <EChart
            option={option}
            theme={theme}
            className="h-full"
            ariaLabel={label}
            onReady={onReady}
            onViewport={setZoom}
          />
        )}
      </div>
      <p className="flex flex-wrap items-center gap-x-2 px-2 pt-1 text-xs text-muted">
        {badge ? (
          <span
            title={meta.method ?? undefined}
            className="rounded-full border border-line bg-surface px-2 py-px text-fg"
          >
            {badge}
          </span>
        ) : null}
        <span>{`${meta.rowsScanned.toLocaleString()} rows · ${meta.ms} ms`}</span>
        {meta.warnings.length > 0 ? <span>{meta.warnings.join(" ")}</span> : null}
        {failed ? <span className="text-danger">{failed}</span> : null}
      </p>
    </div>,
  );
}
