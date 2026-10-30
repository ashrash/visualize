import { expect, test } from "./fixtures";
import { chartImage, chartRegion, createChart, luminance } from "./helpers/charts";
import { sample } from "./helpers/env";
import { shot, unique } from "./helpers/shots";

const pageBackground = (page: import("@playwright/test").Page) =>
  page.evaluate(() => getComputedStyle(document.body).backgroundColor);

test("light and dark themes switch the app and the chart, and the choice persists", async ({
  page,
  app,
  api,
}) => {
  const name = unique("sales");
  await api.importPath(sample("sales.csv"), { name });
  await app.open("charts");
  await app.selectDataset(name);
  await createChart(page);

  // Light
  await app.setTheme("light");
  await expect.poll(() => app.themeAttribute()).toBe("light");
  expect(luminance(await pageBackground(page))).toBeGreaterThan(0.8);
  await expect(chartImage(page)).toBeVisible();
  // Let ECharts finish its theme switch before taking the reference picture.
  await page.waitForTimeout(600);
  const light = await chartRegion(page).screenshot();
  await shot(page, "theme-1-light");

  // Dark
  await app.setTheme("dark");
  await expect.poll(() => app.themeAttribute()).toBe("dark");
  await expect.poll(async () => luminance(await pageBackground(page))).toBeLessThan(0.3);
  await expect(chartImage(page)).toBeVisible();
  // The canvas is repainted with the dark palette: pixels differ from the light picture.
  await expect
    .poll(async () => (await chartRegion(page).screenshot()).equals(light), { timeout: 8000 })
    .toBe(false);
  await shot(page, "theme-2-dark");

  // The choice survives a reload (stored in localStorage by the app).
  await app.waitForSaved(); // the chart must be saved before the reload, or it is lost
  await app.reload();
  await app.goTo("charts");
  await expect(app.themeSelect).toHaveValue("dark");
  expect(await app.themeAttribute()).toBe("dark");
  await expect(chartImage(page)).toBeVisible();
  await shot(page, "theme-3-dark-after-reload");

  // Auto follows the operating system's colour scheme.
  await app.setTheme("auto");
  await expect.poll(() => app.themeAttribute()).toBeNull();
  await page.emulateMedia({ colorScheme: "dark" });
  await expect.poll(async () => luminance(await pageBackground(page))).toBeLessThan(0.3);
  await page.emulateMedia({ colorScheme: "light" });
  await expect.poll(async () => luminance(await pageBackground(page))).toBeGreaterThan(0.8);

  await api.deleteDatasetByName(name);
});
