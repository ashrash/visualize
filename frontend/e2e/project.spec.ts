import { expect, test } from "./fixtures";
import { chartImage, createChart, setChartTitle } from "./helpers/charts";
import { sample } from "./helpers/env";
import { shot, unique } from "./helpers/shots";

test.describe("project menu", () => {
  test("rename a project", async ({ page, app, api, project }) => {
    await app.open("data");
    await expect(app.projectName).toContainText(project.name);

    await app.openProjectMenu();
    await app.menuItem(/^Rename/).click();
    const dialog = page.getByRole("dialog", { name: "Rename project" });
    await expect(dialog.getByRole("textbox", { name: "Name" })).toHaveValue(project.name);
    const renamed = unique("Renamed project");
    await dialog.getByRole("textbox", { name: "Name" }).fill(renamed);
    await dialog.getByRole("button", { name: "Rename", exact: true }).click();
    await expect(dialog).toBeHidden();
    await expect(app.projectName).toContainText(renamed);
    await shot(page, "project-1-renamed");

    // Stored on the server, and still there after a reload.
    expect((await api.project(project.id)).name).toBe(renamed);
    await app.reload();
    await expect(app.projectName).toContainText(renamed);
    await expect(app.saveStatus).toHaveAttribute("data-status", "saved");
  });

  test("rename needs a non-empty name", async ({ page, app }) => {
    await app.open("data");
    await app.openProjectMenu();
    await app.menuItem(/^Rename/).click();
    const dialog = page.getByRole("dialog", { name: "Rename project" });
    await dialog.getByRole("textbox", { name: "Name" }).fill("   ");
    await expect(dialog.getByRole("button", { name: "Rename", exact: true })).toBeDisabled();
  });

  test("duplicate a project: the copy opens with the same charts, the original is untouched", async ({
    page,
    app,
    api,
    project,
  }) => {
    const dataset = unique("sales");
    await api.importPath(sample("sales.csv"), { name: dataset });
    await app.open("charts");
    await app.selectDataset(dataset);
    await createChart(page);
    await setChartTitle(page, "Original title");
    await app.save();
    const original = await api.project(project.id);
    expect(original.charts).toHaveLength(1);

    await app.openProjectMenu();
    await app.menuItem(/^Duplicate/).click();

    // The app switches to the copy (the open project is remembered in localStorage).
    await expect
      .poll(() => page.evaluate(() => localStorage.getItem("viz.lastProject")), { timeout: 15_000 })
      .not.toBe(project.id);
    const copyId = await page.evaluate(() => localStorage.getItem("viz.lastProject"));
    await expect(app.saveStatus).toHaveAttribute("data-status", "saved");
    await shot(page, "project-2-duplicate-opened");

    const copy = await api.project(copyId ?? "");
    expect(copy.name).not.toBe(original.name);
    expect(copy.charts).toHaveLength(1);
    expect(copy.charts?.[0]?.style?.title).toBe("Original title");
    expect(copy.datasetIds).toEqual(original.datasetIds);

    // Editing the copy must not change the original.
    await app.goTo("charts");
    await expect(chartImage(page)).toHaveAccessibleName("Original title");
    await setChartTitle(page, "Copy title");
    await app.save();
    expect((await api.project(copyId ?? "")).charts?.[0]?.style?.title).toBe("Copy title");
    expect((await api.project(project.id)).charts?.[0]?.style?.title).toBe("Original title");

    // Switching back through the project picker works.
    await app.openProjectMenu();
    await app.menuItem("Open or create project…").click();
    const picker = page.getByRole("dialog", { name: "Projects" });
    // A row's name also holds its timestamp, so match the start; "(?! copy)" skips the duplicate.
    const escaped = project.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    await picker.getByRole("button", { name: new RegExp(`^${escaped}(?! copy)`) }).click();
    await expect
      .poll(() => page.evaluate(() => localStorage.getItem("viz.lastProject")))
      .toBe(project.id);
    await expect(app.projectName).toHaveText(project.name);

    await api.deleteProject(copyId ?? "");
    await api.deleteDatasetByName(dataset);
  });

  test("create a new project from the picker", async ({ page, app, api }) => {
    await app.open("data");
    await app.openProjectMenu();
    await app.menuItem("Open or create project…").click();
    const picker = page.getByRole("dialog", { name: "Projects" });
    const name = unique("Brand new");
    await picker.getByRole("textbox", { name: "New project name" }).fill(name);
    await picker.getByRole("button", { name: "Create", exact: true }).click();
    await expect(app.projectName).toContainText(name);
    const created = (await api.projects()).find((p) => p.name === name);
    expect(created).toBeDefined();
    await api.deleteProject(created?.id ?? "");
  });
});
