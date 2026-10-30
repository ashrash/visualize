import type { Locator, Page } from "@playwright/test";
import { expect, test } from "./fixtures";
import { LARGE_ROWS, largeCsv, largeFixtureExists } from "./helpers/env";
import { importDatasetViaUi } from "./helpers/importDataset";
import { shot, unique } from "./helpers/shots";

/**
 * The scaled grid at 10M rows (a scrollbar can't be 280M px tall, so the UI maps scroll position
 * onto the row index and moves by whole rows for wheel and keys). Opt-in:
 *
 *   uv run python scripts/gen_large_dataset.py --rows 10000000
 *   E2E_LARGE=1 npm --prefix frontend run e2e -- --grep @large
 *
 * scripts/gen_large_dataset.py makes `order_id` equal the 1-based row number, so every visible row
 * can be checked for consistency: the "#" column, the aria-rowindex and the order_id cell agree.
 */
test.skip(!largeFixtureExists(), "needs data/perf/large_<rows>.csv (scripts/gen_large_dataset.py)");

interface VisibleRows {
  /** Row numbers (1-based, from the "#" column) that are fully inside the scroll viewport. */
  numbers: number[];
  /** Same rows, taken from the order_id column. */
  orderIds: number[];
  /** Same rows, from aria-rowindex minus the header row. */
  ariaNumbers: number[];
  /** Rows per "page" the keyboard moves by (same formula as the grid). */
  pageRows: number;
  /** Rows whose data has not arrived yet (empty cells). */
  loading: number;
}

const toNumber = (text: string): number => Number(text.replace(/[,\s]/g, ""));

async function readVisibleRows(grid: Locator): Promise<VisibleRows> {
  const raw = await grid.evaluate((scroller) => {
    const box = scroller.getBoundingClientRect();
    const header = scroller.querySelector('[role="rowgroup"]');
    const top = (header?.getBoundingClientRect().bottom ?? box.top) - 0.5;
    const rows = [...scroller.querySelectorAll('[role="row"][aria-rowindex]')].filter((row) => {
      const index = Number(row.getAttribute("aria-rowindex"));
      if (index < 2) return false; // header
      const r = row.getBoundingClientRect();
      return r.top >= top && r.bottom <= box.bottom + 0.5;
    });
    return {
      rows: rows.map((row) => {
        const cells = [...row.querySelectorAll('[role="cell"]')].map((c) => c.textContent ?? "");
        return {
          aria: Number(row.getAttribute("aria-rowindex")),
          number: cells[0] ?? "",
          orderId: cells[1] ?? "",
        };
      }),
      pageRows: Math.max(Math.floor(scroller.clientHeight / 28) - 2, 1),
    };
  });
  const rows = [...raw.rows].sort((a, b) => a.aria - b.aria);
  return {
    numbers: rows.map((r) => toNumber(r.number)),
    orderIds: rows.map((r) => (r.orderId === "" ? Number.NaN : toNumber(r.orderId))),
    ariaNumbers: rows.map((r) => r.aria - 1),
    pageRows: raw.pageRows,
    loading: rows.filter((r) => r.orderId === "").length,
  };
}

/** Wait for the visible rows to have data, then check they are consecutive and consistent. */
async function settledRows(grid: Locator): Promise<VisibleRows & { first: number; last: number }> {
  let rows = await readVisibleRows(grid);
  await expect
    .poll(
      async () => {
        rows = await readVisibleRows(grid);
        return rows.numbers.length > 5 && rows.loading === 0;
      },
      { timeout: 15_000, message: "visible rows should load" },
    )
    .toBe(true);
  const first = rows.numbers[0] ?? 0;
  const last = rows.numbers.at(-1) ?? 0;
  // Row numbers shown: consecutive, identical to aria-rowindex, and equal to order_id.
  expect(rows.numbers).toEqual(Array.from({ length: rows.numbers.length }, (_, i) => first + i));
  expect(rows.ariaNumbers).toEqual(rows.numbers);
  expect(rows.orderIds).toEqual(rows.numbers);
  return { ...rows, first, last };
}

async function openLargeGrid(page: Page, name: string): Promise<Locator> {
  await expect(page.getByRole("region", { name: `${name} data grid` })).toBeVisible();
  return page.getByRole("region", { name: `${name} data grid` });
}

test.describe("scaled grid @large", () => {
  test.setTimeout(300_000);
  let name = "";

  test("import by path, then wheel, keyboard, go-to-row and End on 10M rows", async ({
    page,
    app,
    api,
  }) => {
    name = unique("large");
    const total = LARGE_ROWS;
    await app.open("data");

    // -- import the big CSV in place through the "Local path" tab ------------------------------
    const started = Date.now();
    await importDatasetViaUi(page, { path: largeCsv() }, { name, timeoutMs: 240_000 });
    console.log(`import via UI took ${((Date.now() - started) / 1000).toFixed(1)} s`);
    await app.selectDataset(name);
    await expect(app.datasetButton(new RegExp(`^${name}`))).toContainText(
      `${total.toLocaleString("en-US")} rows`,
    );
    const grid = await openLargeGrid(page, name);
    await expect(
      page.getByRole("table", { name: `${name}: ${total.toLocaleString("en-US")} rows` }),
    ).toBeVisible();
    await expect(page.getByRole("table")).toHaveAttribute("aria-rowcount", String(total + 1));

    // -- the top of the data ---------------------------------------------------------------------
    let view = await settledRows(grid);
    expect(view.first).toBe(1);
    await shot(page, "large-grid-1-top");

    // -- wheel: one notch of 28 px is one row; 2800 px is exactly 100 rows ---------------------
    await grid.hover();
    await page.mouse.wheel(0, 2800);
    await expect.poll(async () => (await readVisibleRows(grid)).numbers[0]).toBe(101);
    view = await settledRows(grid);
    expect(view.first).toBe(101);
    await page.mouse.wheel(0, -1400); // back up 50 rows
    await expect.poll(async () => (await readVisibleRows(grid)).numbers[0]).toBe(51);
    await settledRows(grid);

    // -- keyboard paging --------------------------------------------------------------------------
    await grid.focus();
    const { pageRows } = await readVisibleRows(grid);
    await page.keyboard.press("PageDown");
    await expect.poll(async () => (await readVisibleRows(grid)).numbers[0]).toBe(51 + pageRows);
    await page.keyboard.press("ArrowDown");
    await expect.poll(async () => (await readVisibleRows(grid)).numbers[0]).toBe(52 + pageRows);
    await page.keyboard.press("ArrowUp");
    await page.keyboard.press("PageUp");
    await expect.poll(async () => (await readVisibleRows(grid)).numbers[0]).toBe(51);
    view = await settledRows(grid);
    expect(view.first).toBe(51);

    // -- go to row --------------------------------------------------------------------------------
    await page.getByRole("spinbutton", { name: "Go to row" }).fill("5000000");
    await page.getByRole("button", { name: "Go", exact: true }).click();
    await expect.poll(async () => (await readVisibleRows(grid)).numbers[0]).toBe(5_000_000);
    view = await settledRows(grid);
    expect(view.first).toBe(5_000_000);
    await shot(page, "large-grid-2-row-5M");

    // -- drag the scrollbar to the middle: position maps proportionally onto the rows -----------
    await grid.evaluate((el) => {
      el.scrollTop = (el.scrollHeight - el.clientHeight) / 2;
    });
    view = await settledRows(grid);
    expect(view.first).toBeGreaterThan(total * 0.48);
    expect(view.first).toBeLessThan(total * 0.52);

    // -- End jumps to the very last row, Home back to the first ----------------------------------
    await grid.focus();
    await page.keyboard.press("End");
    await expect.poll(async () => (await readVisibleRows(grid)).numbers.at(-1)).toBe(total);
    view = await settledRows(grid);
    expect(view.last).toBe(total);
    expect(view.ariaNumbers.at(-1)).toBe(total);
    expect(view.orderIds.at(-1)).toBe(total);
    await shot(page, "large-grid-3-end");
    // Scrolling further down stays put (clamped, no blank space or wrap-around).
    await page.mouse.wheel(0, 5000);
    await page.keyboard.press("ArrowDown");
    view = await settledRows(grid);
    expect(view.last).toBe(total);

    await page.keyboard.press("Home");
    await expect.poll(async () => (await readVisibleRows(grid)).numbers[0]).toBe(1);
    await settledRows(grid);

    // -- the last row by go-to-row too -----------------------------------------------------------
    await page.getByRole("spinbutton", { name: "Go to row" }).fill(String(total));
    await page.getByRole("button", { name: "Go", exact: true }).click();
    await expect.poll(async () => (await readVisibleRows(grid)).numbers.at(-1)).toBe(total);
    await settledRows(grid);

    await api.deleteDatasetByName(name);
  });
});
