import { mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { Page } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));
/** Screenshots worth looking at after a run (frontend/test-results/screenshots, git-ignored). */
export const SHOT_DIR = path.resolve(here, "..", "..", "test-results", "screenshots");

export async function shot(page: Page, name: string): Promise<string> {
  mkdirSync(SHOT_DIR, { recursive: true });
  const file = path.join(SHOT_DIR, `${name.replace(/[^\w.-]+/g, "_")}.png`);
  await page.screenshot({ path: file });
  return file;
}

/** A short unique suffix so parallel tests don't collide on dataset names in the shared catalog. */
export const unique = (prefix: string): string =>
  `${prefix}-${Date.now().toString(36).slice(-5)}${Math.floor(Math.random() * 1296).toString(36)}`;
