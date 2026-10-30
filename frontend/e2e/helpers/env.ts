import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

/** Repo root (frontend/e2e/helpers -> three levels up). */
export const REPO_ROOT = path.resolve(here, "..", "..", "..");
export const SAMPLES_DIR = path.join(REPO_ROOT, "samples");
export const PERF_DIR = path.join(REPO_ROOT, "data", "perf");

/** Absolute path of a committed sample file. */
export const sample = (name: string): string => path.join(SAMPLES_DIR, name);

export const LARGE_ROWS = Number(process.env.E2E_LARGE_ROWS ?? 10_000_000);

/** The CSV and Parquet fixtures from scripts/gen_large_dataset.py. */
export const largeCsv = (): string => path.join(PERF_DIR, `large_${LARGE_ROWS}.csv`);
export const largeParquet = (): string => path.join(PERF_DIR, `large_${LARGE_ROWS}.parquet`);
export const largeFixtureExists = (): boolean => existsSync(largeCsv());

/** Feature-flag overrides for the app under test (the UI's `viz.features` localStorage key). */
export function featureOverrides(): Record<string, boolean> {
  const raw = process.env.E2E_FEATURES;
  if (!raw) return {};
  try {
    return JSON.parse(raw) as Record<string, boolean>;
  } catch {
    throw new Error(`E2E_FEATURES must be JSON, e.g. {"stagedImport":true}; got: ${raw}`);
  }
}
