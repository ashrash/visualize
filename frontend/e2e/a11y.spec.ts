import { expect, test } from "./fixtures";
import { criticalOf, runAxe } from "./helpers/axe";
import { createChart, openConfigTab } from "./helpers/charts";
import { sample } from "./helpers/env";
import { unique } from "./helpers/shots";

/**
 * axe (WCAG 2.1 A/AA) on the main screens in both themes. Existing violations are reported, not
 * failed on: only `critical` impact fails a run. The per-screen results are merged into
 * frontend/test-results/axe-report.{json,md} (grouped by owning agent) by global-teardown.
 */
for (const theme of ["light", "dark"] as const) {
  test.describe(`axe: ${theme} theme`, () => {
    let dataset = "";

    test.beforeEach(async ({ app, api }) => {
      dataset = unique("a11y");
      await api.importPath(sample("sales.csv"), { name: dataset });
      await app.open("data");
      await app.selectDataset(dataset);
      await app.setTheme(theme);
      await expect.poll(() => app.themeAttribute()).toBe(theme);
    });

    test.afterEach(async ({ api }) => {
      await api.deleteDatasetByName(dataset);
    });

    test("data screen with the grid", async ({ page }, testInfo) => {
      await expect(page.getByRole("table")).toBeVisible();
      await expect(page.getByRole("row", { name: /2024-06-14/ }).first()).toBeVisible();
      const report = await runAxe(page, "data-grid", theme);
      await testInfo.attach("axe", {
        body: JSON.stringify(report, null, 2),
        contentType: "application/json",
      });
      expect(
        criticalOf(report).map((v) => v.id),
        "critical axe violations",
      ).toEqual([]);
    });

    test("import dialog", async ({ page }, testInfo) => {
      await page.getByRole("button", { name: "Import data" }).click();
      await expect(page.getByRole("dialog", { name: "Import data" })).toBeVisible();
      const report = await runAxe(page, "import-dialog", theme);
      await testInfo.attach("axe", {
        body: JSON.stringify(report, null, 2),
        contentType: "application/json",
      });
      expect(
        criticalOf(report).map((v) => v.id),
        "critical axe violations",
      ).toEqual([]);
    });

    test("charts screen with the config panel (Data tab)", async ({ page, app }, testInfo) => {
      await app.goTo("charts");
      await createChart(page);
      await openConfigTab(page, "Data");
      await page.waitForTimeout(500); // let the chart finish its entry animation
      const report = await runAxe(page, "charts-config-data", theme);
      await testInfo.attach("axe", {
        body: JSON.stringify(report, null, 2),
        contentType: "application/json",
      });
      expect(
        criticalOf(report).map((v) => v.id),
        "critical axe violations",
      ).toEqual([]);
    });

    test("charts screen with the Style tab and export menu", async ({ page, app }, testInfo) => {
      await app.goTo("charts");
      await createChart(page);
      await openConfigTab(page, "Style");
      await page.getByRole("button", { name: "Export" }).click();
      await expect(page.getByRole("menu", { name: "Export chart" })).toBeVisible();
      await page.waitForTimeout(500);
      const report = await runAxe(page, "charts-style-export-menu", theme);
      await testInfo.attach("axe", {
        body: JSON.stringify(report, null, 2),
        contentType: "application/json",
      });
      expect(
        criticalOf(report).map((v) => v.id),
        "critical axe violations",
      ).toEqual([]);
    });

    test("project menu", async ({ page, app }, testInfo) => {
      await app.openProjectMenu();
      const report = await runAxe(page, "project-menu", theme);
      await testInfo.attach("axe", {
        body: JSON.stringify(report, null, 2),
        contentType: "application/json",
      });
      expect(
        criticalOf(report).map((v) => v.id),
        "critical axe violations",
      ).toEqual([]);
    });
  });
}
