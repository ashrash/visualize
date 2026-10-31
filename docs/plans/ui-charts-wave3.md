# ui-charts Wave 3 plan: drill-down and linked charts

Nothing here changes `contracts/`: `Interactions.drillHierarchy: string[]` and `linkGroup: string | null`
already exist, and drill filters reuse `Predicate`.

## 1. Drill-down (within one chart's hierarchy)

Model: `interactions.drillHierarchy` is the ordered column list ["country", "state", "city"]. A chart at
depth d shows the plot's category channel (default `x`) bound to `levels[d]`, filtered by
`levels[i] eq path[i]` for i < d. Click a category: depth + 1. At the last level a click drills no further.

Registry (additive, optional): `PlotEntry.drill?: { channel: string }` names the channel whose field is
swapped per level. Declared by bar, horizontal/grouped/stacked bar, line, area, pie, donut (pie: `x` is
the slice name). Treemap/sunburst already drill natively through ECharts `nodeClick`, so they do not
declare it. Plot agents opt in by adding one line; no shared file is edited.

New `charts/core/drill.ts` (pure, tested first):
- `type DrillPath = Scalar[]`
- `drillLevels(spec, entry)`: hierarchy filtered to usable levels, [] when the plot has no `drill`.
- `applyDrill(spec, entry, path) -> { spec, predicates }`: effective spec (channel field = levels[depth])
  plus one `{field, op: "eq", value}` Predicate per path step. Server-side filter, so numbers stay exact.
- `drillInto(path, value)`, `drillTo(path, depth)`, `canDrill(spec, entry, path)`.
- `resolveDrillValue(data, field, categoryName)`: maps the clicked label back to the raw column value
  (labels are strings; the eq Predicate needs the original type).

ChartView:
- Optional props `drill?: DrillPath`, `onDrillChange?(path, predicates)`. Uncontrolled by default (local
  state, reset when the spec's hierarchy or source changes); controlled when `drill` is passed, so the
  dashboard can persist it.
- Request filters become `[...filters, ...drillPredicates]`; the query key already covers them.
- A breadcrumb row (All > Germany > Bavaria) of real buttons, shown only while a hierarchy is set.
- Keyboard path, since canvas clicks are not reachable: in "View as table", first-column cells become
  "Drill into <value>" buttons whenever the chart can drill.
- Raw click hook for ui-dashboard: `onPointClick?(hit: { name, seriesName, value, dataIndex, field, drilled })`.
  Fired on every chart click; `drilled: true` when that click changed depth. I do NOT turn it into a
  Predicate or filter other charts; that is the cross-filter bus (ui-dashboard).
  `TODO(ui-dashboard)` on the prop. Drill predicates are only reported via `onDrillChange`; the dashboard
  chooses whether to publish them to its bus.
- Config panel (my `config/`): a "Drill-down levels" ordered column picker in the Data tab, shown only
  when the plot declares `drill`, writing `interactions.drillHierarchy`. Generated from the registry flag.

## 2. Linked charts (`echarts.connect`)

- `EChart` gets `linkGroup?: string`. On mount/change it sets `chart.group = linkGroup` and calls
  `echarts.connect(linkGroup)`; cleanup clears `chart.group` and `disconnect`s the group when its last
  member leaves. A small module-level refcount per group lives in `charts/core/linkGroup.ts` (testable
  without a DOM).
- `ChartView` passes `spec.interactions.linkGroup`. Switching to table view disposes the chart and
  remounting re-joins, so no extra handling.
- ECharts then syncs tooltip/axisPointer, dataZoom, legend toggles and highlight across the group.
  Each connected chart still fires its own `datazoom`, so each re-queries its own visible range
  (detail on demand holds). Documented limit: zoom sync is by percentage, so it is only meaningful for
  charts sharing an x domain.
- Same string as the dashboard's cross-filter `linkGroup`, but only the visual sync is mine; I do not
  touch the bus.

## 3. Bug fixes (no new shared API)

1. y-axis title gap: size from the tick labels the axis will actually show (nice ticks formatted
   through the axis formatter), not `fmt(max)` of raw data. Regression tests with 1607.42 / 44.53.
2. `dropNonPositiveX`: honour each series' `encode.x` dims (default dim 0 only for series without
   `encode` on a value-x cartesian layout); never touch custom series' non-x dims. Test with the
   category-row error-bar layout; callers' `{value}` wrapping workaround keeps working.
3. "View as table" for StatResult: `buildStatTables(stat)` in `core/table.ts`. Scalars become a key/value
   table; equal-length arrays become columns of one table; arrays of objects become rows (union of
   keys); nested objects flatten to dotted keys; one captioned table per top-level group; a summary line
   for method, n, sampled; rows capped (1000) with a "not shown" note. Real `<table>` markup.
4. Map readiness: gate on a key built from the prepare inputs
   `JSON.stringify([entry.key, spec.params])` instead of `entry.key` alone, so changing a choropleth's
   level/resolution waits for the new map instead of staying blank. Cosmetic edits do not change it.

## Questions for the lead
- OK that `onPointClick` is a raw hook and ui-dashboard owns click -> Predicate?
- OK that drill predicates are local to the chart unless the dashboard publishes them?
- Do you want treemap/sunburst native drill left alone (my plan) rather than routed through `drillHierarchy`?
