import { readFileSync } from "node:fs";
import type { Download, Page } from "@playwright/test";
import { expect, test } from "./fixtures";
import { chartRegion, createChart, setChartTitle } from "./helpers/charts";
import { sample } from "./helpers/env";
import { unique } from "./helpers/shots";

async function exportAs(page: Page, item: string): Promise<{ download: Download; bytes: Buffer }> {
  const pending = page.waitForEvent("download");
  await chartRegion(page).getByRole("button", { name: "Export" }).click();
  await page.getByRole("menuitem", { name: item }).click();
  const download = await pending;
  const file = await download.path();
  return { download, bytes: readFileSync(file) };
}

test.describe("chart export", () => {
  let dataset = "";

  test.beforeEach(async ({ page, app, api }) => {
    dataset = unique("sales");
    await api.importPath(sample("sales.csv"), { name: dataset });
    await app.open("charts");
    await app.selectDataset(dataset);
    await createChart(page);
    await setChartTitle(page, "Units by region");
  });

  test.afterEach(async ({ api }) => {
    await api.deleteDatasetByName(dataset);
  });

  test("PNG download is a real image with chart dimensions", async ({ page }) => {
    const { download, bytes } = await exportAs(page, "PNG image");
    expect(download.suggestedFilename()).toMatch(/\.png$/i);
    // PNG signature, then the IHDR chunk holds width and height.
    expect([...bytes.subarray(0, 8)]).toEqual([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
    const width = bytes.readUInt32BE(16);
    const height = bytes.readUInt32BE(20);
    expect(width).toBeGreaterThan(300);
    expect(height).toBeGreaterThan(200);
    expect(bytes.length).toBeGreaterThan(5_000);
    await expect(page.getByRole("status").filter({ hasText: "Exported" })).toBeAttached();
  });

  test("SVG download is vector markup with drawn shapes", async ({ page }) => {
    const { download, bytes } = await exportAs(page, "SVG (vector)");
    expect(download.suggestedFilename()).toMatch(/\.svg$/i);
    const text = bytes.toString("utf8");
    expect(text).toMatch(/^(<\?xml[^>]*>\s*)?<svg[\s>]/);
    expect(text).toContain("</svg>");
    // Bars and axes are drawn as shapes; a bare empty <svg> would have none.
    expect((text.match(/<(path|rect)\b/g) ?? []).length).toBeGreaterThan(4);
  });

  test("JPEG download has the JPEG signature", async ({ page }) => {
    const { download, bytes } = await exportAs(page, "JPEG image");
    expect(download.suggestedFilename()).toMatch(/\.jpe?g$/i);
    expect([...bytes.subarray(0, 3)]).toEqual([0xff, 0xd8, 0xff]);
  });

  test("PDF download is a PDF document", async ({ page }) => {
    const { download, bytes } = await exportAs(page, "PDF");
    expect(download.suggestedFilename()).toMatch(/\.pdf$/i);
    expect(bytes.subarray(0, 5).toString("latin1")).toBe("%PDF-");
  });

  test("export is unavailable while the chart is shown as a table", async ({ page }) => {
    await chartRegion(page).getByRole("button", { name: "View as table" }).click();
    await expect(chartRegion(page).getByRole("button", { name: "Export" })).toBeDisabled();
    await expect(chartRegion(page).getByRole("table")).toBeVisible();
    await expect(chartRegion(page).getByRole("columnheader").first()).toBeVisible();
  });
});
