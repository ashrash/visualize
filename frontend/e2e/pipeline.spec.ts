import { readFileSync } from "node:fs";
import { expect, test } from "./fixtures";
import { chartImage, chartRegion, expectChartRendered } from "./helpers/charts";
import { sample } from "./helpers/env";
import { shot, unique } from "./helpers/shots";

// Rows of samples/sales.csv: order_id,order_date,region,product,channel,units,unit_price,sales
const SALES = readFileSync(sample("sales.csv"), "utf8")
  .split(/\r?\n/)
  .filter(Boolean)
  .slice(1)
  .map((line) => line.split(","));
const SOUTH_ROWS = SALES.filter((row) => row[2] === "South").length;
const SOUTH_PRODUCTS = new Set(SALES.filter((row) => row[2] === "South").map((row) => row[3])).size;

/** A "rows after this step" count, tolerant of a locale thousands separator. */
const rowsAfterStep = (n: number): RegExp =>
  new RegExp(`${String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",?")} rows after this step`);

test.describe("pipeline editor", () => {
  test("add filter and group-aggregate steps, preview each, chart the pipeline output", async ({
    page,
    app,
    api,
  }) => {
    const name = unique("sales");
    await api.importPath(sample("sales.csv"), { name });
    await app.open("data");
    await app.selectDataset(name);

    // -- build the pipeline --------------------------------------------------------------------
    await app.goTo("pipelines");
    await page.getByRole("button", { name: "Create a pipeline" }).click();

    const preview = page.getByRole("region", { name: "Pipeline preview data grid" });

    // -- filter: region = South ------------------------------------------------------------------
    await page.getByRole("button", { name: "Add step" }).click();
    await page.getByRole("menuitem", { name: /^Filter rows/ }).click();
    await page.getByRole("button", { name: "Add condition" }).click();
    await page.getByLabel("Column", { exact: true }).selectOption({ label: "region" });
    await page.getByLabel("Value", { exact: true }).fill("South");

    await expect(page.getByText(rowsAfterStep(SOUTH_ROWS))).toBeVisible();
    await expect(preview).toBeVisible();
    await expect(
      preview.getByRole("table", { name: new RegExp(`${SOUTH_ROWS} rows`) }),
    ).toBeVisible();
    await shot(page, "pipeline-1-filter-preview");

    // -- group and aggregate: by product, sum of units -------------------------------------------
    await page.getByRole("button", { name: "Add step" }).click();
    await page.getByRole("menuitem", { name: /^Group and aggregate/ }).click();
    await page
      .locator("fieldset", { hasText: "Group by" })
      .getByRole("checkbox", { name: "product" })
      .check();
    await page.getByLabel("Function", { exact: true }).selectOption({ label: "Sum" });
    await page.getByLabel("Column", { exact: true }).selectOption({ label: "units" });
    await page.getByLabel("Output name", { exact: true }).fill("total_units");

    await expect(page.getByText(rowsAfterStep(SOUTH_PRODUCTS))).toBeVisible();
    await expect(
      preview.getByRole("table", { name: new RegExp(`${SOUTH_PRODUCTS} rows`) }),
    ).toBeVisible();
    await expect(preview.getByRole("columnheader", { name: /^product/ })).toBeVisible();
    await expect(preview.getByRole("columnheader", { name: /^total_units/ })).toBeVisible();
    await shot(page, "pipeline-2-aggregate-preview");

    // -- chart the pipeline's output ---------------------------------------------------------------
    await app.goTo("charts");
    await page
      .getByLabel("New chart from", { exact: true })
      .selectOption({ label: `Pipeline: ${name} pipeline` });
    await page.getByRole("button", { name: "New chart", exact: true }).click();
    await expectChartRendered(page);
    await expect(chartImage(page)).toHaveAccessibleName(/product, Sum of total_units/);
    await expect(chartRegion(page)).toContainText(new RegExp(`${SOUTH_PRODUCTS} rows`));
    await shot(page, "pipeline-3-chart");

    await api.deleteDatasetByName(name);
  });

  test("join two datasets and pivot, materialize, and the step reports materialized", async ({
    page,
    app,
    api,
  }) => {
    const customersName = unique("customers");
    const countriesName = unique("countries");
    await Promise.all([
      api.importPath(sample("customers.tsv"), { name: customersName }),
      api.importPath(sample("countries.csv"), { name: countriesName }),
    ]);

    await app.open("data");
    await app.selectDataset(customersName);
    await app.goTo("pipelines");
    await page.getByRole("button", { name: "Create a pipeline" }).click();

    const preview = page.getByRole("region", { name: "Pipeline preview data grid" });

    // -- rename the join key first: the importer tags customers.country_iso3 as nominal but
    // countries.iso3 as geo (it recognizes the bare name "iso3"), and the compiler refuses to join
    // columns of different semantics. Renaming to the matching name sidesteps that, same as a user
    // would via the "add a cast step first" hint it gives. ------------------------------------------
    await page.getByRole("button", { name: "Add step" }).click();
    await page.getByRole("menuitem", { name: /^Rename columns/ }).click();
    await page.getByRole("button", { name: "Add rename" }).click();
    await page.getByLabel("Column", { exact: true }).selectOption({ label: "country_iso3" });
    await page.getByLabel("New name", { exact: true }).fill("iso3");
    await expect(preview.getByRole("columnheader", { name: /^iso3/ })).toBeVisible();

    // -- join customers to countries on the iso3 code ---------------------------------------------
    await page.getByRole("button", { name: "Add step" }).click();
    await page.getByRole("menuitem", { name: /^Join\b/ }).click();
    await page.getByLabel("Join with", { exact: true }).selectOption({ label: countriesName });
    await page.getByRole("button", { name: "Add key" }).click();
    await page.getByLabel("Current column", { exact: true }).selectOption({ label: "iso3" });
    await page.getByLabel("Other column", { exact: true }).selectOption({ label: "iso3" });

    await expect(page.getByText(rowsAfterStep(3000))).toBeVisible();
    await expect(preview.getByRole("columnheader", { name: /^continent/ })).toBeVisible();
    await shot(page, "pipeline-4-join-preview");

    // -- pivot: continent rows, segment columns, mean income ----------------------------------------
    await page.getByRole("button", { name: "Add step" }).click();
    await page.getByRole("menuitem", { name: /^Pivot/ }).click();
    await page
      .locator("fieldset", { hasText: "Row labels (index)" })
      .getByRole("checkbox", { name: "continent" })
      .check();
    await page
      .getByLabel("Values become columns", { exact: true })
      .selectOption({ label: "segment" });
    await page.getByLabel("Cell values", { exact: true }).selectOption({ label: "income" });
    await page.getByLabel("Combine with", { exact: true }).selectOption({ label: "Mean" });

    await expect(page.getByText(rowsAfterStep(6))).toBeVisible();
    await expect(preview.getByRole("columnheader", { name: /^continent/ })).toBeVisible();
    await shot(page, "pipeline-5-pivot-preview");

    // -- materialize: the step's own Parquet cache, not a new catalog dataset (there is no such
    // feature in this codebase -- see PipelineEditor.tsx/materialize.py). The observable result is
    // the step's "Materialized" status and the preview continuing to work from the cached file. ---
    await page.getByLabel("Materialize (write this step to Parquet)").check();
    await page.getByRole("button", { name: "Materialize now" }).click();
    await expect(page.getByText("Materialized", { exact: true })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("button", { name: "Materialize now" })).toBeVisible();
    await expect(page.getByText(rowsAfterStep(6))).toBeVisible();
    await shot(page, "pipeline-6-materialized");

    await api.deleteDatasetByName(customersName);
    await api.deleteDatasetByName(countriesName).catch(() => undefined);
  });
});
