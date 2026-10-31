import { describe, expect, it } from "vitest";
import {
  brushPayloadFrom,
  legendChangeFrom,
  legendValues,
  pointHitFrom,
  resolveBrushValues,
} from "./events";
import { makeData } from "./testing";

describe("pointHitFrom", () => {
  it("keeps what a consumer needs from a series click", () => {
    expect(
      pointHitFrom({
        componentType: "series",
        seriesType: "bar",
        seriesIndex: 1,
        seriesName: "Sales",
        name: "Germany",
        dataIndex: 3,
        value: 42,
        color: "#0072b2",
        event: { event: { shiftKey: true } },
      }),
    ).toEqual({
      seriesIndex: 1,
      seriesName: "Sales",
      name: "Germany",
      dataIndex: 3,
      value: 42,
      color: "#0072b2",
      shiftKey: true,
    });
  });

  it("ignores clicks that are not on a series mark", () => {
    expect(pointHitFrom({ componentType: "title" })).toBeNull();
    expect(pointHitFrom(undefined)).toBeNull();
  });
});

describe("brushPayloadFrom", () => {
  const categoryOption = { xAxis: [{ type: "category", data: ["a", "b", "c", "d"] }], yAxis: [] };

  it("turns an x-range brush on a category axis into the category names it covers", () => {
    const payload = brushPayloadFrom(
      { areas: [{ brushType: "lineX", coordRange: [0.6, 2.2] }] },
      categoryOption,
    );
    expect(payload).toMatchObject({ axis: "x", kind: "category", categories: ["b", "c"] });
    expect(payload?.cleared).toBe(false);
  });

  it("unions several areas (multi-select) and clamps to the axis", () => {
    const payload = brushPayloadFrom(
      {
        areas: [
          { brushType: "lineX", coordRange: [-1, 0.2] },
          { brushType: "lineX", coordRange: [2.8, 9] },
        ],
      },
      categoryOption,
    );
    expect(payload?.categories).toEqual(["a", "d"]);
  });

  it("reports numeric and time ranges in axis units", () => {
    const payload = brushPayloadFrom(
      { areas: [{ brushType: "lineX", coordRange: [10, 20] }] },
      { xAxis: [{ type: "time" }], yAxis: [] },
    );
    expect(payload).toMatchObject({ kind: "time", ranges: [[10, 20]], categories: [] });
  });

  it("uses the y axis for a horizontal (lineY) brush", () => {
    const payload = brushPayloadFrom(
      { areas: [{ brushType: "lineY", coordRange: [0, 1] }] },
      { xAxis: [{ type: "value" }], yAxis: [{ type: "category", data: ["p", "q", "r"] }] },
    );
    expect(payload).toMatchObject({ axis: "y", kind: "category", categories: ["p", "q"] });
  });

  it("reports a cleared brush", () => {
    expect(brushPayloadFrom({ areas: [] }, categoryOption)).toMatchObject({
      cleared: true,
      categories: [],
      ranges: [],
    });
  });

  it("returns null for brush shapes it cannot turn into a range", () => {
    expect(
      brushPayloadFrom(
        {
          areas: [
            {
              brushType: "rect",
              coordRange: [
                [0, 1],
                [0, 1],
              ],
            },
          ],
        },
        categoryOption,
      ),
    ).toBeNull();
  });
});

describe("resolveBrushValues", () => {
  it("maps selected category names back to raw column values", () => {
    const data = makeData({ x: [2024, 2025, 2026], y0: [1, 2, 3] });
    expect(resolveBrushValues(data, ["2024", "2026", "nope"])).toEqual([2024, 2026]);
  });
});

describe("legendChangeFrom", () => {
  it("lists what is shown and hidden after a toggle", () => {
    expect(legendChangeFrom({ name: "B", selected: { A: true, B: false, C: true } })).toEqual({
      toggled: "B",
      selected: { A: true, B: false, C: true },
      shown: ["A", "C"],
      hidden: ["B"],
    });
  });
});

describe("legendValues", () => {
  it("maps series names to raw color values, including 'group · measure' names", () => {
    const data = makeData({ x: ["a", "b"], color: ["east", "west"], y0: [1, 2] });
    expect(legendValues(data, ["east", "west · Sum of sales", "ghost"])).toEqual(["east", "west"]);
  });
});
