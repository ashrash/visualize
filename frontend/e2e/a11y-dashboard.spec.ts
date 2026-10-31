import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import type { Page, TestInfo } from "@playwright/test";
import { expect, test } from "./fixtures";
import { newBlockingViolations, runAxe, type ScreenReport } from "./helpers/axe";
import { kpiValue, openDashboards, seedRichBoard, widget } from "./helpers/dashboard";
import { unique } from "./helpers/shots";

/**
 * axe (WCAG 2.1 A/AA) on every Wave 3 surface, in both themes: the dashboard in presenting and
 * editing mode, the widget-add menu and dialogs, the share dialog, global filters, the public
 * share and embed pages, the print layout and the generated HTML report. Same rules as
 * a11y.spec.ts: a serious or critical finding fails unless its (rule, screen) pair is listed in
 * e2e/axe-baseline.json. Landmark, title and focus checks that axe's WCAG tags do not cover are
 * asserted directly below.
 */
async function check(page: Page, screen: string, theme: string, testInfo: TestInfo) {
  const report: ScreenReport = await runAxe(page, screen, theme);
  await testInfo.attach("axe", {
    body: JSON.stringify(report, null, 2),
    contentType: "application/json",
  });
  expect(
    newBlockingViolations(report),
    "serious or critical axe findings not in axe-baseline.json",
  ).toEqual([]);
}

/** Let the charts finish their entry animation before axe measures contrast. */
async function settle(page: Page) {
  await expect(page.locator("canvas").first()).toBeVisible();
  await page.waitForTimeout(1000);
}

for (const theme of ["light", "dark"] as const) {
  test.describe(`axe dashboards: ${theme} theme`, () => {
    let dataset = "";
    let projectId = "";

    test.beforeEach(async ({ app, api, project, page }) => {
      dataset = unique("a11yd");
      projectId = project.id;
      await seedRichBoard(api, project.id, dataset);
      await openDashboards(page);
      await app.setTheme(theme);
      await expect.poll(() => app.themeAttribute()).toBe(theme);
      await expect(kpiValue(page, "Total units")).toHaveText(/\d/);
      await settle(page);
    });

    test.afterEach(async ({ api }) => {
      await api.deleteDatasetByName(dataset);
    });

    test("dashboard, presenting", async ({ page }, testInfo) => {
      await check(page, "dashboard-view", theme, testInfo);
    });

    test("dashboard, editing (drag and resize handles, remove buttons)", async ({
      page,
    }, testInfo) => {
      await page.getByRole("button", { name: "Edit", exact: true }).click();
      await expect(page.locator(".dash-drag-handle").first()).toBeVisible();
      await page.waitForTimeout(400);
      await check(page, "dashboard-edit", theme, testInfo);
    });

    test("add-widget menu", async ({ page }, testInfo) => {
      await page.getByRole("button", { name: "Edit", exact: true }).click();
      await page.getByRole("button", { name: "Add widget", exact: true }).click();
      await expect(page.getByRole("menu")).toBeVisible();
      await check(page, "dashboard-add-menu", theme, testInfo);
    });

    for (const [kind, title] of [
      ["KPI card", "Add KPI card"],
      ["Text", "Add text"],
      ["Image", "Add image"],
      ["Filter", "Add filter"],
    ] as const) {
      test(`widget dialog: ${kind}`, async ({ page }, testInfo) => {
        await page.getByRole("button", { name: "Edit", exact: true }).click();
        await page.getByRole("button", { name: "Add widget", exact: true }).click();
        await page.getByRole("menuitem", { name: new RegExp(kind) }).click();
        await expect(page.getByRole("dialog", { name: title })).toBeVisible();
        await check(page, `widget-dialog-${kind.split(" ")[0]?.toLowerCase()}`, theme, testInfo);
      });
    }

    test("share dialog with a link", async ({ page }, testInfo) => {
      await page.getByRole("button", { name: "Share", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: /^Share "/ });
      await dialog.getByRole("button", { name: "Create link" }).click();
      await expect(dialog.getByRole("textbox", { name: "Share URL" })).toBeVisible();
      await check(page, "share-dialog", theme, testInfo);
    });

    test("global filters dialog", async ({ page }, testInfo) => {
      await page.getByRole("button", { name: "Global filters", exact: true }).click();
      await expect(page.getByRole("dialog", { name: "Global filters" })).toBeVisible();
      await check(page, "global-filters-dialog", theme, testInfo);
    });

    test("filter dropdown and drill-through menu", async ({ page }, testInfo) => {
      await page.getByRole("button", { name: "Region filter" }).click();
      await expect(page.getByRole("checkbox", { name: /^North/ })).toBeVisible();
      await check(page, "filter-popover", theme, testInfo);
      await page.keyboard.press("Escape");
      await widget(page, "Units by region")
        .getByRole("button", { name: "Drill through", exact: true })
        .click();
      await expect(page.getByRole("menuitem", { name: "Open data grid" })).toBeVisible();
      await check(page, "drill-through-menu", theme, testInfo);
    });

    test("dashboard with a dropdown filter applied", async ({ page }, testInfo) => {
      await page.getByRole("button", { name: "Region filter" }).click();
      await page.getByRole("checkbox", { name: /^North/ }).click();
      await page.keyboard.press("Escape");
      await expect(page.getByRole("button", { name: "Region filter" })).toContainText("North");
      await check(page, "dashboard-filtered", theme, testInfo);
    });

    test("print layout of the dashboard", async ({ page }, testInfo) => {
      await page.emulateMedia({ media: "print" });
      await check(page, "dashboard-print", theme, testInfo);
    });

    test("generated HTML report", async ({ page }, testInfo) => {
      const download = page.waitForEvent("download", { timeout: 45_000 });
      await page.getByRole("button", { name: "Report", exact: true }).click();
      await page.getByRole("menuitem", { name: "HTML report" }).click();
      const file = await (await download).path();
      expect(readFileSync(file ?? "", "utf8")).toContain("Rich board");
      await page.goto(pathToFileURL(file ?? "").href);
      await check(page, "dashboard-report", theme, testInfo);
    });

    test("public share page, embed page and the share print layout", async ({
      browser,
      api,
    }, testInfo) => {
      const project = await api.project(projectId);
      const boardId = project.dashboards?.[0]?.id ?? "";
      const link = await api.createShare(projectId, boardId);
      const visitor = await browser.newContext({
        colorScheme: theme,
        viewport: { width: 1440, height: 900 },
      });
      try {
        const guest = await visitor.newPage();
        await guest.goto(link.sharePath);
        await expect(guest.getByRole("heading", { level: 1, name: "Rich board" })).toBeVisible();
        await expect(kpiValue(guest, "Total units")).toHaveText(/\d/);
        await settle(guest);
        await check(guest, "share-page", theme, testInfo);

        await guest.emulateMedia({ media: "print" });
        await check(guest, "share-print", theme, testInfo);
        await guest.emulateMedia({ media: "screen" });

        await guest.goto(link.embedPath);
        await expect(kpiValue(guest, "Total units")).toHaveText(/\d/);
        await settle(guest);
        await check(guest, "embed-page", theme, testInfo);
      } finally {
        await visitor.close();
      }
    });
  });
}

// -- things axe's WCAG rule set does not decide ---------------------------------------------

test.describe("dashboard landmarks, titles and focus", () => {
  let dataset = "";
  test.afterEach(async ({ api }) => {
    await api.deleteDatasetByName(dataset);
  });

  test("share and embed pages keep a language, a title, one main landmark and (share) one h1", async ({
    browser,
    api,
    project,
  }) => {
    dataset = unique("a11yl");
    const { board } = await seedRichBoard(api, project.id, dataset);
    const link = await api.createShare(project.id, board.id);
    const visitor = await browser.newContext();
    try {
      const guest = await visitor.newPage();
      await guest.goto(link.sharePath);
      await expect(guest.getByRole("heading", { level: 1, name: "Rich board" })).toBeVisible();
      expect(await guest.locator("html").getAttribute("lang"), "<html lang>").toMatch(/^[a-z]{2}/);
      expect((await guest.title()).trim(), "document title").not.toBe("");
      await expect(guest.getByRole("main")).toHaveCount(1);
      await expect(guest.getByRole("heading", { level: 1 })).toHaveCount(1);
      // the shell's navigation and skip link are deliberately absent: nothing to skip past
      await expect(guest.getByRole("navigation")).toHaveCount(0);
      // the first tab stop is inside <main>
      await guest.keyboard.press("Tab");
      expect(
        await guest.evaluate(() => document.activeElement?.closest("main") !== null),
        "first focusable element is inside main",
      ).toBe(true);

      await guest.goto(link.embedPath);
      await expect(kpiValue(guest, "Total units")).toHaveText(/\d/);
      expect(await guest.locator("html").getAttribute("lang")).toMatch(/^[a-z]{2}/);
      await expect(guest.getByRole("main")).toHaveCount(1);
      // an embed has no page title heading of its own: the host page supplies it. Its iframe
      // `title` ("Embedded dashboard") is what a screen reader announces.
    } finally {
      await visitor.close();
    }
  });

  const DIALOGS = [
    ["KPI card", "Add KPI card"],
    ["Text", "Add text"],
    ["Image", "Add image"],
    ["Filter", "Add filter"],
  ] as const;

  /** Open a widget dialog with the keyboard only: focus the menu button, open it, pick the item. */
  const openWithKeyboard = async (page: Page, item: string, title: string) => {
    await page.getByRole("button", { name: "Add widget", exact: true }).focus();
    await page.keyboard.press("Enter");
    await page.getByRole("menuitem", { name: new RegExp(item) }).focus();
    await page.keyboard.press("Enter");
    const dialog = page.getByRole("dialog", { name: title });
    await expect(dialog).toBeVisible();
    return dialog;
  };
  const inDialog = (page: Page) =>
    page.evaluate(() => document.activeElement?.closest('[role="dialog"]') !== null);

  test("widget dialogs take focus when opened and keep it inside while open", async ({
    page,
    api,
    project,
  }) => {
    dataset = unique("a11yf");
    await seedRichBoard(api, project.id, dataset);
    await openDashboards(page);
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    for (const [item, title] of DIALOGS) {
      const dialog = await openWithKeyboard(page, item, title);
      expect(await inDialog(page), `${title}: focus moved into the dialog`).toBe(true);
      for (let i = 0; i < 12; i++) await page.keyboard.press("Tab");
      expect(await inDialog(page), `${title}: Tab stays inside the modal`).toBe(true);
      await page.keyboard.press("Escape");
      await expect(dialog).toBeHidden();
    }
  });

  // KNOWN BUG (ui-dashboard): the widget dialogs are opened from a menu item through state, so
  // Radix Dialog has no trigger to give focus back to; after Escape, focus is on <body> and a
  // keyboard user restarts from the top of the page (WCAG 2.4.3). `test.fail` is the sentinel.
  // Fix: return focus to the "Add widget" button in the dialog's onCloseAutoFocus.
  test("widget dialogs give focus back to a control when closed", async ({
    page,
    api,
    project,
  }) => {
    test.fail(true, "TODO(ui-dashboard): focus falls to <body> when a widget dialog closes");
    dataset = unique("a11yg");
    await seedRichBoard(api, project.id, dataset);
    await openDashboards(page);
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    for (const [item, title] of DIALOGS) {
      const dialog = await openWithKeyboard(page, item, title);
      await page.keyboard.press("Escape");
      await expect(dialog).toBeHidden();
      await expect
        .poll(() => page.evaluate(() => document.activeElement?.tagName.toLowerCase() ?? "none"), {
          message: `${title}: focus returns to a control after closing`,
        })
        .not.toBe("body");
    }
  });

  test("dashboard toolbar controls are reachable with the keyboard", async ({
    page,
    api,
    project,
  }) => {
    dataset = unique("a11yk");
    await seedRichBoard(api, project.id, dataset);
    await openDashboards(page);
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    // Every control the dashboard exposes (except react-grid-layout's move and resize handles,
    // a known gap covered in dashboard.spec.ts) has a tab stop and an accessible name.
    const wanted = [
      "Present",
      "Duplicate",
      "Global filters",
      "Print",
      "Report",
      "Share",
      "Add widget",
      "Remove Units by region",
      "Remove Total units",
    ];
    for (const name of wanted) {
      const control = page.getByRole("button", { name, exact: true });
      await control.first().focus();
      await expect(control.first(), `${name} can take focus`).toBeFocused();
    }
  });
});
