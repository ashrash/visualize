import { beforeAll, describe, expect, it } from "vitest";
import type { ChartSpec } from "../../../api/types";
import { makeData, makeSpec, makeTheme } from "../../core/testing";
import type { PlotEntry } from "../../core/types";
import { loadMap, matchRegion } from "./assets/maps";
import entries from "./index";

const byKey = (key: string): PlotEntry => {
  const entry = entries.find((e) => e.key === key);
  if (!entry) throw new Error(`no ${key}`);
  return entry;
};

const theme = makeTheme();
// biome-ignore lint/suspicious/noExplicitAny: tests probe deep into the ECharts option
type Opt = Record<string, any>;

const spec = (plotType: string, overrides: Partial<ChartSpec> = {}) =>
  makeSpec({ plotType: plotType as never, ...overrides });

describe("geo plot family registration", () => {
  it("registers every Plots.md Section 7 plot type", () => {
    expect(entries.map((e) => e.key).sort()).toEqual(
      ["bubble_map", "cartogram", "choropleth", "dot_density", "geo_heatmap"].sort(),
    );
  });

  it("every entry declares encodings, params and a recommend()", () => {
    for (const entry of entries) {
      expect(entry.encodingsSchema.length).toBeGreaterThan(0);
      expect(typeof entry.recommend).toBe("function");
      expect(typeof entry.prepare).toBe("function");
    }
  });
});

describe("choropleth", () => {
  const s = spec("choropleth");
  const data = makeData(
    { region: ["USA", "CAN", "ZZZ"], y0: [10, 5, 1] },
    { region: "country", y0: "Sum of population" },
  );

  beforeAll(async () => {
    await byKey("choropleth").prepare?.(s);
  });

  it("builds a map series with a visualMap colored by y0", () => {
    const o = byKey("choropleth").render(s, data, theme) as Opt;
    expect(o.series).toHaveLength(1);
    expect(o.series[0].type).toBe("map");
    expect(o.series[0].map).toMatch(/^viz-country:110m$/);
    expect(o.visualMap.min).toBe(1);
    expect(o.visualMap.max).toBe(10);
    const byName = Object.fromEntries(
      o.series[0].data.map((d: { name: string; value: number }) => [d.name, d.value]),
    );
    // USA/CAN match the bundled map's ISO3 codes; ZZZ is not a real country.
    expect(byName.USA).toBe(10);
    expect(byName.CAN).toBe(5);
  });

  it("matches a country by normalized name, not only by code", () => {
    const named = makeData({ region: ["United States of America"], y0: [42] }, { region: "x" });
    const o = byKey("choropleth").render(s, named, theme) as Opt;
    expect(o.series[0].data[0].name).toBe("USA");
  });

  it("reports regions that don't match the bundled map", () => {
    const warnings = byKey("choropleth").warnings?.(s, data) ?? [];
    expect(warnings.length).toBe(1);
    expect(warnings[0]).toContain("ZZZ");
    expect(warnings[0]).toContain("1 region");
  });

  it("reports no warnings once every region matches", () => {
    const clean = makeData({ region: ["USA", "CAN"], y0: [1, 2] }, { region: "x" });
    expect(byKey("choropleth").warnings?.(s, clean) ?? []).toEqual([]);
  });

  it("picks the US states map when level=us_state", async () => {
    const stateSpec = spec("choropleth", { params: { level: "us_state", resolution: "110m" } });
    await byKey("choropleth").prepare?.(stateSpec);
    const stateData = makeData({ region: ["CA", "TX", "NOPE"], y0: [1, 2, 3] }, { region: "x" });
    const o = byKey("choropleth").render(stateSpec, stateData, theme) as Opt;
    expect(o.series[0].map).toBe("viz-us_state:110m");
    const names = o.series[0].data.map((d: { name: string }) => d.name);
    expect(names).toContain("CA");
    expect(names).toContain("TX");
  });

  it("recommends itself for a region-shaped geo column but not a lat/lon pair", () => {
    const recommend = byKey("choropleth").recommend;
    expect(recommend).toBeDefined();
    const regionScore = recommend?.([
      { name: "country", type: "VARCHAR", semantic: "geo", nullCount: 0, approxDistinct: 10 },
    ]);
    const latLonScore = recommend?.([
      { name: "lat", type: "DOUBLE", semantic: "geo", nullCount: 0, approxDistinct: 10 },
      { name: "lon", type: "DOUBLE", semantic: "geo", nullCount: 0, approxDistinct: 10 },
    ]);
    expect(regionScore ?? 0).toBeGreaterThan(latLonScore ?? 1);
  });
});

describe("region matching (Natural Earth's -99 sentinel)", () => {
  it("never matches the literal missing-value sentinel to a real country", async () => {
    const index = await loadMap("country", "110m");
    // France, Norway and several disputed territories carry Natural Earth's "-99" missing-
    // value sentinel in one or more raw ISO fields (verified against the bundled asset). A
    // dataset using the same literal text as its own missing-value marker must not get
    // colored in as whichever of those happened to be indexed last.
    expect(matchRegion(index, "-99")).toBeNull();
  });

  it("still matches countries whose raw ISO3/ISO2 needed a fallback", async () => {
    const index = await loadMap("country", "110m");
    expect(matchRegion(index, "France")).toBe("FRA");
    expect(matchRegion(index, "Norway")).toBe("NOR");
    expect(matchRegion(index, "FR")).toBe("FRA");
    expect(matchRegion(index, "TW")).toBe("TWN"); // not Taiwan's raw ISO2 "CN-TW"
  });
});

describe("bubble map", () => {
  const s = spec("bubble_map");
  const data = makeData(
    { lat: [10, 20, 30], lon: [1, 2, 3], y0: [5, 50, 10], color: ["a", "b", "a"] },
    { lat: "lat", lon: "lon", y0: "Sum of size", color: "kind" },
  );

  beforeAll(async () => {
    await byKey("bubble_map").prepare?.(s);
  });

  it("draws one scatter series per color group on the geo coordinate system", () => {
    const o = byKey("bubble_map").render(s, data, theme) as Opt;
    expect(o.geo.map).toBe("viz-country:110m");
    expect(o.series).toHaveLength(2);
    for (const series of o.series) {
      expect(series.type).toBe("scatter");
      expect(series.coordinateSystem).toBe("geo");
    }
    const aSeries = o.series.find((sr: Opt) => sr.name === "a");
    expect(aSeries.data).toEqual([
      [1, 10, 5],
      [3, 30, 10],
    ]);
  });

  it("scales symbol size with the value", () => {
    const o = byKey("bubble_map").render(s, data, theme) as Opt;
    const bSeries = o.series.find((sr: Opt) => sr.name === "b");
    const size = bSeries.symbolSize([2, 20, 50]);
    const smallSize = o.series.find((sr: Opt) => sr.name === "a").symbolSize([1, 10, 5]);
    expect(size).toBeGreaterThan(smallSize);
  });

  it("recommends itself only when both a latitude and a longitude column exist", () => {
    const recommend = byKey("bubble_map").recommend;
    const withBoth = recommend?.([
      { name: "lat", type: "DOUBLE", semantic: "geo", nullCount: 0, approxDistinct: 1 },
      { name: "lon", type: "DOUBLE", semantic: "geo", nullCount: 0, approxDistinct: 1 },
    ]);
    const withOne = recommend?.([
      { name: "lat", type: "DOUBLE", semantic: "geo", nullCount: 0, approxDistinct: 1 },
    ]);
    expect(withBoth ?? 0).toBeGreaterThan(withOne ?? 1);
  });
});

describe("dot density", () => {
  const s = spec("dot_density");
  const data = makeData(
    { lat: [1, 2, 3, 4], lon: [1, 2, 3, 4], color: ["a", "a", "b", "b"] },
    { lat: "lat", lon: "lon", color: "category" },
  );

  beforeAll(async () => {
    await byKey("dot_density").prepare?.(s);
  });

  it("draws capped/sampled points as a large scatter on geo, grouped by color", () => {
    const o = byKey("dot_density").render(s, data, theme) as Opt;
    expect(o.series).toHaveLength(2);
    for (const series of o.series) {
      expect(series.coordinateSystem).toBe("geo");
      expect(series.large).toBe(true);
    }
    const total = o.series.reduce((n: number, sr: Opt) => n + sr.data.length, 0);
    expect(total).toBe(4);
  });
});

describe("geographic heatmap", () => {
  const s = spec("geo_heatmap");
  const data = makeData(
    { lat: [1, 2], lon: [3, 4], y0: [7, 9] },
    { lat: "lat", lon: "lon", y0: "Count" },
  );

  beforeAll(async () => {
    await byKey("geo_heatmap").prepare?.(s);
  });

  it("draws a heatmap series on the geo coordinate system from the binned cells", () => {
    const o = byKey("geo_heatmap").render(s, data, theme) as Opt;
    expect(o.series).toHaveLength(1);
    expect(o.series[0].type).toBe("heatmap");
    expect(o.series[0].coordinateSystem).toBe("geo");
    expect(o.series[0].data).toEqual([
      [3, 1, 7],
      [4, 2, 9],
    ]);
    expect(o.visualMap.min).toBe(7);
    expect(o.visualMap.max).toBe(9);
  });
});

describe("cartogram", () => {
  const s = spec("cartogram");
  const data = makeData(
    { region: ["USA", "ZZZ"], lon: [-100, 10], lat: [40, 10], y0: [300, 5], radius: [5, 1] },
    { region: "country", y0: "Sum of population" },
  );

  beforeAll(async () => {
    await byKey("cartogram").prepare?.(s);
  });

  it("draws one custom-series point per region on the geo coordinate system", () => {
    const o = byKey("cartogram").render(s, data, theme) as Opt;
    expect(o.series).toHaveLength(1);
    expect(o.series[0].type).toBe("custom");
    expect(o.series[0].coordinateSystem).toBe("geo");
    expect(o.series[0].data).toHaveLength(2);
    expect(o.series[0].data[0].name).toBe("USA"); // matched to the bundled map
    expect(o.series[0].data[1].name).toBe("ZZZ"); // unmatched: falls back to the raw value
  });

  it("sizes each circle in screen space via api.coord, proportional to the server radius", () => {
    const o = byKey("cartogram").render(s, data, theme) as Opt;
    const series = o.series[0];
    // A stub of the renderItem API: coord does a simple, known linear projection so the
    // resulting pixel radius can be checked algebraically, and value reads back the series
    // data's own `value` array for whichever item is being rendered.
    const stubApi = (dataIndex: number) => ({
      value: (i: number) => series.data[dataIndex].value[i],
      coord: ([lon, lat]: [number, number]) => [lon * 10, lat * 10],
      style: (extra: Record<string, unknown>) => extra,
    });
    const circleOf = (dataIndex: number) => {
      const group = series.renderItem({ dataIndex }, stubApi(dataIndex));
      return group.children.find((c: Opt) => c.type === "circle");
    };
    const bigCircle = circleOf(0); // radius 5
    const smallCircle = circleOf(1); // radius 1
    expect(bigCircle.shape.r).toBeGreaterThan(smallCircle.shape.r);
    // The circle's center is wherever api.coord places [lon, lat] right now (so panning or
    // zooming the map moves and rescales it), not a value baked in at render time.
    expect(bigCircle.shape.cx).toBe(-100 * 10);
    expect(bigCircle.shape.cy).toBe(40 * 10);
  });

  it("reports regions that don't match the backdrop map", () => {
    const warnings = byKey("cartogram").warnings?.(s, data) ?? [];
    expect(warnings.length).toBe(1);
    expect(warnings[0]).toContain("ZZZ");
  });
});
