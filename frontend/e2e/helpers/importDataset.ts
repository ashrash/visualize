import { expect, type Page } from "@playwright/test";

/**
 * Every UI step of "import a dataset" lives in this file, so when the import dialog changes (it is
 * moving from a one-shot form to a staged wizard: pick file -> preview -> import) only this helper
 * needs updating. It copes with both shapes:
 *
 *   one-shot form: set the file or path, optionally the name, press "Import"
 *   staged wizard: set the file (it uploads) or path + "Continue", wait for the preview,
 *                  optionally rename, press "Import" ("Import N sheets" for Excel)
 *
 * Roles and accessible names only; no test ids.
 */
export interface ImportSource {
  /** Absolute path of a local file to upload through the file picker. */
  file?: string;
  /** A path on the server's machine, imported in place through the "Local path" tab. */
  path?: string;
}

export interface ImportUiOptions {
  /** Dataset name; default is whatever the dialog suggests (the file name). */
  name?: string;
  /** Excel only: sheets to import in the wizard (default: leave the pre-selected sheets). */
  sheets?: string[];
  /** Wait up to this long for the import to finish (default 120 s). */
  timeoutMs?: number;
}

export async function importDatasetViaUi(
  page: Page,
  source: ImportSource,
  options: ImportUiOptions = {},
): Promise<void> {
  const timeout = options.timeoutMs ?? 120_000;
  await page.getByRole("button", { name: "Import data" }).click();
  const dialog = page.getByRole("dialog", { name: "Import data" });
  await expect(dialog).toBeVisible();

  if (source.file) {
    await dialog.getByLabel("File", { exact: true }).setInputFiles(source.file);
  } else if (source.path) {
    await dialog.getByRole("tab", { name: "Local path" }).click();
    await dialog.getByLabel("File path").fill(source.path);
    // The wizard needs an explicit "Continue"; the one-shot form imports straight from here.
    const next = dialog.getByRole("button", { name: "Continue" });
    if ((await next.count()) > 0) await next.click();
  } else {
    throw new Error("importDatasetViaUi needs a file or a path");
  }

  const importButton = dialog.getByRole("button", { name: /^Import( \d+ sheets)?$/ });
  // In the wizard this enables once the preview has loaded.
  await expect(importButton).toBeEnabled({ timeout: 60_000 });

  if (options.name !== undefined) {
    await dialog.getByLabel(/^Dataset name/).fill(options.name);
  }
  if (options.sheets) {
    // Only the wizard has sheet checkboxes; the one-shot form imports the first sheet.
    for (const sheet of options.sheets) {
      const box = dialog.getByRole("checkbox", { name: sheet, exact: true });
      if ((await box.count()) > 0 && !(await box.isChecked())) await box.check();
    }
  }
  await importButton.click();

  // The dialog closes when the dataset is ready. If the wizard stays open to show warnings, close it.
  await expect
    .poll(
      async () => {
        if (!(await dialog.isVisible())) return "closed";
        // "Done" is the wizard's summary step (kept open to show warnings). Never press "Close":
        // that is the dialog's X, and closing mid-import abandons the upload.
        const done = dialog.getByRole("button", { name: "Done", exact: true });
        if ((await done.count()) > 0) await done.click();
        const alert = dialog.getByRole("alert");
        if ((await alert.count()) > 0) {
          throw new Error(`import failed: ${((await alert.first().textContent()) ?? "").trim()}`);
        }
        return "open";
      },
      { timeout, intervals: [250, 500, 1000] },
    )
    .toBe("closed");
}
