import { expect, test } from "./fixtures";
import { chartImage, createChart, setChartTitle } from "./helpers/charts";
import { sample } from "./helpers/env";
import { shot, unique } from "./helpers/shots";

// Edits closer together than 800 ms are one undo step (so typing isn't 100 steps). Waiting a
// little longer between test edits makes each one its own step.
const PAUSE_MS = 1000;

test("undo and redo a chart edit (buttons and keyboard)", async ({ page, app, api }) => {
  const name = unique("sales");
  await api.importPath(sample("sales.csv"), { name });
  await app.open("charts");
  await app.selectDataset(name);
  await createChart(page);
  const original = /Bar chart: region, Sum of units/;
  await expect(chartImage(page)).toHaveAccessibleName(original);

  await page.waitForTimeout(PAUSE_MS);
  await setChartTitle(page, "First title");
  await expect(chartImage(page)).toHaveAccessibleName("First title");
  await page.waitForTimeout(PAUSE_MS);
  await setChartTitle(page, "Second title");
  await expect(chartImage(page)).toHaveAccessibleName("Second title");
  await shot(page, "undo-redo-1-edited");

  // Undo twice with the buttons, then redo twice.
  await expect(app.redoButton).toBeDisabled();
  await app.undoButton.click();
  await expect(chartImage(page)).toHaveAccessibleName("First title");
  await expect(app.redoButton).toBeEnabled();
  await app.undoButton.click();
  await expect(chartImage(page)).toHaveAccessibleName(original);
  await app.redoButton.click();
  await expect(chartImage(page)).toHaveAccessibleName("First title");
  await app.redoButton.click();
  await expect(chartImage(page)).toHaveAccessibleName("Second title");
  await expect(app.redoButton).toBeDisabled();

  // Keyboard shortcuts work when focus is not in a text field.
  await page.getByRole("heading", { name: "Visualize" }).click();
  await page.keyboard.press("ControlOrMeta+z");
  await expect(chartImage(page)).toHaveAccessibleName("First title");
  await page.keyboard.press("ControlOrMeta+Shift+z");
  await expect(chartImage(page)).toHaveAccessibleName("Second title");

  // A new edit after an undo throws the redo stack away.
  await app.undoButton.click();
  await expect(chartImage(page)).toHaveAccessibleName("First title");
  await page.waitForTimeout(PAUSE_MS);
  await setChartTitle(page, "Third title");
  await expect(chartImage(page)).toHaveAccessibleName("Third title");
  await expect(app.redoButton).toBeDisabled();
  await shot(page, "undo-redo-2-after-new-edit");

  await api.deleteDatasetByName(name);
});

test("undo also reverts the chart creation itself", async ({ page, app, api }) => {
  const name = unique("sales");
  await api.importPath(sample("sales.csv"), { name });
  await app.open("charts");
  await app.selectDataset(name);
  await createChart(page);
  await app.undoButton.click();
  await expect(page.getByText("No charts yet")).toBeVisible();
  await app.redoButton.click();
  await expect(chartImage(page)).toBeVisible();
});
