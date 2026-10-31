import { describe, expect, it } from "vitest";
import {
  addFilter,
  addTab,
  addWidget,
  applyLayout,
  bottomRow,
  createDashboard,
  duplicateDashboard,
  removeFilter,
  removeTab,
  removeWidget,
  setChartLinkGroup,
} from "./model";
import type { Widget } from "./types";

const chart = (id: string): Widget => ({ kind: "chart", chartId: id });

describe("addWidget", () => {
  it("places the first widget at the origin and the next beside it when it fits", () => {
    let d = createDashboard();
    const tab = d.tabs?.[0];
    if (!tab) throw new Error("no tab");
    const a = addWidget(d, tab.id, chart("a"), { w: 6, h: 4 });
    d = a.dashboard;
    const b = addWidget(d, tab.id, chart("b"), { w: 6, h: 4 });
    expect(a.item).toMatchObject({ x: 0, y: 0, w: 6, h: 4 });
    expect(b.item).toMatchObject({ x: 6, y: 0 });
  });

  it("starts a new row when the last row is full", () => {
    let d = createDashboard();
    const tab = d.tabs?.[0];
    if (!tab) throw new Error("no tab");
    d = addWidget(d, tab.id, chart("a"), { w: 8, h: 4 }).dashboard;
    const b = addWidget(d, tab.id, chart("b"), { w: 6, h: 3 });
    expect(b.item).toMatchObject({ x: 0, y: 4 });
    expect(bottomRow(b.dashboard.tabs?.[0]?.items ?? [])).toBe(7);
  });

  it("rejects an unknown tab", () => {
    expect(() => addWidget(createDashboard(), "nope", chart("a"))).toThrow();
  });
});

describe("applyLayout", () => {
  it("applies moved and resized positions, and keeps identity when nothing changed", () => {
    let d = createDashboard();
    const tab = d.tabs?.[0];
    if (!tab) throw new Error("no tab");
    const added = addWidget(d, tab.id, chart("a"), { w: 6, h: 4 });
    d = added.dashboard;
    const same = [{ i: added.item.i, x: 0, y: 0, w: 6, h: 4 }];
    expect(applyLayout(d, tab.id, same)).toBe(d);
    const moved = applyLayout(d, tab.id, [{ i: added.item.i, x: 3, y: 2, w: 9, h: 5 }]);
    expect(moved.tabs?.[0]?.items?.[0]).toMatchObject({ x: 3, y: 2, w: 9, h: 5 });
    expect(moved.tabs?.[0]?.items?.[0]?.widget).toEqual(chart("a"));
  });
});

describe("tabs", () => {
  it("adds a numbered tab and refuses to remove the last one", () => {
    const d = createDashboard();
    const { dashboard, tab } = addTab(d);
    expect(tab.name).toBe("Tab 2");
    expect(dashboard.tabs).toHaveLength(2);
    expect(removeTab(dashboard, tab.id).tabs).toHaveLength(1);
    expect(removeTab(removeTab(dashboard, tab.id), d.tabs?.[0]?.id ?? "").tabs).toHaveLength(1);
  });
});

describe("filters", () => {
  it("removing a filter also removes the filter widgets that showed it", () => {
    let d = createDashboard();
    const tab = d.tabs?.[0];
    if (!tab) throw new Error("no tab");
    d = addFilter(d, { id: "f1", label: "Date", field: "d", fieldMap: {}, predicate: null });
    d = addWidget(d, tab.id, { kind: "filter", filterId: "f1", control: "date_range" }).dashboard;
    d = addWidget(d, tab.id, chart("a")).dashboard;
    d = removeFilter(d, "f1");
    expect(d.filters).toHaveLength(0);
    expect(d.tabs?.[0]?.items?.map((i) => i.widget.kind)).toEqual(["chart"]);
  });
});

describe("setChartLinkGroup", () => {
  it("moves a chart between groups and drops groups that end up empty", () => {
    let d = createDashboard();
    d = setChartLinkGroup(d, "a", "g1");
    d = setChartLinkGroup(d, "b", "g1");
    expect(d.linkGroups).toEqual([{ id: "g1", chartIds: ["a", "b"] }]);
    d = setChartLinkGroup(d, "a", "g2");
    expect(d.linkGroups).toEqual([
      { id: "g1", chartIds: ["b"] },
      { id: "g2", chartIds: ["a"] },
    ]);
    d = setChartLinkGroup(d, "b", null);
    expect(d.linkGroups).toEqual([{ id: "g2", chartIds: ["a"] }]);
  });
});

describe("removeWidget", () => {
  it("removes one item by id", () => {
    let d = createDashboard();
    const tab = d.tabs?.[0];
    if (!tab) throw new Error("no tab");
    const a = addWidget(d, tab.id, chart("a"));
    d = addWidget(a.dashboard, tab.id, chart("b")).dashboard;
    expect(removeWidget(d, tab.id, a.item.i).tabs?.[0]?.items).toHaveLength(1);
  });
});

describe("duplicateDashboard", () => {
  it("gives everything a new id but keeps the content, and re-points filter widgets", () => {
    let d = createDashboard("Sales");
    const tab = d.tabs?.[0];
    if (!tab) throw new Error("no tab");
    d = addFilter(d, { id: "f1", label: "Date", field: "d", fieldMap: {}, predicate: null });
    d = addWidget(d, tab.id, { kind: "filter", filterId: "f1", control: "date_range" }).dashboard;
    d = addWidget(d, tab.id, chart("a")).dashboard;
    const copy = duplicateDashboard(d);
    expect(copy.name).toBe("Sales copy");
    expect(copy.id).not.toBe(d.id);
    expect(copy.tabs?.[0]?.id).not.toBe(tab.id);
    const newFilter = copy.filters?.[0];
    expect(newFilter?.id).not.toBe("f1");
    const filterWidget = copy.tabs?.[0]?.items?.[0]?.widget;
    expect(filterWidget).toMatchObject({ kind: "filter", filterId: newFilter?.id });
    expect(copy.tabs?.[0]?.items?.[1]?.widget).toEqual(chart("a"));
    const ids = [
      ...(copy.tabs?.[0]?.items ?? []).map((i) => i.i),
      ...(d.tabs?.[0]?.items ?? []).map((i) => i.i),
    ];
    expect(new Set(ids).size).toBe(4);
  });
});
