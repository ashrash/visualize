import { readFileSync } from "node:fs";
import { expect, test } from "./fixtures";
import { sample } from "./helpers/env";
import { shot, unique } from "./helpers/shots";

// Rows of samples/sales.csv: order_id,order_date,region,product,channel,units,unit_price,sales
const SALES = readFileSync(sample("sales.csv"), "utf8")
  .split(/\r?\n/)
  .filter(Boolean)
  .slice(1)
  .map((line) => line.split(","));
const SOUTH = SALES.filter((row) => row[2] === "South").length;

test.describe("data grid (server-side sort, filter, columns, export)", () => {
  let name = "";

  test.beforeEach(async ({ app, api }) => {
    name = unique("sales");
    await api.importPath(sample("sales.csv"), { name });
    await app.open("data");
    await app.selectDataset(name);
  });

  test.afterEach(async ({ api }) => {
    await api.deleteDatasetByName(name);
  });

  test("sort from a column header: ascending, descending, cleared", async ({ page }) => {
    const grid = page.getByRole("region", { name: `${name} data grid` });
    const sort = grid.getByRole("button", { name: "Sort by region", exact: true });
    const header = grid.getByRole("columnheader", { name: /^region/ });
    // sales.csv regions: East, North, South, West.
    const firstRegion = () => grid.getByRole("row").nth(1).getByRole("cell").nth(3);

    await sort.click();
    await expect(header).toHaveAttribute("aria-sort", "ascending");
    await expect(firstRegion()).toHaveText("East");
    await sort.click();
    await expect(header).toHaveAttribute("aria-sort", "descending");
    await expect(firstRegion()).toHaveText("West");
    await sort.click();
    await expect(header).not.toHaveAttribute("aria-sort", /.+/);
    await shot(page, "grid-1-sorted-cleared");
  });

  test("filter by values: rows, count chip and CSV export follow the filter", async ({ page }) => {
    const grid = page.getByRole("region", { name: `${name} data grid` });
    await expect(page.getByText("500 rows", { exact: true })).toBeVisible();

    await grid.getByRole("button", { name: "Filter region", exact: true }).click();
    await page.getByRole("checkbox", { name: /^South/ }).check();
    await page.getByRole("button", { name: "Apply" }).click();

    await expect(page.getByText(`${SOUTH} of 500 rows`)).toBeVisible();
    await expect(page.getByRole("table", { name: new RegExp(`${SOUTH} rows`) })).toHaveAttribute(
      "aria-rowcount",
      String(SOUTH + 1),
    );
    // Every row on screen is a South row.
    const regions = grid.getByRole("row").filter({ hasText: /^\d/ }).getByRole("cell").nth(3);
    await expect(regions.first()).toHaveText("South");
    await expect(grid.getByRole("row").filter({ hasText: " North " })).toHaveCount(0);
    await shot(page, "grid-2-filtered");

    // Export the filtered view as CSV: header + only the South rows.
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "Export", exact: true }).click();
    await page.getByRole("menuitem", { name: "Export CSV" }).click();
    const file = await download;
    expect(file.suggestedFilename()).toMatch(/\.csv$/i);
    const lines = readFileSync(await file.path(), "utf8")
      .split(/\r?\n/)
      .filter(Boolean);
    expect(lines[0]).toBe("order_id,order_date,region,product,channel,units,unit_price,sales");
    expect(lines).toHaveLength(SOUTH + 1);
    expect(lines.slice(1).every((line) => line.split(",")[2] === "South")).toBe(true);

    // Removing the filter chip restores every row.
    await page.getByRole("button", { name: "Remove filter on region" }).click();
    await expect(page.getByText("500 rows", { exact: true })).toBeVisible();
  });

  test("hide and show a column from the Columns menu", async ({ page }) => {
    const grid = page.getByRole("region", { name: `${name} data grid` });
    const header = grid.getByRole("columnheader", { name: /^unit_price/ });
    await expect(header).toBeVisible();
    await page.getByRole("button", { name: "Columns", exact: true }).click();
    await page.getByRole("menuitemcheckbox", { name: "unit_price" }).click();
    await page.keyboard.press("Escape");
    await expect(header).toHaveCount(0);
    await page.getByRole("button", { name: "Columns", exact: true }).click();
    await page.getByRole("menuitemcheckbox", { name: "unit_price" }).click();
    await page.keyboard.press("Escape");
    await expect(header).toBeVisible();
  });
});
