import { expect, type Locator, type Page } from "@playwright/test";

/**
 * Chart screen helpers. ECharts draws its text on a canvas, so nothing here looks for axis labels:
 * a chart is "rendered" when its accessible image exists and the data badge/footer is showing, and
 * a config change is verified through the chart's aria-label, the config panel's form controls and
 * the saved project document.
 */
export type ConfigTab = "Data" | "Chart" | "Style";

export const chartRegion = (page: Page): Locator =>
  page.getByRole("region", { name: "Chart", exact: true });

/** The chart's accessible image (aria-label = title, or "<Plot>: <fields>" by default). */
export const chartImage = (page: Page): Locator => chartRegion(page).getByRole("img");

export const configPanel = (page: Page): Locator =>
  page.getByRole("complementary", { name: "Chart settings" });

export async function openConfigTab(page: Page, tab: ConfigTab): Promise<void> {
  await configPanel(page).getByRole("tab", { name: tab }).click();
  await expect(configPanel(page).getByRole("tabpanel", { name: tab })).toBeVisible();
}

/** Press "New chart" and wait for the first render (accessible image and the data footer). */
export async function createChart(page: Page): Promise<void> {
  await page.getByRole("button", { name: "New chart" }).click();
  await expectChartRendered(page);
}

export async function expectChartRendered(page: Page): Promise<void> {
  await expect(chartImage(page)).toBeVisible();
  await expect(
    chartRegion(page)
      .getByText(/Aggregated from [\d,]+ rows|[\d,]+ rows ·/)
      .first(),
  ).toBeVisible();
}

/** Set the chart title on the Style tab (the title becomes the chart image's accessible name). */
export async function setChartTitle(page: Page, title: string): Promise<void> {
  await openConfigTab(page, "Style");
  await configPanel(page).getByRole("textbox", { name: "Title", exact: true }).fill(title);
}

/** Choose how the first value is aggregated (Data tab). */
export async function setValueAggregate(page: Page, label: string): Promise<void> {
  await openConfigTab(page, "Data");
  await configPanel(page)
    .getByRole("combobox", { name: "Value 1: aggregate", exact: true })
    .selectOption({ label });
}

/** A CSS colour string to relative luminance 0..1 (for light/dark assertions). */
export function luminance(cssColor: string): number {
  const match = cssColor.match(/rgba?\((\d+)[,\s]+(\d+)[,\s]+(\d+)/);
  if (!match) throw new Error(`cannot parse colour: ${cssColor}`);
  const [r = 0, g = 0, b = 0] = match.slice(1, 4).map((v) => Number(v) / 255);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
