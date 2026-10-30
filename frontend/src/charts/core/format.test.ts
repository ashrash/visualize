import { describe, expect, it } from "vitest";
import { FORMAT_PRESETS, formatDateAuto, isDateFormat, makeFormatter } from "./format";

const en = (format: string | null) => makeFormatter(format, "en-US");

describe("makeFormatter", () => {
  it("formats numbers with fixed decimals and grouping", () => {
    expect(en("number:0")(1234.5)).toBe("1,235");
    expect(en("number:2")(1234.5)).toBe("1,234.50");
  });

  it("formats compact numbers", () => {
    expect(en("compact:1")(1234)).toBe("1.2K");
    expect(en("compact:1")(3_400_000)).toBe("3.4M");
  });

  it("treats percent as a fraction and percent100 as already scaled", () => {
    expect(en("percent:1")(0.125)).toBe("12.5%");
    expect(en("percent100:0")(12)).toBe("12%");
  });

  it("formats currency and defaults to USD", () => {
    expect(en("currency:USD:2")(1234.5)).toBe("$1,234.50");
    expect(en("currency:EUR:0")(1234.5)).toBe("€1,235");
    expect(en("currency")(5)).toBe("$5.00");
  });

  it("formats dates from epoch ms in UTC", () => {
    const ms = Date.UTC(2026, 9, 30, 14, 5);
    expect(en("date")(ms)).toBe("2026-10-30");
    expect(en("datetime")(ms)).toBe("2026-10-30 14:05");
    expect(en("time")(ms)).toBe("14:05");
    expect(en("year")(ms)).toBe("2026");
    expect(en("quarter")(ms)).toBe("2026 Q4");
    expect(en("month")(ms)).toBe("Oct 2026");
  });

  it("falls back to auto for unknown formats and handles empties", () => {
    expect(en("bogus")(1234.5678)).toBe("1,234.5678");
    expect(en(null)(null)).toBe("");
    expect(en("number:2")("abc")).toBe("abc");
    expect(en("date")("not a date")).toBe("");
  });
});

describe("date helpers", () => {
  it("drops the time of day at midnight", () => {
    expect(formatDateAuto(Date.UTC(2026, 0, 2))).toBe("2026-01-02");
    expect(formatDateAuto(Date.UTC(2026, 0, 2, 3, 4))).toBe("2026-01-02 03:04");
  });

  it("recognizes date formats and offers presets for both kinds", () => {
    expect(isDateFormat("month")).toBe(true);
    expect(isDateFormat("number:2")).toBe(false);
    expect(FORMAT_PRESETS.some((p) => p.group === "date")).toBe(true);
    for (const preset of FORMAT_PRESETS) expect(() => makeFormatter(preset.value)).not.toThrow();
  });
});
