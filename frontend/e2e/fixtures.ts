import { test as base, expect } from "@playwright/test";
import type { Project } from "../src/api/types";
import { Api } from "./helpers/api";
import { App } from "./helpers/app";

interface Fixtures {
  /** REST helper for test setup and for checking what the UI saved. */
  api: Api;
  /** A fresh, empty project that the app opens on first load (browser context is isolated). */
  project: Project;
  /** Page object with the app's common controls. */
  app: App;
}

export const test = base.extend<Fixtures>({
  api: async ({ request }, use) => {
    await use(new Api(request));
  },

  project: async ({ api, context }, use, testInfo) => {
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const project = await api.createProject(`e2e ${testInfo.title.slice(0, 40)} ${suffix}`);
    // Only seed storage when it is empty, so a reload keeps whatever project the test switched to.
    await context.addInitScript((id: string) => {
      try {
        if (!localStorage.getItem("viz.lastProject")) localStorage.setItem("viz.lastProject", id);
      } catch {
        // Storage can be unavailable on about:blank; the app page always has it.
      }
    }, project.id);
    await use(project);
    await api.deleteProject(project.id).catch(() => undefined);
  },

  // Depends on `project` on purpose: every test that drives the UI gets its own project. Without
  // it the app would open whichever project is newest, which may belong to a parallel test.
  app: async ({ page, project }, use) => {
    await use(new App(page, project));
  },
});

export { expect };
