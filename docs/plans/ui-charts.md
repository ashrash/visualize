# ui-charts plan (Wave 1)

Paths are under `frontend/src/`. Existing exports (`ChartBuilder`, `ChartView`, `EChart`, `getPlot`, `listPlots`) keep their props.

## 1. Registry entry (`charts/core/types.ts`)
```ts
export interface ChannelSchema {            // today's fields kept; new ones optional
  name: string; label: string; required: boolean; multiple: boolean;
  accepts: ColumnSemantic[]; aggregate: boolean;
  aggs?: AggFn[]; timeUnit?: boolean; sort?: boolean; hint?: string;
}
export type ParamSchema = { name: string; label: string; help?: string;
  showIf?: { param: string; equals: JsonValue } } & (   // param may also name a channel ("color is set")
  | { type: "number"; min?: number; max?: number; step?: number; default: number }
  | { type: "int"; min?: number; max?: number; default: number }
  | { type: "boolean"; default: boolean }
  | { type: "select"; options: { value: string; label: string }[]; default: string }
  | { type: "text"; default: string }
  | { type: "color"; default: string }
  | { type: "column"; accepts: ColumnSemantic[] }
  | { type: "range"; min: number; max: number; step?: number; default: [number, number] });
export type StyleSection = "title" | "legend" | "axes" | "multiAxis" | "labels" | "annotations"
  | "refLines" | "markers" | "lines" | "stack" | "orientation" | "trendline" | "errorBars"
  | "tooltip" | "numberFormat" | "palette" | "seriesStyle";
export interface RenderContext { overlays?: OverlayResult[]; locale?: string }   // see 4.5
export interface PlotEntry {
  key: PlotType; label: string; family: PlotFamily; description?: string;
  encodingsSchema: ChannelSchema[];
  paramsSchema: ParamSchema[];
  styleSections: StyleSection[];            // which generic sections the panel shows
  defaults: { encodings?: Record<string, Encoding[]>; params: Record<string, JsonValue>;
              style?: DeepPartial<ChartStyle>; interactions?: Partial<Interactions> };
  statOnly?: boolean;                        // data comes as JSON StatResult, not Arrow
  render(spec: ChartSpec, data: ChartData, theme: ChartTheme, ctx?: RenderContext): EChartsCoreOption;
  prepare?(spec: ChartSpec): Promise<void>;  // lazy assets (GeoJSON, custom series)
  recommend?(columns: Column[]): number;     // 0..1
}
export const definePlot = (e: PlotEntry): PlotEntry => e;   // helper for plot agents
```
- Discovery is unchanged: `plots/*/index.ts` default-exports `PlotEntry[]`. A plot module calls `echarts.use([...])` for the series it needs, so no shared file is edited. A temporary `channels` getter aliases `encodingsSchema` until ui-data stops using it.
- `horizontal_bar`, `grouped_bar` and `stacked_bar` are `bar` presets: same renderer, different `defaults.style`.
- Registry test: every `PlotType` has an entry (a shrinking "known missing" list is allowed until Wave 2 ends), channel names are unique, `showIf` references resolve, defaults match their schemas.

## 2. Config panel schema
`ChartBuilder` renders three tabs from the entry: **Data** (channels: field, aggregate, time unit, sort, "count rows"; `multiple` channels add/remove rows), **Chart** (params, control chosen by `type`, written to `spec.params[name]`, hidden when `showIf` fails), **Style** (one component per `StyleSection`). Never specialised per plot. Scale choices are filtered by column semantic; multi-axis assigns series to a `y_axes` entry via `style.series[i].y_axis`.

Bar:
```ts
encodingsSchema: [
  { name: "x", label: "Category", required: true, multiple: false, accepts: ["nominal","ordinal","temporal"], aggregate: false, timeUnit: true, sort: true },
  { name: "y", label: "Value", required: false, multiple: true, accepts: ["quantitative"], aggregate: true },
  { name: "color", label: "Group", required: false, multiple: false, accepts: ["nominal","ordinal"], aggregate: false } ],
paramsSchema: [
  { name: "barWidth", type: "number", label: "Bar width (%)", min: 10, max: 100, default: 60 },
  { name: "gap", type: "number", label: "Group gap (%)", min: 0, max: 100, default: 20, showIf: { param: "color", equals: true } } ],
styleSections: ["title","legend","axes","multiAxis","labels","annotations","refLines","stack","orientation","errorBars","tooltip","numberFormat","palette","seriesStyle"]
```
Histogram (Wave 2):
```ts
encodingsSchema: [ { name: "x", label: "Value", required: true, multiple: false, accepts: ["quantitative"], aggregate: false },
                   { name: "color", label: "Group", required: false, multiple: false, accepts: ["nominal"], aggregate: false } ],
paramsSchema: [ { name: "binRule", type: "select", label: "Bin rule", default: "fd", options: [{value:"fd",label:"Freedman-Diaconis"},{value:"sturges",label:"Sturges"},{value:"fixed",label:"Fixed count"}] },
                { name: "bins", type: "int", label: "Bins", min: 2, max: 500, default: 30, showIf: { param: "binRule", equals: "fixed" } },
                { name: "density", type: "boolean", label: "Show as density", default: false } ],
styleSections: ["title","legend","axes","refLines","annotations","tooltip","numberFormat","palette"]
```

## 3. Primitives (`charts/core/primitives/`)
Pure functions returning ECharts series or option fragments that a plot spreads into its option. Snapshot-tested.
```ts
errorBars(o: { x: unknown[]; lo: number[]; hi: number[]; mid?: number[]; orientation?: "v"|"h";
               name?: string; color?: string; yAxisIndex?: number; theme: ChartTheme }): SeriesOption   // custom series
ciBand(o: { x: number[]|string[]; lo: number[]; hi: number[]; name?: string; color?: string; opacity?: number; yAxisIndex?: number }): SeriesOption[]  // stacked lo + (hi-lo)
refLine(o: { axis: "x"|"y"|"diagonal"; value?: number|string; label?: string; style?: "solid"|"dashed"|"dotted"; color?: string; extent?: [number, number] }): { markLine?: MarkLineOption; series?: SeriesOption }
offsetKde(o: { groups: { name: string; x: number[]; density: number[] }[]; overlap: number; theme: ChartTheme }): { series: SeriesOption[]; yAxis: YAxisOption }  // ridgeline, raincloud
hierarchyToTree(o: { rows: Record<string, unknown>[]; levels: string[]; value: string }): TreeNode[]   // {name, value, children}
densityFallback(data: ChartData, o: { cap?: number; theme: ChartTheme }): { series: SeriesOption; visualMap: VisualMapOption } | null  // null unless reduction === "binned"
applyStyle(option: EChartsOption, spec: ChartSpec, theme: ChartTheme, ctx?: RenderContext): EChartsOption
```
`applyStyle` implements every generic `StyleSection` (titles, legend, axes and scales, formats, ref lines, annotations, labels, markers, line styles, trendline overlays, tooltips, orientation swap). Plots build series only and call `applyStyle` last, so MVP §4 is written once. Also `core/format.ts` (Intl number/currency/percent/date, presets plus custom patterns), `core/palettes.ts`, `core/table.ts` (data to table rows).

## 4. What renderers need from chart data (for transform)
1. **Column naming:** a `multiple: false` channel arrives as one column named after it (`x`, `color`, `size`, `shape`, `facet`, `label`, `lat`, `lon`). A `multiple` channel arrives as `<name>0..N` (`y0..yN`, `tooltip0..`). `meta.fields` maps columns to display labels (as today).
2. **Shape discriminator:** `meta.method` + `meta.reduction`. I request additive `meta.extra: dict[str, JsonValue]` (bin edges, `bin_w`, `bin_h`, `bandwidth`). 2-D bins arrive as `x`, `y0` (bin centres) and `count`. M4/LTTB keeps `x`, `y0..`.
3. **Types:** temporal values are epoch ms with `kinds.x === "temporal"`; measures are DOUBLE (as today).
4. **Zoom:** the wrapper emits `onViewport({xRange})` (debounced 150 ms); `ChartView` re-queries with `viewport.xRange` and `widthPx` using the abort signal.
5. **Overlays (trendline, regression, CI), server-computed:** `POST /api/charts/overlays` takes a `ChartDataRequest` and returns JSON `OverlayResult[]`: `{kind, series: number, x: number[], y: number[], lo: number[]|null, hi: number[]|null, stats: {r2?, equation?, n, sampled}}`. Until it exists a `TODO(transform)` stub returns `[]` and the panel marks the section "needs server". Error bars (`sd|se|ci`) come as extra columns `y0_lo`, `y0_hi`.
6. **Stat plots** (`statOnly`) receive the JSON `StatResult` through `data`; its shape is stats-agent territory.

## 5. Contract additions (lead adds; additive; `chart.py`)
```python
class TrendlineKind(StrEnum):
    LINEAR = "linear"; POLYNOMIAL = "polynomial"; EXPONENTIAL = "exponential"
    LOGARITHMIC = "logarithmic"; POWER = "power"; LOESS = "loess"; MOVING_AVERAGE = "moving_average"

class Trendline(Contract):
    kind: TrendlineKind = TrendlineKind.LINEAR
    series: int = 0                      # index into the y channel
    degree: int = 2                      # polynomial
    window: int | None = None            # moving average
    ci_level: float | None = None        # None = no band
    show_equation: bool = False
    show_r2: bool = False

class ErrorBars(Contract):
    kind: Literal["sd", "se", "ci", "fixed"] = "sd"
    level: float = 0.95
    value: float | None = None           # for "fixed"

class SeriesStyle(Contract):
    index: int                           # index into the y channel
    y_axis: int = 0                      # which y_axes entry
    color: str | None = None
    kind: Literal["bar", "line", "area", "scatter"] | None = None   # combo charts
    marker: bool | None = None
    line_width: float | None = None
    line_style: Literal["solid", "dashed", "dotted"] | None = None

class TooltipStyle(Contract):
    show: bool = True
    trigger: Literal["axis", "item"] | None = None
    format: str | None = None

# AxisStyle: add
    position: Literal["start", "end"] | None = None
    inverse: bool = False
    tick_rotate: float | None = None
    log_base: float = 10
# ChartStyle: add
    series: list[SeriesStyle] = Field(default_factory=list)
    trendlines: list[Trendline] = Field(default_factory=list)
    error_bars: ErrorBars | None = None
    tooltip: TooltipStyle = Field(default_factory=TooltipStyle)
    font_family: str | None = None
    font_size: float | None = None
    smooth: bool | None = None
    area_opacity: float | None = None
    label_position: Literal["auto", "inside", "outside", "top", "end"] | None = None
```
Also `ChartDataMeta.extra: dict[str, JsonValue]` (4.2). Plot-specific settings (bins, bandwidth, ...) stay in `params`. Templates reuse the existing `ChartTemplate.style`, so `project.py` is unchanged.

## 6. CSS tokens wanted from ui-data (`app/theme.css`)
Existing `--viz-*` tokens cover text, border and categories. New: `--viz-chart-bg`, `--viz-chart-grid`, `--viz-chart-axis`, `--viz-chart-tooltip-bg`, `--viz-chart-tooltip-text`, `--viz-seq-1..9` (viridis ramp, light and dark), `--viz-div-1..9` (diverging), `--viz-font-chart` (default `--viz-font-sans`). If absent I derive them from `--viz-bg/border/text`. Named palettes (Okabe-Ito, Tableau-10, viridis, cividis) live in `charts/core/palettes.ts`, picked by `style.palette`; the default follows `--viz-cat-*`.

## 7. Dependencies
- `echarts-custom-series` (violin, contour, bar-range, line-range): not needed for Wave 1; Wave 2 plot agents need it. Custom series are separate imports, so it costs bundle only for charts that use them (exact size measured on install). Lead decides when.
- Nothing else for Wave 1. jsPDF and svg2pdf.js are already in `package.json`, and I lazy-load them with `import()`, so they stay out of the main chunk (jsPDF is the large one, roughly 300 KB min, loaded only on PDF export). New ECharts modules (MarkLine, MarkPoint, MarkArea, Graphic, Brush, Toolbox, VisualMap, Dataset): roughly 60-90 KB min; I report the measured bundle change.

## 8. Export design (`export/`)
```ts
type ExportFormat = "png" | "jpeg" | "svg" | "pdf" | "clipboard";
interface ExportOptions { format: ExportFormat; filename: string; pixelRatio?: number; background?: string; title?: string }
interface ExportSink { save(blob: Blob, filename: string): Promise<void>; copyImage(blob: Blob): Promise<void> }  // default = browser; a desktop shell swaps it
saveChart(chart: ECharts, opts: ExportOptions, sink?: ExportSink): Promise<void>   // the single entry point
```
- PNG/JPEG: `getDataURL`; JPEG gets an opaque theme background.
- SVG: offscreen `echarts.init(div, null, {renderer: "svg"})`, same option, `renderToSVGString`, dispose. No visible reflow.
- PDF: `import("jspdf")` + `import("svg2pdf.js")` draw that SVG as vector; raster fallback if svg2pdf throws.
- Clipboard: `ClipboardItem({"image/png": blob})`; clear "clipboard unavailable" error in non-secure contexts.
- `ExportMenu` component mounted by `ChartView` (`exportable` prop, default true).

## 9. Wrapper and themes
- `EChart` gains `onReady(chart)`, `onViewport`, `mode` ("light"|"dark") applied with `chart.setTheme` (ECharts 6; no dispose or re-init on a mode switch), and `tableData` for a **View as table** toggle (accessible `<table>`, capped at 1000 rows, from `core/table.ts`). `ChartView` shows a reduction badge ("sampled", "binned", "aggregated", "downsampled") with `meta.method` as tooltip.
- ECharts theme is generated from CSS tokens by `buildEChartsTheme(tokens, dark)` in `core/theme.ts`; `readChartTheme` is kept.

## 10. Test plan
- Known-answer Vitest: `format.ts` (Intl presets), palettes (Okabe-Ito hex, viridis endpoints), `hierarchyToTree`, `ciBand`/`errorBars` outputs, `table.ts`.
- Snapshot per basic plot: spec + fixture `ChartData` to option, in light and dark, with multi-axis and stacked/percent variants.
- Registry tests as in §1. Config panel (Testing Library): controls generated for bar vs histogram fixture entries, `showIf` toggling, keyboard reachability, axe.
- Export: `saveChart` with a mocked sink and `getDataURL`; PDF path with mocked dynamic imports.
- jsdom has no canvas: `EChart` is tested with a stub `echarts.init`; real rendering is checked by qa's e2e screenshots.

## 11. Implementation order (one commit each; lint + test before each)
1. `types.ts` (entry, `ChannelSchema`, `ParamSchema`), `definePlot`, registry tests, compat getter so current `bar` works.
2. `format.ts`, `palettes.ts`, theme builder, `table.ts`.
3. `applyStyle` and the generic style sections (test-first), then bar (+ horizontal/grouped/stacked), line, area, pie, donut.
4. Wrapper: `setTheme`, view-as-table, badge, `onViewport` + `ChartView` re-query.
5. Config panel generation; `ChartBuilder` keeps its props.
6. Primitives with snapshots.
7. Export (`saveChart`, `ExportMenu`).
8. Overlays (trendlines, CI) against the stub, then the server once transform ships it.

## Open questions for the lead
- OK to hand `meta.extra` and `/api/charts/overlays` to transform's plan?
- Is a shrinking "known missing" list in the PlotType coverage test acceptable during Wave 2?
