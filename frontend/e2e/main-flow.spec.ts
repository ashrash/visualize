import { expect, test } from "./fixtures";
import {
  chartImage,
  chartRegion,
  configPanel,
  createChart,
  openConfigTab,
  setChartTitle,
  setValueAggregate,
} from "./helpers/charts";
import { sample } from "./helpers/env";
import { importDatasetViaUi } from "./helpers/importDataset";
import { shot, unique } from "./helpers/shots";

test.describe("main flow", () => {
  test("import a CSV, see it in the grid, chart it, configure, save, reload", async ({
    page,
    app,
    api,
    project,
  }) => {
    const name = unique("sales");
    await app.open("data");

    // -- import through the dialog ------------------------------------------------------------
    await importDatasetViaUi(page, { file: sample("sales.csv") }, { name });
    await expect(app.datasetButton(new RegExp(`^${name}`))).toContainText("500 rows");

    // -- the grid shows the rows ---------------------------------------------------------------
    await app.selectDataset(name);
    const grid = page.getByRole("region", { name: `${name} data grid` });
    await expect(grid).toBeVisible();
    const table = grid.getByRole("table", { name: `${name}: 500 rows` });
    await expect(table).toHaveAttribute("aria-rowcount", "501"); // 500 rows + header
    for (const column of ["order_id", "order_date", "region", "product", "channel", "units"]) {
      await expect(
        table.getByRole("columnheader", { name: new RegExp(`^${column}`) }),
      ).toBeVisible();
    }
    // First record of samples/sales.csv: 1,2024-06-14,North,Gadget,Retail,13,41.21,535.73
    await expect(
      table.getByRole("row", { name: "1 1 2024-06-14 North Gadget Retail 13 41.21 535.73" }),
    ).toBeVisible();
    await shot(page, "main-flow-1-grid");

    // -- a bar chart --------------------------------------------------------------------------
    await app.goTo("charts");
    await createChart(page);
    await expect(chartImage(page)).toHaveAccessibleName(/Bar chart: region, Sum of units/);
    await expect(chartRegion(page)).toContainText("Aggregated from 500 rows");
    await shot(page, "main-flow-2-chart");

    // -- change its config --------------------------------------------------------------------
    await setChartTitle(page, "Average units by region");
    await expect(chartImage(page)).toHaveAccessibleName("Average units by region");
    await setValueAggregate(page, "Mean");
    await openConfigTab(page, "Chart");
    await configPanel(page).getByRole("spinbutton", { name: "Bar width (%)" }).fill("40");
    await shot(page, "main-flow-3-configured");

    // -- save ----------------------------------------------------------------------------------
    await app.save();
    const saved = await api.project(project.id);
    expect(saved.charts).toHaveLength(1);
    const chart = saved.charts?.[0];
    expect(chart?.plotType).toBe("bar");
    expect(chart?.style?.title).toBe("Average units by region");
    expect(chart?.encodings?.y?.[0]).toMatchObject({ field: "units", agg: "mean" });
    expect(chart?.params).toMatchObject({ barWidth: 40 });
    expect(saved.datasetIds).toContain(chart?.source.id);

    // -- reload: everything persists ----------------------------------------------------------
    await app.reload();
    await expect(app.projectName).toContainText(project.name);
    await expect(app.saveStatus).toHaveAttribute("data-status", "saved");
    await app.goTo("charts");
    await expect(page.getByRole("combobox", { name: "Chart", exact: true })).toContainText(
      /chart/i,
    );
    await expect(chartImage(page)).toHaveAccessibleName("Average units by region");
    await openConfigTab(page, "Style");
    await expect(
      configPanel(page).getByRole("textbox", { name: "Title", exact: true }),
    ).toHaveValue("Average units by region");
    await openConfigTab(page, "Data");
    await expect(
      configPanel(page).getByRole("combobox", { name: "Value 1: aggregate", exact: true }),
    ).toHaveValue("mean");
    await openConfigTab(page, "Chart");
    await expect(configPanel(page).getByRole("spinbutton", { name: "Bar width (%)" })).toHaveValue(
      "40",
    );
    await shot(page, "main-flow-4-after-reload");

    await api.deleteDataset(chart?.source.id ?? "").catch(() => undefined);
  });
});
