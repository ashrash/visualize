/**
 * Scaled scrolling for very long grids.
 *
 * Browsers clamp element heights (about 33.5M px in Chrome, 17.9M px in Firefox), so 10M rows at
 * 28 px (280M px) cannot be a real scroll height. The scroll area is capped at `MAX_SCROLL_PX`
 * and the scrollbar position is mapped linearly onto the row index; only the rows around the
 * mapped index are rendered, placed relative to the current scrollTop. Short datasets use the
 * identity mapping (scrollTop / rowHeight).
 */

export const MAX_SCROLL_PX = 8_000_000;

export interface ScrollWindow {
  /** Height of the scroll content in px (capped). */
  totalPx: number;
  /** True when the dataset is taller than the cap and positions are scaled. */
  scaled: boolean;
  /** First row to render and one past the last (includes overscan). */
  start: number;
  end: number;
  /** Row index at the top of the viewport. */
  firstVisible: number;
  /** Offset in px (inside the scroll content) of row `start`. */
  startTop: number;
}

export interface ScrollInput {
  scrollTop: number;
  viewportPx: number;
  totalRows: number;
  rowHeight: number;
  overscan?: number;
  maxPx?: number;
}

const clamp = (n: number, lo: number, hi: number) => Math.min(Math.max(n, lo), hi);

export function scrollWindow({
  scrollTop,
  viewportPx,
  totalRows,
  rowHeight,
  overscan = 20,
  maxPx = MAX_SCROLL_PX,
}: ScrollInput): ScrollWindow {
  const fullPx = totalRows * rowHeight;
  const totalPx = Math.min(fullPx, maxPx);
  const scaled = fullPx > maxPx;
  const visibleRows = Math.ceil(viewportPx / rowHeight);
  const maxScroll = Math.max(totalPx - viewportPx, 1);
  const top = clamp(scrollTop, 0, maxScroll);

  let firstVisible: number;
  if (!scaled) {
    firstVisible = Math.floor(top / rowHeight);
  } else {
    const fraction = top / maxScroll;
    firstVisible = Math.round(fraction * Math.max(totalRows - visibleRows, 0));
  }
  firstVisible = clamp(firstVisible, 0, Math.max(totalRows - 1, 0));
  const start = Math.max(firstVisible - overscan, 0);
  const end = Math.min(firstVisible + visibleRows + overscan, totalRows);
  // Unscaled rows sit at index * rowHeight; scaled rows hang off the current scrollTop so the
  // viewport always shows the mapped rows however many rows one pixel of scrolling skips.
  const startTop = scaled ? top + (start - firstVisible) * rowHeight : start * rowHeight;
  return { totalPx, scaled, start, end, firstVisible, startTop };
}

/** The scrollTop that brings `row` to the top of the viewport (inverse of the mapping). */
export function scrollTopForRow(
  row: number,
  { viewportPx, totalRows, rowHeight, maxPx = MAX_SCROLL_PX }: Omit<ScrollInput, "scrollTop">,
): number {
  const fullPx = totalRows * rowHeight;
  const totalPx = Math.min(fullPx, maxPx);
  const target = clamp(row, 0, Math.max(totalRows - 1, 0));
  if (fullPx <= maxPx) return clamp(target * rowHeight, 0, Math.max(totalPx - viewportPx, 0));
  const visibleRows = Math.ceil(viewportPx / rowHeight);
  const maxScroll = Math.max(totalPx - viewportPx, 1);
  const fraction = target / Math.max(totalRows - visibleRows, 1);
  return clamp(fraction * maxScroll, 0, maxScroll);
}
