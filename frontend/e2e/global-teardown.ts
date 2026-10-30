import { rmSync } from "node:fs";
import { summarizeAxeReports } from "./helpers/axe";

/**
 * Runs once after all tests: merges the per-test axe results into one report, then removes the
 * throwaway data dir (best effort: the server still holds files open until Playwright stops it,
 * so on Windows part of it may linger in the OS temp folder).
 */
export default async function globalTeardown(): Promise<void> {
  summarizeAxeReports();
  const dir = process.env.VIZ_E2E_DATA_DIR;
  if (dir && !process.env.E2E_KEEP_DATA) {
    try {
      rmSync(dir, { recursive: true, force: true, maxRetries: 3, retryDelay: 200 });
    } catch {
      // Locked by the still-running server; the OS temp cleaner will get it.
    }
  }
}
