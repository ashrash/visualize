import { describe, expect, it } from "vitest";
import type { ChartSpec, Predicate } from "../api/types";
import {
  type BusState,
  EMPTY_BUS,
  type FilterInputs,
  filtersForChart,
  forget,
  groupOf,
  pruneBus,
  publish,
  selectionOf,
} from "./bus";
import type { Dashboard } from "./types";

const spec = (id: string, sourceId = "ds1", linkGroup?: string): ChartSpec =>
  ({
    id,
    name: id,
    plotType: "bar",
    source: { kind: "dataset", id: sourceId },
    encodings: {},
    interactions: { zoom: true, brush: false, clickFilter: true, drillHierarchy: [], linkGroup },
  }) as ChartSpec;

const inP = (field: string, ...values: string[]): Predicate => ({ op: "in", field, values });

const dashboard = (over: Partial<Dashboard> = {}): Dashboard =>
  ({ id: "d", name: "d", tabs: [], filters: [], linkGroups: [], ...over }) as Dashboard;

function inputs(
  over: Partial<FilterInputs> & Pick<FilterInputs, "spec" | "widgetId">,
): FilterInputs {
  return {
    dashboard: dashboard(),
    bus: EMPTY_BUS,
    filterValues: {},
    globalFilters: [],
    globalValues: {},
    drill: [],
    columnsOf: () => undefined,
    ...over,
  };
}

const sel = (sourceId: string, ...predicates: Predicate[]) => ({ sourceId, predicates });

describe("groupOf", () => {
  it("prefers the dashboard link group listing the chart, then the spec's own group", () => {
    const d = dashboard({ linkGroups: [{ id: "g1", chartIds: ["a"] }] });
    expect(groupOf(d, spec("a", "ds1", "other"))).toBe("g1");
    expect(groupOf(d, spec("b", "ds1", "other"))).toBe("other");
    expect(groupOf(d, spec("c"))).toBeNull();
    expect(groupOf(undefined, spec("c", "ds1", "x"))).toBe("x");
  });
});

describe("publish / forget", () => {
  it("replaces a widget's slot rather than appending", () => {
    let bus = publish(EMPTY_BUS, "g", "w1", sel("ds1", inP("region", "N")));
    bus = publish(bus, "g", "w1", sel("ds1", inP("region", "S")));
    expect(selectionOf(bus, "g", "w1")).toEqual([inP("region", "S")]);
  });

  it("removes the slot and the group when emptied, and keeps identity when nothing changes", () => {
    const bus = publish(EMPTY_BUS, "g", "w1", sel("ds1", inP("region", "N")));
    expect(publish(bus, "g", "w1", null)).toEqual({});
    expect(publish(bus, "g", "w1", sel("ds1"))).toEqual({});
    expect(publish(EMPTY_BUS, "g", "w1", null)).toBe(EMPTY_BUS);
  });

  it("forgets a widget in every group", () => {
    let bus: BusState = publish(EMPTY_BUS, "g1", "w1", sel("ds1", inP("a", "x")));
    bus = publish(bus, "g2", "w1", sel("ds1", inP("b", "y")));
    bus = publish(bus, "g2", "w2", sel("ds1", inP("c", "z")));
    expect(forget(bus, "w1")).toEqual({ g2: { w2: sel("ds1", inP("c", "z")) } });
  });
});

describe("filtersForChart: own-predicate exclusion", () => {
  const bus = publish(
    publish(EMPTY_BUS, "g", "w1", sel("ds1", inP("region", "N"))),
    "g",
    "w2",
    sel("ds1", inP("channel", "web")),
  );

  it("gives every other chart in the group the selection, but never the chart its own", () => {
    const d = dashboard();
    const one = filtersForChart(
      inputs({ dashboard: d, spec: spec("c1", "ds1", "g"), widgetId: "w1", bus }),
    );
    const two = filtersForChart(
      inputs({ dashboard: d, spec: spec("c2", "ds1", "g"), widgetId: "w2", bus }),
    );
    const three = filtersForChart(
      inputs({ dashboard: d, spec: spec("c3", "ds1", "g"), widgetId: "w3", bus }),
    );
    expect(one).toEqual([inP("channel", "web")]);
    expect(two).toEqual([inP("region", "N")]);
    expect(three).toEqual([inP("region", "N"), inP("channel", "web")]);
  });

  it("does not leak selections to charts outside the group", () => {
    const f = filtersForChart(inputs({ spec: spec("c4", "ds1", "other"), widgetId: "w4", bus }));
    expect(f).toEqual([]);
    expect(filtersForChart(inputs({ spec: spec("c5"), widgetId: "w5", bus }))).toEqual([]);
  });

  it("is the same two charts sharing one widget id only when they are the same widget", () => {
    // Two widgets showing one chart: each excludes only its own selection.
    const shared = spec("c1", "ds1", "g");
    const a = filtersForChart(inputs({ spec: shared, widgetId: "w1", bus }));
    const b = filtersForChart(inputs({ spec: shared, widgetId: "w9", bus }));
    expect(a).toEqual([inP("channel", "web")]);
    expect(b).toEqual([inP("region", "N"), inP("channel", "web")]);
  });
});

describe("filtersForChart: dashboard and global filters", () => {
  const dateFilter = {
    id: "f1",
    label: "Date",
    field: "order_date",
    fieldMap: { ds2: "created" },
    predicate: null,
  };
  const range: Predicate = {
    op: "between_dates",
    field: "order_date",
    min: "2024-01-01",
    max: "2024-02-01",
    includeMin: true,
    includeMax: true,
  };

  it("maps the filter field per source through fieldMap", () => {
    const d = dashboard({ filters: [dateFilter] });
    const base = { dashboard: d, filterValues: { f1: range }, widgetId: "w" };
    expect(filtersForChart(inputs({ ...base, spec: spec("c", "ds1") }))).toEqual([range]);
    expect(filtersForChart(inputs({ ...base, spec: spec("c", "ds2") }))).toEqual([
      { ...range, field: "created" },
    ]);
  });

  it("falls back to the saved predicate until a live value is set, and live null clears it", () => {
    const d = dashboard({ filters: [{ ...dateFilter, predicate: range }] });
    const s = spec("c");
    expect(filtersForChart(inputs({ dashboard: d, spec: s, widgetId: "w" }))).toEqual([range]);
    expect(
      filtersForChart(inputs({ dashboard: d, spec: s, widgetId: "w", filterValues: { f1: null } })),
    ).toEqual([]);
  });

  it("skips a filter on a source known to lack the field", () => {
    const d = dashboard({ filters: [dateFilter] });
    const columnsOf = (id: string) => (id === "ds1" ? new Set(["region"]) : undefined);
    const f = filtersForChart(
      inputs({
        dashboard: d,
        spec: spec("c", "ds1"),
        widgetId: "w",
        filterValues: { f1: range },
        columnsOf,
      }),
    );
    expect(f).toEqual([]);
  });

  it("puts global filters first, then dashboard filters, then drill, then selections", () => {
    const g = { id: "g1", label: "G", field: "year", predicate: null } as const;
    const gp: Predicate = { op: "eq", field: "year", value: 2024 };
    const bus = publish(EMPTY_BUS, "grp", "other", sel("ds1", inP("region", "N")));
    const f = filtersForChart(
      inputs({
        dashboard: dashboard({ filters: [dateFilter] }),
        spec: spec("c", "ds1", "grp"),
        widgetId: "w",
        globalFilters: [g],
        globalValues: { g1: gp },
        filterValues: { f1: range },
        drill: [inP("tier", "gold")],
        bus,
      }),
    );
    expect(f).toEqual([gp, range, inP("tier", "gold"), inP("region", "N")]);
  });
});

describe("filtersForChart: cross-dataset selections", () => {
  const bus = publish(EMPTY_BUS, "g", "w1", sel("ds1", inP("region", "N")));

  it("applies a selection from another source only where that source has the column", () => {
    const target = spec("c2", "ds2", "g");
    const known = (cols: string[]) => (id: string) => (id === "ds2" ? new Set(cols) : undefined);
    expect(
      filtersForChart(inputs({ spec: target, widgetId: "w2", bus, columnsOf: known(["region"]) })),
    ).toEqual([inP("region", "N")]);
    expect(
      filtersForChart(inputs({ spec: target, widgetId: "w2", bus, columnsOf: known(["other"]) })),
    ).toEqual([]);
    // Columns not known: not guessed.
    expect(filtersForChart(inputs({ spec: target, widgetId: "w2", bus }))).toEqual([]);
  });

  it("applies a same-source selection even when columns are unknown", () => {
    expect(filtersForChart(inputs({ spec: spec("c2", "ds1", "g"), widgetId: "w2", bus }))).toEqual([
      inP("region", "N"),
    ]);
  });
});

describe("filtersForChart: followAllGroups", () => {
  it("lets a widget in no group take the selections of every group", () => {
    let bus = publish(EMPTY_BUS, "g1", "w1", sel("ds1", inP("region", "N")));
    bus = publish(bus, "g2", "w2", sel("ds1", inP("channel", "web")));
    const kpi = spec("kpi:x");
    expect(filtersForChart(inputs({ spec: kpi, widgetId: "k", bus }))).toEqual([]);
    expect(
      filtersForChart(inputs({ spec: kpi, widgetId: "k", bus, followAllGroups: true })),
    ).toEqual([inP("region", "N"), inP("channel", "web")]);
  });
});

describe("pruneBus", () => {
  it("drops selections of widgets that left the document and keeps the rest", () => {
    let bus: BusState = publish(EMPTY_BUS, "g", "w1", sel("ds1", inP("a", "x")));
    bus = publish(bus, "g", "w2", sel("ds1", inP("b", "y")));
    bus = publish(bus, "h", "w3", sel("ds1", inP("c", "z")));
    expect(pruneBus(bus, new Set(["w2", "w3"]))).toEqual({
      g: { w2: sel("ds1", inP("b", "y")) },
      h: { w3: sel("ds1", inP("c", "z")) },
    });
    expect(pruneBus(bus, new Set(["w1", "w2", "w3"]))).toBe(bus);
    expect(pruneBus(bus, new Set())).toEqual({});
  });
});
