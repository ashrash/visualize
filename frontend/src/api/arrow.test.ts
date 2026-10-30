import { tableFromArrays } from "apache-arrow";
import { describe, expect, it } from "vitest";
import { toColumns, toJsValue } from "./arrow";
import { formatValue } from "./format";

describe("arrow helpers", () => {
  it("converts BigInt and Date to plain values", () => {
    expect(toJsValue(42n)).toBe(42);
    expect(toJsValue(2n ** 60n)).toBe("1152921504606846976");
    expect(toJsValue(new Date(Date.UTC(2024, 0, 5)))).toBe(Date.UTC(2024, 0, 5));
  });

  it("turns a table into columns with kinds", () => {
    const table = tableFromArrays({
      region: ["North", "South"],
      units: new BigInt64Array([3n, 2n]),
      sales: new Float64Array([1.5, 2]),
    });
    const { columns, kinds, rowCount } = toColumns(table);
    expect(rowCount).toBe(2);
    expect(columns).toEqual({ region: ["North", "South"], units: [3, 2], sales: [1.5, 2] });
    expect(kinds).toEqual({ region: "string", units: "number", sales: "number" });
  });

  it("formats temporal values as dates", () => {
    expect(formatValue(Date.UTC(2024, 0, 5), "temporal")).toBe("2024-01-05");
    expect(formatValue(Date.UTC(2024, 0, 5, 13, 4, 5), "temporal")).toBe("2024-01-05 13:04:05");
    expect(formatValue(null, "number")).toBe("");
  });
});
