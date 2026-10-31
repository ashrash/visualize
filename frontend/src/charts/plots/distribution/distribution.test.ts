import { describe, expect, it } from "vitest";
import type { ChartSpec } from "../../../api/types";
import { makeData, makeSpec, makeTheme } from "../../core/testing";
import type { PlotEntry } from "../../core/types";
import entries from "./index";

const byKey = (key: string): PlotEntry => {
  const entry = entries.find((e) => e.key === key);
  if (!entry) throw new Error(`no ${key}`);
  return entry;
};

const theme = makeTheme();
// biome-ignore lint/suspicious/noExplicitAny: tests probe deep into the ECharts option
type Opt = Record<string, any>;

const render = (key: string, spec: Partial<ChartSpec>, data: ReturnType<typeof makeData>) =>
  byKey(key).render(makeSpec({ plotType: key as never, ...spec }), data, theme, undefined) as Opt;

describe("histogram", () => {
  const data = makeData(
    { x_lo: [0, 10, 20], x_hi: [10, 20, 30], count: [3, 5, 2] },
    { x_lo: "value", x_hi: "value", count: "Count" },
  );

  it("draws one bar per bin with contiguous category gaps", () => {
    const o = render("histogram", {}, data);
    expect(o.xAxis.type).toBe("category");
    expect(o.xAxis.data).toHaveLength(3);
    expect(o.series).toHaveLength(1);
    expect(o.series[0]).toMatchObject({ type: "bar", data: [3, 5, 2], barCategoryGap: "0%" });
  });

  it("pivots a color column into one series per group on shared bins", () => {
    const grouped = makeData(
      {
        x_lo: [0, 10, 0, 10],
        x_hi: [10, 20, 10, 20],
        count: [3, 5, 1, 2],
        color: ["a", "a", "b", "b"],
      },
      { x_lo: "v", x_hi: "v", count: "Count" },
    );
    const o = render("histogram", {}, grouped);
    expect(o.series.map((s: Opt) => s.name)).toEqual(["a", "b"]);
    expect(o.series[0].data).toEqual([3, 5]);
    expect(o.series[1].data).toEqual([1, 2]);
    expect(o.legend.show).toBe(true);
  });
});

describe("frequency polygon", () => {
  it("draws a line through the bin midpoints", () => {
    const data = makeData({ x: [5, 15, 25], y0: [3, 5, 2] }, { x: "v", y0: "Count" });
    const o = render("frequency_polygon", {}, data);
    expect(o.series[0].data).toEqual([
      [5, 3],
      [15, 5],
      [25, 2],
    ]);
  });
});

describe("density", () => {
  it("draws one filled line for an ungrouped curve", () => {
    const data = makeData({ x: [0, 1, 2], y0: [0.1, 0.4, 0.1] }, { x: "v", y0: "Density" });
    const o = render("density", {}, data);
    expect(o.series).toHaveLength(1);
    expect(o.series[0].areaStyle).toBeDefined();
  });

  it("draws one curve per color group", () => {
    const data = makeData(
      { x: [0, 1, 0, 1], y0: [0.1, 0.2, 0.3, 0.4], color: ["a", "a", "b", "b"] },
      { x: "v" },
    );
    const o = render("density", {}, data);
    expect(o.series.map((s: Opt) => s.name)).toEqual(["a", "b"]);
  });
});

describe("box", () => {
  const data = makeData(
    {
      x: ["a", "b"],
      q1: [1, 10],
      median: [2, 20],
      q3: [3, 30],
      lo: [0, 8],
      hi: [4, 32],
      n: [5, 6],
    },
    { x: "group" },
    { meta: { params: { outliers: [{ x: "a", value: 100 }], truncatedOutliers: false } } },
  );

  it("builds [lo, q1, median, q3, hi] rows for the boxplot series", () => {
    const o = render("box", {}, data);
    const boxSeries = o.series.find((s: Opt) => s.type === "boxplot");
    expect(boxSeries.data).toEqual([
      [0, 1, 2, 3, 4],
      [8, 10, 20, 30, 32],
    ]);
  });

  it("plots outliers as a scatter series positioned at their category", () => {
    const o = render("box", {}, data);
    const outlierSeries = o.series.find((s: Opt) => s.type === "scatter");
    expect(outlierSeries.data).toEqual([[0, 100]]);
  });

  it("matches an outlier's category by raw value, not by stringifying it", () => {
    // A null, numeric or boolean category must match the real xs entry, not the first one:
    // String(null) === "a".indexOf(...) kind of bugs silently mis-plot these.
    const withNullCategory = makeData(
      {
        x: [null, "a"],
        q1: [1, 10],
        median: [2, 20],
        q3: [3, 30],
        lo: [0, 8],
        hi: [4, 32],
        n: [5, 6],
      },
      { x: "group" },
      { meta: { params: { outliers: [{ x: "a", value: 100 }], truncatedOutliers: false } } },
    );
    const o = render("box", {}, withNullCategory);
    const outlierSeries = o.series.find((s: Opt) => s.type === "scatter");
    expect(outlierSeries.data).toEqual([[1, 100]]);
  });

  it("matches a numeric category by value, not by its stringified form", () => {
    // This is the case that actually discriminates the old bug from the fix: with numeric
    // categories xs=[1, 2], the old `xs.indexOf(String(o.x))` compared the string "2" against
    // the numbers 1 and 2 with strict equality, which never matches (indexOf has no implicit
    // coercion), silently plotting the outlier on category 1 instead of category 2. A string
    // category like "a" would have passed under the old code too, since String("a") === "a".
    const numericCategory = makeData(
      { x: [1, 2], q1: [1, 10], median: [2, 20], q3: [3, 30], lo: [0, 8], hi: [4, 32], n: [5, 6] },
      { x: "group" },
      { meta: { params: { outliers: [{ x: 2, value: 100 }], truncatedOutliers: false } } },
    );
    const o = render("box", {}, numericCategory);
    const outlierSeries = o.series.find((s: Opt) => s.type === "scatter");
    expect(outlierSeries.data).toEqual([[1, 100]]);
  });
});

describe("violin", () => {
  // Does NOT use the official @echarts-x/custom-violin series: its KDE sweeps a hard-coded
  // [0, 10] range regardless of the data's actual scale, clipping tails and drawing the wrong
  // relative width for groups spanning a small fraction of that range. This draws the server's
  // own per-group KDE (`kde_by_group`, same kernel as `density`) through a custom renderItem.
  const categories = ["a", "b"];
  const rows: [string, number, number, number][] = [
    ["a", 0, 0, 10],
    ["a", 1, 1, 10],
    ["a", 2, 0, 10],
    ["b", 0, 0, 5],
    ["b", 1, 0.5, 5],
    ["b", 2, 0, 5],
  ];
  const data = makeData(
    {
      x: rows.map((r) => r[0]),
      y: rows.map((r) => r[1]),
      density: rows.map((r) => r[2]),
      n: rows.map((r) => r[3]),
    },
    { x: "species", y: "body_mass_g" },
  );

  // A faithful-enough stand-in for ECharts' custom-series `api`. On a *category* axis, real
  // ECharts' `api.value(0, ...)` returns the category's numeric ordinal (e.g. `0`), not the
  // category string ("a") that lives in the raw data row — and `api.coord` only resolves that
  // numeric ordinal, never a stringified one (there is no category literally named "0"). This
  // mock enforces both: `value(0, ...)` returns the ordinal, and `coord` returns NaN for
  // anything that isn't that numeric ordinal, so a renderItem that re-stringifies the category
  // (the regression this test exists to catch) fails here instead of in production.
  function mockApi(currentIndex: number) {
    return {
      value: (dim: number, idx = currentIndex) => {
        const row = rows[idx];
        if (dim === 0) return categories.indexOf(row?.[0] as string);
        return row?.[dim];
      },
      coord: ([catIndex, y]: [number, number]) => {
        const valid = typeof catIndex === "number" && categories[catIndex] !== undefined;
        return valid ? [catIndex * 100, 300 - y * 50] : [Number.NaN, Number.NaN];
      },
      size: () => [80],
      style: () => ({ fill: "blue" }),
    };
  }

  it("draws one mirrored polygon per category, scaled to its own peak density", () => {
    const o = render("violin", {}, data);
    const renderItem = o.series[0].renderItem as (p: Opt, a: Opt) => Opt | undefined;
    const context: Opt = {};

    // The first row of "a" (dataIndexInside 0) draws its whole polygon at once.
    const shapeA = renderItem(
      { context, dataInsideLength: rows.length, dataIndexInside: 0 },
      mockApi(0),
    );
    expect(shapeA?.type).toBe("polygon");
    const points = shapeA?.shape.points as number[][];
    expect(points).toHaveLength(6); // 3 grid points, mirrored left + right
    // The grid point with the peak density (y=1) reaches the full half-width (80 * 0.42).
    expect(points[1]?.[0]).toBeCloseTo(0 - 80 * 0.42);
    expect(points[4]?.[0]).toBeCloseTo(0 + 80 * 0.42);
    // The zero-density ends collapse to the category's center line.
    expect(points[0]?.[0]).toBeCloseTo(0);
    expect(points[2]?.[0]).toBeCloseTo(0);

    // Any other row of the same category draws nothing (already drawn).
    const shapeRepeat = renderItem(
      { context, dataInsideLength: rows.length, dataIndexInside: 1 },
      mockApi(1),
    );
    expect(shapeRepeat).toBeUndefined();

    // The first row of "b" draws its own, independently-scaled polygon.
    const shapeB = renderItem(
      { context, dataInsideLength: rows.length, dataIndexInside: 3 },
      mockApi(3),
    );
    expect(shapeB?.type).toBe("polygon");
    const pointsB = shapeB?.shape.points as number[][];
    // "b"'s peak density also reaches the full half-width, even though it's half of "a"'s.
    expect(pointsB[1]?.[0]).toBeCloseTo(100 - 80 * 0.42);
  });

  it("escapes the category name in the tooltip (stored XSS via a crafted column/category name)", () => {
    const malicious = makeData(
      {
        x: ['<img src=x onerror="alert(1)">'],
        y: [1],
        density: [1],
        n: [3],
      },
      { x: "species", y: "v" },
    );
    const o = render("violin", {}, malicious);
    const formatter = o.series[0].tooltip.formatter as (p: Opt) => string;
    const html = formatter({ value: ['<img src=x onerror="alert(1)">', 1, 1, 3] });
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
  });

  it("labels numeric and null categories as strings, so a category axis can look them up", () => {
    // A bare number as a category-axis data value is read by ECharts as an ordinal *index*,
    // not a value to look up, and null has no category of its own - both must become strings.
    const numeric = makeData(
      { x: [10, 20], y: [0, 0], density: [1, 1], n: [5, 5] },
      { x: "rating", y: "v" },
    );
    const withNull = makeData(
      { x: [null, "a"], y: [0, 0], density: [1, 1], n: [5, 5] },
      { x: "g", y: "v" },
    );
    expect(render("violin", {}, numeric).xAxis.data).toEqual(["10", "20"]);
    expect(render("violin", {}, numeric).series[0].data[0]?.[0]).toBe("10");
    expect(render("violin", {}, withNull).xAxis.data).toEqual(["(null)", "a"]);
  });
});

describe("strip and swarm", () => {
  const data = makeData({ x: ["a", "a", "b"], y: [1, 2, 3] }, { x: "g", y: "v" });

  it("labels numeric and null categories as strings", () => {
    const numeric = makeData({ x: [10, 20], y: [0, 0] }, { x: "rating", y: "v" });
    const withNull = makeData({ x: [null, "a"], y: [0, 0] }, { x: "g", y: "v" });
    expect(render("strip", {}, numeric).xAxis.data).toEqual(["10", "20"]);
    expect(render("strip", {}, withNull).xAxis.data).toEqual(["(null)", "a"]);
  });

  it("strip jitters within the category, allowing overlap", () => {
    const o = render("strip", {}, data);
    expect(o.xAxis.jitter).toBeGreaterThan(0);
    expect(o.xAxis.jitterOverlap).toBe(true);
    expect(o.series[0].data).toEqual([
      ["a", 1],
      ["a", 2],
      ["b", 3],
    ]);
  });

  it("swarm disables overlap (a beeswarm layout)", () => {
    const o = render("swarm", {}, data);
    expect(o.xAxis.jitterOverlap).toBe(false);
  });
});

describe("Q-Q plot", () => {
  it("draws the sample scatter and a reference line from slope/intercept", () => {
    const data = makeData(
      { x: [-1, 0, 1], y: [8, 10, 12] },
      { x: "Theoretical quantile", y: "value" },
      { meta: { params: { slope: 2, intercept: 10 } } },
    );
    const o = render("qq", {}, data);
    expect(o.series[0].data).toEqual([
      [-1, 8],
      [0, 10],
      [1, 12],
    ]);
    expect(o.series[1].data).toEqual([
      [-1, 8],
      [1, 12],
    ]);
  });
});

describe("ECDF", () => {
  it("draws a step line through the cumulative probabilities", () => {
    const data = makeData({ x: [1, 2, 3], y: [0.25, 0.75, 1] }, { x: "v", y: "F(x)" });
    const o = render("ecdf", {}, data);
    expect(o.series[0]).toMatchObject({ step: "end", type: "line" });
    expect(o.yAxis.max).toBe(1);
  });

  it("draws one step line per color group", () => {
    const data = makeData(
      { x: [1, 2, 3, 4], y: [0.5, 1, 0.5, 1], color: ["a", "a", "b", "b"] },
      { x: "v" },
    );
    const o = render("ecdf", {}, data);
    expect(o.series.map((s: Opt) => s.name)).toEqual(["a", "b"]);
  });
});
