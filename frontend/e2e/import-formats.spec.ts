import { copyFileSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "./fixtures";
import { sample } from "./helpers/env";
import { importDatasetViaUi } from "./helpers/importDataset";
import { shot, unique } from "./helpers/shots";

/**
 * The import dialog with the less common formats (the API-level checks for every sample are in
 * samples.spec.ts). Each case imports a committed sample through the file picker and checks what the
 * grid shows. Column headers are real DOM text, so unicode names can be asserted directly.
 */
const CASES = [
  {
    file: "customers.tsv",
    rows: "3,000 rows",
    headers: ["customer_id", "company", "city", "signup_date", "income"],
  },
  {
    file: "products.json",
    rows: "120 rows",
    headers: ["sku", "category", "price", "launched"],
  },
  {
    file: "events.ndjson",
    rows: "2,000 rows",
    headers: ["ts", "user_id", "event", "country"],
  },
  {
    // Windows-1252 bytes: accents must survive.
    file: "ventes_europe_cp1252.csv",
    rows: "400 rows",
    headers: ["Commande", "Région", "Quantité", "Prix (€)"],
  },
  {
    // UTF-8 with BOM, CJK and symbols in the header.
    file: "survey_unicode.csv",
    rows: "600 rows",
    headers: ["名前", "年齢", "Größe (cm)", "Café visits / week", "prix €", "Satisfaction ★"],
  },
] as const;

const escapeRegExp = (text: string): string => text.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");

for (const c of CASES) {
  test(`import ${c.file} through the dialog and see it in the grid`, async ({ page, app, api }) => {
    const name = unique(c.file.replace(/\W/g, "_"));
    await app.open("data");
    await importDatasetViaUi(page, { file: sample(c.file) }, { name });
    await app.selectDataset(name);

    await expect(app.datasetButton(new RegExp(`^${name}`))).toContainText(c.rows);
    const grid = page.getByRole("region", { name: `${name} data grid` });
    await expect(grid).toBeVisible();
    for (const header of c.headers) {
      await expect(
        grid.getByRole("columnheader", { name: new RegExp(`^${escapeRegExp(header)}`) }),
      ).toBeVisible();
    }
    // The first data row has real content (not blank, not replacement characters).
    const firstRow = grid.getByRole("row").nth(1);
    await expect(firstRow).not.toContainText("�");
    await expect(firstRow.getByRole("cell").nth(1)).not.toHaveText("");
    await shot(page, `import-${c.file}`);

    await api.deleteDatasetByName(name);
  });
}

test("import an Excel workbook through the dialog", {
  annotation: {
    type: "bug",
    description:
      "with the stagedImport flag on, importing two sheets of one upload fails the first sheet: 'the uploaded file has expired' (data-core / ui-data)",
  },
}, async ({ page, app, api }) => {
  const name = unique("xlsx");
  await app.open("data");
  await importDatasetViaUi(
    page,
    { file: sample("quarterly_report.xlsx") },
    { name, sheets: ["Revenue", "Headcount"] },
  );
  // One dataset per imported sheet; the wizard names them "<name> - <sheet>", the one-shot form
  // imports the first sheet under the given name. Either way the Revenue sheet (48 rows) is there.
  await expect(page.getByRole("complementary", { name: "Datasets" })).toContainText(name);
  await expect(page.getByRole("complementary", { name: "Datasets" })).toContainText("48 rows");
  await api.deleteDatasetByName(name);
  await api.deleteDatasetByName(`${name} - Revenue`);
  await api.deleteDatasetByName(`${name} - Headcount`);
});

test("a path with spaces and non-ASCII characters imports in place", async ({
  page,
  app,
  api,
}, testInfo) => {
  const dir = join(testInfo.outputDir, "dossier de données été");
  mkdirSync(dir, { recursive: true });
  const target = join(dir, "ventes décembre.csv");
  copyFileSync(sample("sales.csv"), target);

  const name = unique("path");
  await app.open("data");
  await importDatasetViaUi(page, { path: target }, { name });
  await expect(app.datasetButton(new RegExp(`^${name}`))).toContainText("500 rows");
  await api.deleteDatasetByName(name);
});

const BAD_FILES = [
  {
    name: "picture.png",
    content: Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13]),
  },
  { name: "corrupt.xlsx", content: Buffer.from("this is not a zip archive, so not a workbook\n") },
] as const;

for (const bad of BAD_FILES) {
  test(`${bad.name}: a file that can't be imported shows a readable error and adds no dataset`, async ({
    page,
    app,
    api,
  }, testInfo) => {
    mkdirSync(testInfo.outputDir, { recursive: true });
    const file = join(testInfo.outputDir, bad.name);
    writeFileSync(file, bad.content);

    await app.open("data");
    await page.getByRole("button", { name: "Import data" }).click();
    const dialog = page.getByRole("dialog", { name: "Import data" });
    await dialog.getByLabel("File", { exact: true }).setInputFiles(file);
    // One-shot form: press Import and get an alert. Wizard: staging or preview fails with an alert.
    const importButton = dialog.getByRole("button", { name: /^Import$/ });
    if ((await importButton.count()) > 0 && (await importButton.isEnabled())) {
      await importButton.click();
    }
    const alert = dialog.getByRole("alert");
    await expect(alert).toBeVisible({ timeout: 20_000 });
    await expect(alert).not.toBeEmpty();
    await expect(alert).not.toContainText(/traceback|internal server error|\[object Object\]/i);
    await shot(page, `import-error-${bad.name}`);
    // Nothing was added to the (shared) catalog for this file.
    expect((await api.datasets()).filter((d) => d.source.fileName === bad.name)).toEqual([]);
  });
}
