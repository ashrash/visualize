import { mkdtempSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { defineConfig } from "@playwright/test";
import { featureOverrides, REPO_ROOT } from "./helpers/env";
import { freePort } from "./helpers/port";

/**
 * End-to-end tests run against the built app served by `uv run viz serve` on a free port with a
 * throwaway data dir. Environment switches:
 *
 *   E2E_BASE_URL     test an already running server instead (no build, no webServer)
 *   E2E_SKIP_BUILD   reuse backend/src/viz/static instead of rebuilding the frontend
 *   E2E_LARGE        also run the @large tests (needs scripts/gen_large_dataset.py output)
 *   E2E_LARGE_ROWS   row count of that fixture (default 10,000,000)
 *   E2E_FEATURES     JSON feature-flag overrides, e.g. {"stagedImport":true,"serverRows":true}
 *   E2E_CHANNEL      browser channel (default "chrome" locally, bundled chromium when CI is set)
 *   VIZ_E2E_UV       the uv executable (default "uv")
 *
 * The port and data dir are chosen once and published through env vars so that worker processes,
 * which load this file again, see the same values.
 */
const external = process.env.E2E_BASE_URL;
if (!external) {
  process.env.VIZ_E2E_PORT ??= String(await freePort());
  process.env.VIZ_E2E_DATA_DIR ??= mkdtempSync(path.join(os.tmpdir(), "viz-e2e-"));
}
const baseURL = external ?? `http://127.0.0.1:${process.env.VIZ_E2E_PORT}`;
const uv = process.env.VIZ_E2E_UV ?? "uv";
const serve = `${uv} run viz serve --no-open --port ${process.env.VIZ_E2E_PORT}`;
const command = process.env.E2E_SKIP_BUILD ? serve : `${uv} run viz build && ${serve}`;
const ci = Boolean(process.env.CI);

export default defineConfig({
  testDir: ".",
  testMatch: /.*\.spec\.ts$/,
  outputDir: "../test-results/artifacts",
  fullyParallel: true,
  forbidOnly: ci,
  retries: ci ? 1 : 0,
  workers: ci ? 2 : 4,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  // @large needs the generated 10M-row fixture, so it is opt-in.
  grepInvert: process.env.E2E_LARGE ? undefined : /@large/,
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "../playwright-report" }],
    ["json", { outputFile: "../test-results/results.json" }],
  ],
  globalTeardown: "./global-teardown.ts",
  use: {
    baseURL,
    channel: ci ? undefined : (process.env.E2E_CHANNEL ?? "chrome"),
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    acceptDownloads: true,
  },
  metadata: { features: featureOverrides() },
  webServer: external
    ? undefined
    : {
        command,
        cwd: REPO_ROOT,
        url: `${baseURL}/api/health`,
        timeout: 240_000,
        reuseExistingServer: false,
        stdout: "pipe",
        stderr: "pipe",
        env: {
          VIZ_DATA_DIR: process.env.VIZ_E2E_DATA_DIR ?? "",
          // Never let a developer's own settings leak into the test server.
          VIZ_DATABASE_URL: "",
        },
      },
});
