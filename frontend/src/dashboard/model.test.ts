import { describe, expect, it } from "vitest";
import {
  addFilter,
  addTab,
  addWidget,
  applyLayout,
  bottomRow,
  createDashboard,
  duplicateDashboard,
  nudge,
  removeFilter,
  removeTab,
  removeWidget,
  setChartLinkGroup,
  settleLayout,
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

describe("nudge (keyboard move and resize)", () => {
  const at = (x: number, y: number, w: number, h: number) => ({ i: "w", x, y, w, h });

  it("moves one cell per arrow, inside the grid", () => {
    expect(nudge(at(2, 3, 4, 4), "ArrowRight", false)).toEqual(at(3, 3, 4, 4));
    expect(nudge(at(2, 3, 4, 4), "ArrowLeft", false)).toEqual(at(1, 3, 4, 4));
    expect(nudge(at(2, 3, 4, 4), "ArrowDown", false)).toEqual(at(2, 4, 4, 4));
    expect(nudge(at(2, 3, 4, 4), "ArrowUp", false)).toEqual(at(2, 2, 4, 4));
  });

  it("stops at the edges and says so (null) instead of leaving the grid", () => {
    expect(nudge(at(0, 0, 4, 4), "ArrowLeft", false)).toBeNull();
    expect(nudge(at(0, 0, 4, 4), "ArrowUp", false)).toBeNull();
    expect(nudge(at(8, 0, 4, 4), "ArrowRight", false)).toBeNull(); // 8 + 4 = 12 columns
    expect(nudge(at(7, 0, 4, 4), "ArrowRight", false)).toEqual(at(8, 0, 4, 4));
  });

  it("Shift resizes: right and down grow, left and up shrink, never below the minimum", () => {
    expect(nudge(at(0, 0, 4, 4), "ArrowRight", true)).toEqual(at(0, 0, 5, 4));
    expect(nudge(at(0, 0, 4, 4), "ArrowDown", true)).toEqual(at(0, 0, 4, 5));
    expect(nudge(at(0, 0, 4, 4), "ArrowLeft", true)).toEqual(at(0, 0, 3, 4));
    expect(nudge(at(0, 0, 4, 4), "ArrowUp", true)).toEqual(at(0, 0, 4, 3));
    expect(nudge(at(0, 0, 2, 2), "ArrowLeft", true)).toBeNull();
    expect(nudge(at(0, 0, 2, 2), "ArrowUp", true)).toBeNull();
  });

  it("cannot grow past the right edge", () => {
    expect(nudge(at(8, 0, 4, 4), "ArrowRight", true)).toBeNull();
    expect(nudge(at(6, 0, 5, 4), "ArrowRight", true)).toEqual(at(6, 0, 6, 4));
  });
});

describe("settleLayout (the layout a keyboard step produces)", () => {
  const L = (i: string, x: number, y: number, w: number, h: number) => ({ i, x, y, w, h });

  it("moves a widget sideways", () => {
    expect(settleLayout([L("a", 0, 0, 6, 4)], L("a", 2, 0, 6, 4))).toEqual([L("a", 2, 0, 6, 4)]);
  });

  it("compacts upwards like the grid: a lone widget moved down settles back at the top", () => {
    expect(settleLayout([L("a", 0, 0, 6, 4)], L("a", 2, 1, 6, 4))).toEqual([L("a", 2, 0, 6, 4)]);
  });

  it("pushes a widget out of the way of the one that moved onto it", () => {
    expect(settleLayout([L("a", 0, 0, 6, 4), L("b", 6, 0, 6, 4)], L("a", 6, 0, 6, 4))).toEqual([
      L("a", 6, 0, 6, 4),
      L("b", 6, 4, 6, 4),
    ]);
  });

  it("makes room below for a widget that grew taller", () => {
    expect(settleLayout([L("a", 0, 0, 6, 4), L("c", 0, 4, 6, 4)], L("a", 0, 0, 6, 6))).toEqual([
      L("a", 0, 0, 6, 6),
      L("c", 0, 6, 6, 4),
    ]);
  });

  it("leaves a settled layout alone, and does not touch its input", () => {
    const layout = [L("a", 0, 0, 6, 4), L("c", 0, 4, 6, 4)];
    const copy = structuredClone(layout);
    expect(settleLayout(layout, L("a", 0, 0, 6, 4))).toEqual(copy);
    expect(layout).toEqual(copy);
  });
});
