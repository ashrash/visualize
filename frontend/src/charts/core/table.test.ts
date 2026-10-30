import { describe, expect, it } from "vitest";
import { buildTable } from "./table";
import { makeData } from "./testing";

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
