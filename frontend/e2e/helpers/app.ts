import { expect, type Locator, type Page } from "@playwright/test";
import type { Project } from "../../src/api/types";

export type Screen = "data" | "pipelines" | "charts";
export type ThemeName = "auto" | "light" | "dark";

/**
 * Page object for the app shell: header, navigation, project menu, save status, theme.
 * Selectors are roles and accessible names; canvas text (ECharts axis titles) is never asserted.
 */
export class App {
  constructor(
    readonly page: Page,
    /** The project this test's browser context opens (created by the `project` fixture). */
    readonly project: Pick<Project, "id" | "name">,
  ) {}

  /** Open a screen and wait until the project has loaded (the menu shows its name). */
  async open(screen: Screen = "data"): Promise<void> {
    await this.page.goto(`/${screen}`);
    await expect(this.projectMenu).not.toContainText("Loading");
  }

  /** Same as open(), for a reload of the current page. */
  async reload(): Promise<void> {
    await this.page.reload();
    await expect(this.projectMenu).not.toContainText("Loading");
  }

  async goTo(screen: Screen): Promise<void> {
    const name = { data: "Data", pipelines: "Pipelines", charts: "Charts" }[screen];
    await this.page
      .getByRole("navigation", { name: "Primary" })
      .getByRole("link", { name })
      .click();
    await expect(this.page).toHaveURL(new RegExp(`/${screen}$`));
  }

  // -- header ---------------------------------------------------------------------------------

  get projectMenu(): Locator {
    // Its accessible name is "<project name>, project menu" (the name comes first, WCAG 2.5.3).
    return this.page.getByRole("banner").getByRole("button", { name: /project menu$/i });
  }

  get projectName(): Locator {
    return this.projectMenu;
  }

  /** The "Saved / Saving... / Unsaved changes / Save failed" indicator (data-status attribute). */
  get saveStatus(): Locator {
    return this.page.locator("[data-status]");
  }

  async waitForSaved(): Promise<void> {
    await expect(this.saveStatus).toHaveAttribute("data-status", "saved", { timeout: 15_000 });
  }

  get saveButton(): Locator {
    return this.page.getByRole("banner").getByRole("button", { name: "Save", exact: true });
  }

  get undoButton(): Locator {
    return this.page.getByRole("button", { name: "Undo", exact: true });
  }

  get redoButton(): Locator {
    return this.page.getByRole("button", { name: "Redo", exact: true });
  }

  /** Press Save and wait for the indicator to settle. */
  async save(): Promise<void> {
    await this.saveButton.click();
    await this.waitForSaved();
  }

  // -- theme ----------------------------------------------------------------------------------

  get themeSelect(): Locator {
    return this.page.getByLabel("Theme", { exact: true });
  }

  async setTheme(mode: ThemeName): Promise<void> {
    await this.themeSelect.selectOption({
      label: { auto: "Auto", light: "Light", dark: "Dark" }[mode],
    });
  }

  /** The `data-theme` attribute on <html> ("light", "dark", or null for auto). */
  async themeAttribute(): Promise<string | null> {
    return this.page.evaluate(() => document.documentElement.getAttribute("data-theme"));
  }

  // -- project menu -----------------------------------------------------------------------------

  async openProjectMenu(): Promise<void> {
    await this.projectMenu.click();
    await expect(this.page.getByRole("menu")).toBeVisible();
  }

  menuItem(name: string | RegExp): Locator {
    return this.page.getByRole("menuitem", { name });
  }

  // -- datasets -------------------------------------------------------------------------------

  /** The dataset list entry (a button) for a dataset name. */
  datasetButton(name: string | RegExp): Locator {
    return this.page.getByRole("complementary", { name: "Datasets" }).getByRole("button", { name });
  }

  /** Make a dataset the active one (the catalog is shared, so never rely on list order). */
  async selectDataset(name: string): Promise<void> {
    const button = this.datasetButton(
      new RegExp(`^${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`),
    );
    await button.click();
    await expect(button).toHaveAttribute("aria-current", "true");
  }
}
