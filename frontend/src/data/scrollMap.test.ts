import { describe, expect, it } from "vitest";
import { MAX_SCROLL_PX, scrollTopForRow, scrollWindow } from "./scrollMap";

const ROW = 28;
const VIEW = 600;
const input = (scrollTop: number, totalRows: number) => ({
  scrollTop,
  viewportPx: VIEW,
  totalRows,
  rowHeight: ROW,
});

describe("scrollWindow", () => {
  it("is the identity mapping for datasets that fit", () => {
    const w = scrollWindow(input(2800, 10_000));
    expect(w.scaled).toBe(false);
    expect(w.totalPx).toBe(280_000);
    expect(w.firstVisible).toBe(100);
    expect(w.start).toBe(80);
    expect(w.startTop).toBe(80 * ROW);
  });

  it("caps the scroll height at 10M rows instead of 280M px", () => {
    const w = scrollWindow(input(0, 10_000_000));
    expect(w.scaled).toBe(true);
    expect(w.totalPx).toBe(MAX_SCROLL_PX);
    expect(w.totalPx).toBeLessThan(17_000_000); // under Firefox's element height limit
  });

  it("reaches the first and last row of 10M rows", () => {
    const top = scrollWindow(input(0, 10_000_000));
    expect(top.firstVisible).toBe(0);
    expect(top.start).toBe(0);
    const bottom = scrollWindow(input(MAX_SCROLL_PX, 10_000_000));
    expect(bottom.end).toBe(10_000_000);
    expect(bottom.firstVisible).toBeGreaterThan(9_999_900);
  });

  it("maps the middle of the scrollbar to the middle of the data", () => {
    const w = scrollWindow(input((MAX_SCROLL_PX - VIEW) / 2, 10_000_000));
    expect(Math.abs(w.firstVisible - 5_000_000)).toBeLessThan(100);
  });

  it("is monotonic and renders only a small window", () => {
    let previous = -1;
    for (let top = 0; top <= MAX_SCROLL_PX; top += 123_457) {
      const w = scrollWindow(input(top, 10_000_000));
      expect(w.firstVisible).toBeGreaterThanOrEqual(previous);
      expect(w.end - w.start).toBeLessThan(100);
      previous = w.firstVisible;
    }
  });

  it("keeps the mapped rows inside the viewport", () => {
    const w = scrollWindow(input(3_000_000, 10_000_000));
    // The first visible row is drawn at scrollTop, i.e. at the top edge of the viewport.
    expect(w.startTop + (w.firstVisible - w.start) * ROW).toBe(3_000_000);
  });
});

describe("scrollTopForRow", () => {
  it("round-trips through scrollWindow within one pixel's worth of rows", () => {
    const rowsPerPx = 10_000_000 / MAX_SCROLL_PX;
    for (const row of [0, 1, 1234, 4_999_999, 9_000_000, 9_999_000]) {
      const top = scrollTopForRow(row, input(0, 10_000_000));
      const w = scrollWindow(input(top, 10_000_000));
      expect(Math.abs(w.firstVisible - row)).toBeLessThanOrEqual(Math.ceil(rowsPerPx) + 1);
    }
  });

  it("is exact for datasets that fit", () => {
    expect(scrollTopForRow(500, input(0, 10_000))).toBe(500 * ROW);
  });

  it("clamps out-of-range rows", () => {
    expect(scrollTopForRow(-5, input(0, 1000))).toBe(0);
    expect(scrollTopForRow(10_000_000_000, input(0, 1000))).toBe(1000 * ROW - VIEW);
  });
});
