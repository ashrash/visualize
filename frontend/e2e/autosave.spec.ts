import { expect, test } from "./fixtures";
import { chartImage, createChart, setChartTitle } from "./helpers/charts";
import { sample } from "./helpers/env";
import { shot, unique } from "./helpers/shots";

test("an edit autosaves after a pause (no Save press) and survives a reload", async ({
  page,
  app,
  api,
  project,
}) => {
  const name = unique("sales");
  await api.importPath(sample("sales.csv"), { name });
  const before = await api.project(project.id);

  await app.open("charts");
  await app.selectDataset(name);
  await createChart(page);
  // The edit makes the project dirty; autosave fires after a 2 s debounce.
  await setChartTitle(page, "Autosaved title");
  await expect(app.saveStatus).toHaveAttribute("data-status", /^(unsaved|saving)$/);
  await app.waitForSaved();
  await shot(page, "autosave-1-saved");

  const saved = await api.project(project.id);
  expect(saved.revision).toBeGreaterThan(before.revision);
  expect(saved.charts?.[0]?.style?.title).toBe("Autosaved title");

  await app.reload();
  await app.goTo("charts");
  await expect(chartImage(page)).toHaveAccessibleName("Autosaved title");
  // Nothing is pending after a clean load.
  await expect(app.saveStatus).toHaveAttribute("data-status", "saved");

  await api.deleteDatasetByName(name);
});

test("Save writes immediately and records a snapshot", async ({ page, app, api, project }) => {
  const name = unique("sales");
  await api.importPath(sample("sales.csv"), { name });
  await app.open("charts");
  await app.selectDataset(name);
  await createChart(page);
  await setChartTitle(page, "Manually saved");
  await app.save();
  const versions = await api.versions(project.id);
  expect(versions.length).toBeGreaterThan(0);
  expect((await api.project(project.id)).charts?.[0]?.style?.title).toBe("Manually saved");
});
