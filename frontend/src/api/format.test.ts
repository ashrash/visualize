import { describe, expect, it } from "vitest";
import { formatValue } from "./format";

describe("formatValue", () => {
  it("prints integer columns without grouping", () => {
    expect(formatValue(2024, "number", "BIGINT")).toBe("2024");
    expect(formatValue(1500, "number", "INTEGER")).toBe("1500");
  });

  it("groups a DOUBLE column the same way whether or not the value is whole", () => {
    const whole = formatValue(1500, "number", "DOUBLE");
    const fractional = formatValue(1500.5, "number", "DOUBLE");
    // Same grouping separator after the thousands digit in both.
    expect(whole.replace(/\d/g, "")).not.toBe("");
    expect(whole.charAt(1)).toBe(fractional.charAt(1));
    expect(whole.charAt(1)).not.toMatch(/\d/);
  });

  it("falls back to the value when the type is unknown", () => {
    expect(formatValue(2024, "number")).toBe("2024");
  });

  it("formats temporal values as dates", () => {
    expect(formatValue(Date.UTC(2024, 5, 14), "temporal", "DATE")).toBe("2024-06-14");
  });
});
