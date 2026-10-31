import type { ECharts } from "echarts/core";
import { Table2 } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { components } from "../../api/schema";
import type { ChartDataMeta, ChartSpec, Predicate } from "../../api/types";
import { ExportMenu } from "../../export/ExportMenu";
import { type ExportFormat, saveChart } from "../../export/saveChart";
import { useExportSink } from "../../export/sinkContext";
import { dataIdentity, isSuperseded, useChartData, useChartOverlays } from "./data";
import {
  applyDrill,
  canDrillDown,
  type DrillPath,
  type DrillValue,
  drillCrumbs,
  drillInto,
  drillLevels,
  drillTo,
  resolveDrillValue,
} from "./drill";
import { EChart, type XRange } from "./EChart";
import {
  type BrushPayload,
  type LegendChange,
  legendValues,
  type PointHit,
  resolveBrushValues,
  toDrillValue,
} from "./events";
import { chartXDomain } from "./primitives/extra";
import { getPlot } from "./registry";
import { numericExtent } from "./series";
import { buildStatTables, buildTable } from "./table";
import { buildEChartsTheme, useChartTheme } from "./theme";
import type { ChartData, RenderContext } from "./types";

/** A click on a mark, with the field it belongs to and the raw value behind its label. */
export interface ChartPointClick extends PointHit {
  /** The x channel's field (after any drill-down), or null when the channel is empty. */
  field: string | null;
  /** The raw column value behind `name`; undefined when the label matches no row. */
  rawValue?: DrillValue;
  /** True when this click drilled one level down instead of ending at a leaf. */
  drilled: boolean;
}

/** What a brush covers, in the chart's own terms (no Predicates). */
export interface ChartBrush extends BrushPayload {
  /** The x channel's field. */
  field: string | null;
  /** Category axes: the raw column values of `categories`. */
  values: DrillValue[];
}

/** The legend after a toggle, with the color field and raw values behind the series names. */
export interface ChartLegendChange extends LegendChange {
  /** The color channel's field, or null when series are not split by color. */
  field: string | null;
  shownValues: DrillValue[];
  hiddenValues: DrillValue[];
}

interface ChartViewProps {
  spec: ChartSpec;
  filters?: Predicate[];
  /** Pipelines a pipeline source refers to, from the project doc (unsaved ones are fine). */
  pipelines?: components["schemas"]["Pipeline"][];
  /** Show the table / export toolbar. Default true. */
  toolbar?: boolean;
  /**
   * The backend's cancellation slot for this chart's requests (default: the chart id). Give each
   * dashboard widget its own so one spec shown twice does not cancel itself.
   */
  slot?: string;
  /**
   * Drill-down position (`interactions.drillHierarchy`): the values clicked so far, one per
   * level. Omit for an uncontrolled chart that remembers its own; pass it (with
   * `onDrillChange`) to persist it elsewhere.
   */
  drill?: DrillPath;
  /**
   * Called when the drill position changes, with the new path and the equivalent `Predicate`s.
   * They stay local to the chart unless the embedder publishes them.
   */
  onDrillChange?(path: DrillPath, predicates: Predicate[]): void;
  /**
   * Raw signals for an embedder's cross-filter bus. The chart never filters other charts itself;
   * the dashboard turns these into Predicates.
   * TODO(ui-dashboard): wire these to the cross-filter bus.
   */
  onPointClick?(hit: ChartPointClick): void;
  /** Needs `interactions.brush`. A committed brush (or `cleared`). */
  onBrushSelected?(brush: ChartBrush): void;
  /** The legend state after an entry was toggled. */
  onLegendChange?(change: ChartLegendChange): void;
}

function Notice({ children, tone = "muted" }: { children: string; tone?: "muted" | "danger" }) {
  const color = tone === "danger" ? "text-danger" : "text-muted";
  return (
    <div className={`flex h-full items-center justify-center text-sm ${color}`}>{children}</div>
  );
}

/** Rows that matched the filters (what the chart summarises); falls back to rows scanned. */
export function matchedRows(meta: ChartDataMeta): number {
  return meta.rowsMatched ?? meta.rowsScanned;
}

/** Text for the "this is not every row" badge, or null when the chart shows the raw data. */
export function reductionBadge(meta: ChartDataMeta): string | null {
  const scanned = matchedRows(meta).toLocaleString();
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

function StatTables({ stat }: { stat: NonNullable<ChartData["stat"]> }) {
  const { summary, tables } = useMemo(() => buildStatTables(stat), [stat]);
  return (
    <div className="h-full space-y-3 overflow-auto p-2">
      <p className="text-xs text-muted">{summary}</p>
      {tables.length === 0 ? (
        <p className="text-xs text-muted">This result has no values.</p>
      ) : null}
      {tables.map((table, t) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: captions can repeat; the list is static
        <div key={t}>
          <table className="w-full border-collapse text-left text-xs text-fg">
            <caption className="pb-1 text-left font-medium">{table.caption}</caption>
            <thead className="bg-surface">
              <tr>
                {table.headers.map((header) => (
                  <th
                    key={header}
                    scope="col"
                    className="border-b border-line px-2 py-1 font-medium"
                  >
                    {header}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table.rows.map((row, i) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: rows have no identity
                <tr key={i}>
                  <th scope="row" className="border-b border-line/50 px-2 py-0.5 font-normal">
                    {row[0]}
                  </th>
                  {row.slice(1).map((cell, j) => (
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
      ))}
    </div>
  );
}

function DataTable({
  data,
  onDrill,
}: {
  data: ChartData;
  /** When set, the x cell of each row is a button that drills into that value. */
  onDrill?(value: DrillValue): void;
}) {
  const table = useMemo(() => buildTable(data), [data]);
  if (data.stat) return <StatTables stat={data.stat} />;
  const xColumn = Object.keys(data.columns).indexOf("x");
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
                  {onDrill && j === xColumn ? (
                    <button
                      type="button"
                      onClick={() => onDrill(toDrillValue(data.columns.x?.[i]))}
                      aria-label={`Drill into ${cell || "(blank)"}`}
                      className="text-accent underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-accent"
                    >
                      {cell || "(blank)"}
                    </button>
                  ) : (
                    cell
                  )}
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

export function ChartView({
  spec: baseSpec,
  filters: baseFilters,
  pipelines,
  toolbar = true,
  slot,
  drill,
  onDrillChange,
  onPointClick,
  onBrushSelected,
  onLegendChange,
}: ChartViewProps) {
  const entry = getPlot(baseSpec.plotType);
  const theme = useChartTheme();

  // Drill-down: the path of clicked values picks the hierarchy level shown and filters to it.
  const levels = useMemo(() => (entry ? drillLevels(baseSpec, entry) : []), [baseSpec, entry]);
  const drillKey = JSON.stringify([baseSpec.id, baseSpec.source, levels]);
  const [ownDrill, setOwnDrill] = useState<{ key: string; path: DrillPath }>({
    key: drillKey,
    path: [],
  });
  // Uncontrolled: remembered per chart and hierarchy, so editing the levels starts over at the top.
  const path = useMemo(
    () => (levels.length === 0 ? [] : (drill ?? (ownDrill.key === drillKey ? ownDrill.path : []))),
    [levels, drill, ownDrill, drillKey],
  );
  const drilled = useMemo(
    () =>
      entry ? applyDrill(baseSpec, entry, path) : { spec: baseSpec, predicates: [], depth: 0 },
    [baseSpec, entry, path],
  );
  const spec = drilled.spec;
  const filters = useMemo(
    () =>
      drilled.predicates.length > 0 ? [...(baseFilters ?? []), ...drilled.predicates] : baseFilters,
    [baseFilters, drilled.predicates],
  );
  const changeDrill = useCallback(
    (next: DrillPath) => {
      if (drill === undefined) setOwnDrill({ key: drillKey, path: next });
      if (entry) onDrillChange?.(next, applyDrill(baseSpec, entry, next).predicates);
    },
    [drill, drillKey, entry, baseSpec, onDrillChange],
  );

  // The zoom belongs to one chart identity: derived, so a new spec never queries with the
  // previous chart's range (resetting it in an effect would be one refetch too late).
  const identity = JSON.stringify([dataIdentity(spec), drilled.predicates]);
  const [zoomState, setZoomState] = useState<{ identity: string; range: XRange | null }>({
    identity,
    range: null,
  });
  const zoom = zoomState.identity === identity ? zoomState.range : null;
  const setZoom = useCallback(
    (range: XRange | null) => setZoomState({ identity, range }),
    [identity],
  );
  const sink = useExportSink();
  const [asTable, setAsTable] = useState(false);
  const chartRef = useRef<ECharts | null>(null);
  const frameRef = useRef<HTMLDivElement>(null);
  const sizeRef = useRef<{ widthPx: number; heightPx: number } | null>(null);
  const extentRef = useRef<{ identity: string; extent: [number, number] } | null>(null);

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

  const options = { xRange: zoom, size: () => sizeRef.current, pipelines, slot };
  const query = useChartData(spec, filters, entry !== undefined, options);
  const overlays = useChartOverlays(spec, filters, entry !== undefined, options);

  // Lazy plot assets (maps, custom series) load before the first render. Readiness is keyed on
  // what `prepare` reads (the plot and its params, e.g. a map's level), not on the plot alone, so
  // changing a param waits for the new asset instead of drawing with the old or no map.
  const prepareKey = JSON.stringify([baseSpec.plotType, baseSpec.params ?? {}]);
  const [preparedKey, setPreparedKey] = useState<string | null>(null);
  useEffect(() => {
    if (!entry?.prepare) return;
    let live = true;
    entry.prepare(baseSpec).then(() => live && setPreparedKey(prepareKey));
    return () => {
      live = false;
    };
  }, [entry, baseSpec, prepareKey]);
  const prepared = !entry?.prepare || preparedKey === prepareKey;

  const data = query.data;
  const fullExtent = useMemo(() => {
    if (!data) return undefined;
    const domain = chartXDomain(data.meta);
    if (domain) return domain;
    const remembered = extentRef.current?.identity === identity ? extentRef.current.extent : null;
    return zoom ? (remembered ?? undefined) : (numericExtent(data.columns.x ?? []) ?? undefined);
  }, [data, zoom, identity]);
  if (!zoom && fullExtent) extentRef.current = { identity, extent: fullExtent };

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

  // Interaction signals. The x channel is the category/time axis; color splits series.
  const xChannel = entry?.drill?.channel ?? "x";
  const xField = spec.encodings?.[xChannel]?.[0]?.field ?? null;
  const colorField = spec.encodings?.color?.[0]?.field ?? null;
  const xFormat = spec.style?.xAxis?.format;
  const horizontal = spec.style?.orientation === "horizontal";

  const handlePointClick = useCallback(
    (hit: PointHit) => {
      if (!data || data.stat) return;
      // A category label maps back to the raw value; value/time axes name the point by [x, y].
      const resolved =
        resolveDrillValue(data, hit.name, { format: xFormat }) ??
        (Array.isArray(hit.value) && hit.value[0] !== undefined
          ? { value: toDrillValue(hit.value[0]) }
          : undefined);
      const goDeeper = resolved !== undefined && canDrillDown(levels, path);
      if (goDeeper) changeDrill(drillInto(path, resolved.value));
      onPointClick?.({
        ...hit,
        field: xField,
        rawValue: resolved?.value,
        drilled: goDeeper,
      });
    },
    [data, xFormat, levels, path, changeDrill, onPointClick, xField],
  );

  const handleBrush = useCallback(
    (brush: BrushPayload) => {
      if (!onBrushSelected) return;
      onBrushSelected({
        ...brush,
        axis: brush.cleared ? (horizontal ? "y" : "x") : brush.axis,
        field: xField,
        values:
          data && brush.kind === "category"
            ? resolveBrushValues(data, brush.categories, { format: xFormat })
            : [],
      });
    },
    [onBrushSelected, data, xField, xFormat, horizontal],
  );

  const handleLegend = useCallback(
    (change: LegendChange) => {
      if (!onLegendChange) return;
      onLegendChange({
        ...change,
        field: colorField,
        shownValues: data ? legendValues(data, change.shown) : [],
        hiddenValues: data ? legendValues(data, change.hidden) : [],
      });
    },
    [onLegendChange, data, colorField],
  );

  const doExport = useCallback(
    async (format: ExportFormat) => {
      const chart = chartRef.current;
      if (!chart) throw new Error("The chart is not ready yet.");
      const page = getComputedStyle(document.documentElement).getPropertyValue("--viz-bg").trim();
      await saveChart(
        chart,
        {
          format,
          filename: spec.style?.title || spec.name || spec.plotType,
          background: page || "#ffffff",
          theme: buildEChartsTheme(theme),
        },
        sink,
      );
    },
    [spec, theme, sink],
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
  if (!data && query.error && isSuperseded(query.error) && !query.isFetching) {
    // The retries ran out and nothing else is loading: say so, and let the user retry.
    return framed(
      <div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-muted">
        <p>A newer request replaced this chart's update.</p>
        <button
          type="button"
          onClick={() => void query.refetch()}
          className="rounded-md border border-line bg-bg px-2 py-1 text-xs text-fg hover:bg-surface focus-visible:outline-2 focus-visible:outline-accent"
        >
          Reload chart
        </button>
      </div>,
    );
  }
  if (!data || !option) return framed(<Notice>Loading…</Notice>);
  if (!data.stat && data.rowCount === 0) {
    // Server warnings often explain why (a dropped group, a failed cast): show them here too.
    return framed(
      <div className="flex h-full flex-col items-center justify-center gap-1 text-sm text-muted">
        <p>No data to show: no rows match this chart's fields and filters.</p>
        {data.meta.warnings.length > 0 ? <p role="status">{data.meta.warnings.join(" ")}</p> : null}
      </div>,
    );
  }

  const { meta } = data;
  const notices = [...meta.warnings, ...(entry.warnings?.(spec, data) ?? [])];
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
      {levels.length > 0 ? (
        <nav
          aria-label="Drill-down"
          className="flex flex-wrap items-center gap-1 px-1 pb-1 text-xs"
        >
          {drillCrumbs(path).map((crumb, i, all) => {
            const current = i === all.length - 1;
            return (
              <span key={crumb.depth} className="inline-flex items-center gap-1">
                {i > 0 ? (
                  <span aria-hidden="true" className="text-muted">
                    ›
                  </span>
                ) : null}
                <button
                  type="button"
                  disabled={current}
                  aria-current={current ? "location" : undefined}
                  onClick={() => changeDrill(drillTo(path, crumb.depth))}
                  className="rounded px-1 text-fg hover:bg-surface focus-visible:outline-2 focus-visible:outline-accent disabled:font-medium disabled:hover:bg-transparent"
                >
                  {crumb.label}
                </button>
              </span>
            );
          })}
          <span className="ml-1 text-muted">
            {canDrillDown(levels, path)
              ? `Click a ${xField ?? "category"} to see its ${levels[path.length + 1] ?? "detail"}`
              : "Lowest level"}
          </span>
        </nav>
      ) : null}
      <div className="min-h-0 flex-1">
        {asTable ? (
          <DataTable
            data={data}
            onDrill={
              canDrillDown(levels, path) && !data.stat
                ? (value) => changeDrill(drillInto(path, value))
                : undefined
            }
          />
        ) : (
          <EChart
            option={option}
            theme={theme}
            className="h-full"
            ariaLabel={label}
            resetKey={`${identity}|${spec.interactions?.zoom ?? true}|${spec.interactions?.brush ?? false}`}
            onReady={onReady}
            onViewport={setZoom}
            linkGroup={spec.interactions?.linkGroup}
            onPointClick={handlePointClick}
            onBrush={handleBrush}
            onLegendChange={handleLegend}
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
        <span>{`${matchedRows(meta).toLocaleString()} rows · ${meta.ms} ms`}</span>
        {notices.length > 0 ? <span role="status">{notices.join(" ")}</span> : null}
        {failed ? <span className="text-danger">{failed}</span> : null}
      </p>
    </div>,
  );
}
