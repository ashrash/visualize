import { describe, expect, it } from "vitest";
import { buildStatTables, buildTable } from "./table";
import { makeData } from "./testing";
import type { StatResult } from "./types";

const stat = (values: StatResult["values"], extra: Partial<StatResult> = {}): StatResult => ({
  values,
  method: "test",
  n: 10,
  nMissing: 0,
  sampled: false,
  warnings: [],
  ...extra,
});

describe("buildStatTables", () => {
  it("shows top-level scalars as a key/value table", () => {
    const { tables } = buildStatTables(stat({ auc: 0.912345678, label: "ok", flag: true }));
    expect(tables).toHaveLength(1);
    expect(tables[0]?.headers).toEqual(["Name", "Value"]);
    expect(tables[0]?.rows).toEqual([
      ["auc", "0.912346"],
      ["label", "ok"],
      ["flag", "true"],
    ]);
  });

  it("joins equal-length arrays into one columnar table", () => {
    const { tables } = buildStatTables(stat({ fpr: [0, 0.5, 1], tpr: [0, 0.8, 1], auc: 0.9 }));
    const columnar = tables.find((t) => t.headers.includes("fpr"));
    expect(columnar?.headers).toEqual(["#", "fpr", "tpr"]);
    expect(columnar?.rows).toEqual([
      ["1", "0", "0"],
      ["2", "0.5", "0.8"],
      ["3", "1", "1"],
    ]);
  });

  it("turns an array of objects into rows over the union of keys", () => {
    const { tables } = buildStatTables(
      stat({
        steps: [
          { t: 1, s: 0.9 },
          { t: 2, s: 0.8, ci: { lo: 0.7, hi: 0.9 } },
        ],
      }),
    );
    expect(tables[0]?.caption).toBe("steps");
    expect(tables[0]?.headers).toEqual(["#", "t", "s", "ci.lo", "ci.hi"]);
    expect(tables[0]?.rows[0]).toEqual(["1", "1", "0.9", "", ""]);
    expect(tables[0]?.rows[1]).toEqual(["2", "2", "0.8", "0.7", "0.9"]);
  });

  it("lays a matrix out with one column per entry", () => {
    const { tables } = buildStatTables(
      stat({
        loadings: [
          [1, 2],
          [3, 4],
        ],
      }),
    );
    expect(tables[0]?.headers).toEqual(["#", "1", "2"]);
    expect(tables[0]?.rows).toEqual([
      ["1", "1", "2"],
      ["2", "3", "4"],
    ]);
  });

  it("recurses into nested objects with a dotted caption", () => {
    const { tables } = buildStatTables(stat({ model: { params: { a: 1, b: 2 }, r2: 0.5 } }));
    const captions = tables.map((t) => t.caption);
    expect(captions).toContain("model");
    expect(captions).toContain("model.params");
  });

  it("caps rows and reports the rest", () => {
    const { tables } = buildStatTables(stat({ v: [1, 2, 3, 4, 5] }), 3);
    expect(tables[0]?.rows).toHaveLength(3);
    expect(tables[0]?.truncated).toBe(2);
  });

  it("summarises method, n and sampling, and survives empty values", () => {
    const out = buildStatTables(stat({}, { method: "pca", n: 5, sampled: true }));
    expect(out.summary).toBe("pca, n = 5 (sampled)");
    expect(out.tables).toEqual([]);
  });
});

describe("buildTable", () => {
  it("uses display labels and formats temporal cells", () => {
    const data = makeData(
      { x: [Date.UTC(2026, 0, 1), Date.UTC(2026, 0, 2)], y0: [1.5, null] },
      { x: "day", y0: "Sum of sales" },
      { kinds: { x: "temporal" } },
    );
    expect(buildTable(data)).toEqual({
      headers: ["day", "Sum of sales"],
      rows: [
        ["2026-01-01", "1.5"],
        ["2026-01-02", ""],
      ],
      truncated: 0,
    });
  });

  it("caps rows and reports how many were left out", () => {
    const data = makeData({ x: ["a", "b", "c", "d"], y0: [1, 2, 3, 4] });
    const table = buildTable(data, 3);
    expect(table.rows).toHaveLength(3);
    expect(table.truncated).toBe(1);
  });
});
