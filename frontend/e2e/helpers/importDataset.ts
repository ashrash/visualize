import { expect, type Page } from "@playwright/test";

/**
 * Every UI step of "import a dataset" lives in this file, so when the import wizard changes only
 * this helper needs updating. The flow it drives:
 *
 *   "Import data" -> pick a file (it uploads) or a path + "Continue" -> wait for the preview ->
 *   optionally rename and choose sheets -> "Import" ("Import N sheets" for Excel) -> wait until
 *   the dialog closes (it stays open on a summary with warnings, which the helper dismisses).
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
  /** Dataset name; default is whatever the wizard suggests (the file name). */
  name?: string;
  /** Excel only: sheets to import (default: the wizard's pre-selected sheets). */
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
    await dialog.getByRole("button", { name: "Continue" }).click();
  } else {
    throw new Error("importDatasetViaUi needs a file or a path");
  }

  // The Import button enables once the preview has loaded.
  const importButton = dialog.getByRole("button", { name: /^Import( \d+ sheets)?$/ });
  await expect(importButton).toBeEnabled({ timeout: 60_000 });

  if (options.name !== undefined) {
    await dialog.getByLabel("Dataset name", { exact: true }).fill(options.name);
  }
  for (const sheet of options.sheets ?? []) {
    const box = dialog.getByRole("checkbox", { name: sheet, exact: true });
    if (!(await box.isChecked())) await box.check();
  }
  await dialog.getByRole("button", { name: /^Import( \d+ sheets)?$/ }).click();

  await expect
    .poll(
      async () => {
        if (!(await dialog.isVisible())) return "closed";
        // "Done" is the summary step, kept open to show warnings. Never press "Close": that is the
        // dialog's X, and closing mid-import abandons the requests still in flight.
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
