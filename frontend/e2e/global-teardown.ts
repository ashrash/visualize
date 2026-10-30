import { summarizeAxeReports } from "./helpers/axe";

/**
 * Runs once after all tests: merges the per-test axe results into one report
 * (test-results/axe-report.{json,md}). The throwaway data dir is removed by the config's exit
 * handler, after the server has stopped.
 */
export default async function globalTeardown(): Promise<void> {
  summarizeAxeReports();
}
