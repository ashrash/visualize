import { readFileSync } from "node:fs";
import { expect, test } from "./fixtures";
import {
  activeFilters,
  barChart,
  brushBars,
  chartItem,
  chartMap,
  chartRows,
  chipLabel,
  chipValues,
  clickBar,
  dashboard,
  dragBy,
  fmt,
  item,
  kpiItem,
  kpiValue,
  linkAll,
  makePng,
  openDashboards,
  SALES,
  seedProject,
  sumUnits,
  widget,
} from "./helpers/dashboard";
import { sample } from "./helpers/env";
import { shot, unique } from "./helpers/shots";

/**
 * Dashboards (Wave 3): build, tabs, cross-filter, brush, drill-down and drill-through, filter
 * widgets, share and embed, report and print. Projects are seeded through the API; behaviour is
 * driven and asserted through the UI. Rendered data is checked through KPI numbers, the widgets'
 * "View as table" view, active-filter chips and the data grid (ECharts draws on a canvas).
 */
const REGIONS = ["East", "North", "South", "West"];
const by = (key: "region" | "product" | "channel", rows = SALES) => {
  const out: Record<string, number> = {};
  for (const r of rows) out[r[key]] = (out[r[key]] ?? 0) + r.units;
  return out;
};
const inRegions = (regions: string[]) => SALES.filter((r) => regions.includes(r.region));

test.describe("dashboards", () => {
  let names: string[] = [];
  test.afterEach(async ({ api }) => {
    for (const n of names) await api.deleteDatasetByName(n);
    names = [];
  });

  const importSales = async (api: import("./helpers/api").Api, prefix = "dash") => {
    const name = unique(prefix);
    names.push(name);
    return api.importPath(sample("sales.csv"), { name });
  };

  // -- build ----------------------------------------------------------------------------------

  test("create a dashboard, add chart, KPI, text and image widgets, arrange, save, reload", async ({
    page,
    app,
    api,
    project,
  }) => {
    const ds = await importSales(api);
    const source = { kind: "dataset", id: ds.id } as const;
    const chart = barChart(source, { name: "Units by region", x: "region", y: "units" });
    await seedProject(api, project.id, [ds], [chart], []);

    await openDashboards(page);
    await expect(page.getByText("This project has no dashboards yet.")).toBeVisible();
    await page.getByRole("button", { name: "Create a dashboard", exact: true }).click();
    // An empty dashboard opens in edit mode.
    await expect(page.getByRole("button", { name: "Present", exact: true })).toBeVisible();
    await page.getByRole("textbox", { name: "Dashboard name" }).fill("Sales overview");

    const addWidget = async (menuItem: string | RegExp) => {
      await page.getByRole("button", { name: "Add widget", exact: true }).click();
      await page.getByRole("menuitem", { name: menuItem }).click();
    };

    // chart widget from the saved chart
    await addWidget("Units by region");
    await expect(widget(page, "Units by region")).toBeVisible();

    // KPI
    await addWidget("KPI card");
    let dialog = page.getByRole("dialog", { name: "Add KPI card" });
    await dialog.getByRole("textbox", { name: "Title" }).fill("Total units");
    await dialog.getByRole("combobox", { name: "Source" }).selectOption({ label: ds.name });
    await dialog.getByLabel("Measure field").fill("units");
    await dialog.getByRole("combobox", { name: "Number format" }).selectOption("number:0");
    await dialog.getByRole("button", { name: "Add", exact: true }).click();
    await expect(kpiValue(page, "Total units")).toHaveText(fmt(sumUnits(SALES)));

    // text (Markdown)
    await addWidget(/Text/);
    dialog = page.getByRole("dialog", { name: "Add text" });
    await dialog
      .getByRole("textbox", { name: "Markdown" })
      .fill("## Quarterly notes\n\nA **bold** claim.");
    await dialog.getByRole("button", { name: "Add", exact: true }).click();
    await expect(
      widget(page, "Text").getByRole("heading", { name: "Quarterly notes" }),
    ).toBeVisible();
    await expect(widget(page, "Text").locator("strong")).toHaveText("bold");

    // image: a real upload through POST /api/assets
    await addWidget(/Image/);
    dialog = page.getByRole("dialog", { name: "Add image" });
    const uploaded = page.waitForResponse(
      (r) => r.url().endsWith("/api/assets") && r.request().method() === "POST",
    );
    await dialog
      .getByLabel("Image", { exact: true })
      .setInputFiles({ name: "company_logo.png", mimeType: "image/png", buffer: makePng() });
    expect((await uploaded).status()).toBe(201);
    await expect(dialog.getByRole("img", { name: "Preview of the upload" })).toBeVisible();
    // The alt text starts from the file name; the user describes it properly.
    await expect(dialog.getByRole("textbox", { name: "Alt text" })).toHaveValue("company logo");
    await dialog.getByRole("textbox", { name: "Alt text" }).fill("Company logo");
    await dialog.getByRole("button", { name: "Add", exact: true }).click();
    const logo = widget(page, "Company logo").getByRole("img", { name: "Company logo" });
    await expect(logo).toBeVisible();
    await expect
      .poll(() => logo.evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth))
      .toBe(8);

    // arrange: drag the KPI by its handle, shrink the chart by its resize handle
    const frame = (title: string) =>
      page.locator(".react-grid-item").filter({ has: widget(page, title) });
    await page.waitForTimeout(400); // layout settles
    const before = await frame("Total units").boundingBox();
    if (!before) throw new Error("no KPI box");
    await dragBy(page, frame("Total units").locator(".dash-drag-handle"), 170, 0);
    await expect
      .poll(async () => (await frame("Total units").boundingBox())?.x ?? 0)
      .toBeGreaterThan(before.x + 80);

    const chartFrame = frame("Units by region");
    const sized = await chartFrame.boundingBox();
    if (!sized) throw new Error("no chart box");
    await dragBy(page, chartFrame.locator(".react-resizable-handle").first(), -120, -40);
    await expect
      .poll(async () => (await chartFrame.boundingBox())?.width ?? 0)
      .toBeLessThan(sized.width - 40);
    await shot(page, "dashboard-1-built");

    // save, then check the document
    await app.save();
    const saved = await api.project(project.id);
    const d = saved.dashboards?.[0];
    expect(d?.name).toBe("Sales overview");
    const items = d?.tabs?.[0]?.items ?? [];
    expect(items.map((i) => i.widget.kind).sort()).toEqual(["chart", "image", "kpi", "text"]);
    const kpi = items.find((i) => i.widget.kind === "kpi");
    const chartItemSaved = items.find((i) => i.widget.kind === "chart");
    expect(kpi && kpi.x > 6, "the KPI was moved right").toBe(true);
    expect(chartItemSaved && chartItemSaved.w < 6, "the chart was made narrower").toBe(true);
    const image = items.find((i) => i.widget.kind === "image")?.widget;
    expect(image && "assetId" in image ? image.assetId : "").toMatch(/\S/);
    const assetId = image && "assetId" in image ? image.assetId : "";
    const served = await page.request.get(`/api/assets/${assetId}`);
    expect(served.status()).toBe(200);
    expect(served.headers()["content-type"]).toBe("image/png");

    // reload: everything is back, in presenting mode
    await app.reload();
    await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeVisible();
    await expect(kpiValue(page, "Total units")).toHaveText(fmt(sumUnits(SALES)));
    await expect(widget(page, "Units by region").locator("canvas").first()).toBeVisible();
    await expect(
      widget(page, "Text").getByRole("heading", { name: "Quarterly notes" }),
    ).toBeVisible();
    await expect(widget(page, "Company logo").getByRole("img")).toBeVisible();
  });

  // Moving and resizing have a keyboard path (CLAUDE.md section 6): the drag handle is a button;
  // Space picks the widget up, arrows move it, Shift+arrows resize it, Space drops it.
  test("layout can be moved and resized with the keyboard", async ({ page, api, project }) => {
    const ds = await importSales(api, "kbd");
    const chart = barChart({ kind: "dataset", id: ds.id }, { name: "K", x: "region", y: "units" });
    await seedProject(
      api,
      project.id,
      [ds],
      [chart],
      [
        dashboard("Keyboard", [
          { name: "Tab 1", items: [chartItem(chart, { x: 0, y: 0, w: 6, h: 6 })] },
        ]),
      ],
    );
    await openDashboards(page);
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    const handle = page.locator(".dash-drag-handle").first();
    await handle.focus();
    await expect(handle).toBeFocused();
    const before = await page.locator(".react-grid-item").first().boundingBox();
    await page.keyboard.press("Space");
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("Shift+ArrowRight");
    await page.keyboard.press("Space");
    // the grid animates a move (200 ms), so poll rather than read the box at once
    await expect
      .poll(async () => (await page.locator(".react-grid-item").first().boundingBox())?.x ?? 0)
      .toBeGreaterThan(before?.x ?? 0);
    await expect
      .poll(async () => (await page.locator(".react-grid-item").first().boundingBox())?.width ?? 0)
      .toBeGreaterThan(before?.width ?? 0);
  });

  // -- tabs, cross-filter -----------------------------------------------------------------------

  test("cross-filter by clicking a bar; the selection survives a tab switch", async ({
    page,
    api,
    project,
  }) => {
    const ds = await importSales(api, "xf");
    const source = { kind: "dataset", id: ds.id } as const;
    const a = barChart(source, { name: "Units by region", x: "region", y: "units" });
    const b = barChart(source, { name: "Units by product", x: "product", y: "units" });
    const c = barChart(source, { name: "Units by channel", x: "channel", y: "units" });
    const board = dashboard(
      "Cross-filter",
      [
        {
          name: "Overview",
          items: [
            chartItem(a, { x: 0, y: 0, w: 6, h: 6 }),
            chartItem(b, { x: 6, y: 0, w: 6, h: 6 }),
            kpiItem("Total units", source, { x: 0, y: 6, w: 3, h: 3 }),
          ],
        },
        {
          name: "Details",
          items: [
            item({ kind: "text", markdown: "Second tab" }, { x: 0, y: 0, w: 4, h: 2 }),
            chartItem(c, { x: 4, y: 0, w: 8, h: 6 }),
          ],
        },
      ],
      { linkGroups: [linkAll([a, b, c])] },
    );
    await seedProject(api, project.id, [ds], [a, b, c], [board]);
    await openDashboards(page);

    // baseline: nothing filtered
    await expect(kpiValue(page, "Total units")).toHaveText(fmt(sumUnits(SALES)));
    expect(await chartMap(page, "Units by product")).toEqual(by("product"));

    // click the second bar of the region chart
    const chip = await clickBar(page, "Units by region", 1, REGIONS.length);
    const picked = chipValues(chip);
    expect(picked).toHaveLength(1);
    expect(REGIONS).toContain(picked[0]);
    const rows = inRegions(picked);

    // linked widgets re-query with the selection; the clicked chart keeps its full data
    await expect(kpiValue(page, "Total units")).toHaveText(fmt(sumUnits(rows)));
    await expect.poll(() => chartMap(page, "Units by product")).toEqual(by("product", rows));
    await expect(activeFilters(page)).toContainText(picked[0] ?? "");
    await shot(page, "dashboard-2-cross-filtered");

    // switch tab: the other tab's chart is filtered by the same selection
    await page.getByRole("tab", { name: "Details" }).click();
    await expect(widget(page, "Text")).toContainText("Second tab");
    await expect.poll(() => chartMap(page, "Units by channel")).toEqual(by("channel", rows));
    await expect(activeFilters(page)).toContainText(picked[0] ?? "");

    // and back: the selection is still in force on the chart that made it, and on the others
    await page.getByRole("tab", { name: "Overview" }).click();
    await expect(activeFilters(page)).toContainText(picked[0] ?? "");
    await expect(
      widget(page, "Units by region").getByRole("button", { name: "Clear selection", exact: true }),
    ).toBeVisible();
    await expect(kpiValue(page, "Total units")).toHaveText(fmt(sumUnits(rows)));
    await expect.poll(() => chartMap(page, "Units by product")).toEqual(by("product", rows));
    // the clicked chart itself is not filtered by its own selection
    expect(await chartMap(page, "Units by region")).toEqual(by("region"));

    // clear all: back to the baseline everywhere
    await page.getByRole("button", { name: "Clear all", exact: true }).click();
    await expect(activeFilters(page)).toHaveCount(0);
    await expect(kpiValue(page, "Total units")).toHaveText(fmt(sumUnits(SALES)));
    await expect.poll(() => chartMap(page, "Units by product")).toEqual(by("product"));
  });

  test("brush selection filters linked widgets, survives a tab switch, and clears", async ({
    page,
    api,
    project,
  }) => {
    const ds = await importSales(api, "brush");
    const source = { kind: "dataset", id: ds.id } as const;
    const a = barChart(source, { name: "Units by region", x: "region", y: "units", brush: true });
    const b = barChart(source, { name: "Units by product", x: "product", y: "units" });
    const board = dashboard(
      "Brush",
      [
        {
          name: "Overview",
          items: [
            chartItem(a, { x: 0, y: 0, w: 7, h: 6 }),
            chartItem(b, { x: 7, y: 0, w: 5, h: 6 }),
            kpiItem("Total units", source, { x: 0, y: 6, w: 3, h: 3 }),
          ],
        },
        {
          name: "Other",
          items: [item({ kind: "text", markdown: "Other tab" }, { x: 0, y: 0, w: 4, h: 2 })],
        },
      ],
      { linkGroups: [linkAll([a, b])] },
    );
    await seedProject(api, project.id, [ds], [a, b], [board]);
    await openDashboards(page);
    await expect(kpiValue(page, "Total units")).toHaveText(fmt(sumUnits(SALES)));

    // drag a band over the first two categories
    await brushBars(page, "Units by region", 0, 1, REGIONS.length);
    await expect(activeFilters(page)).toBeVisible();
    const chip = await chipLabel(activeFilters(page).locator("span.rounded-full").first());
    const picked = chipValues(chip);
    expect(picked.length, `brushed regions: ${chip}`).toBeGreaterThanOrEqual(2);
    for (const region of picked) expect(REGIONS).toContain(region);
    const rows = inRegions(picked);
    await expect(kpiValue(page, "Total units")).toHaveText(fmt(sumUnits(rows)));
    await expect.poll(() => chartMap(page, "Units by product")).toEqual(by("product", rows));
    await shot(page, "dashboard-3-brushed");

    // the band is not lost by leaving the tab and coming back
    await page.getByRole("tab", { name: "Other" }).click();
    await expect(widget(page, "Text")).toContainText("Other tab");
    await expect(activeFilters(page)).toContainText(picked.join(", "));
    await page.getByRole("tab", { name: "Overview" }).click();
    await expect(activeFilters(page)).toContainText(picked.join(", "));
    await expect(kpiValue(page, "Total units")).toHaveText(fmt(sumUnits(rows)));

    // clear the brush from the chart: the filter goes everywhere
    await widget(page, "Units by region")
      .getByRole("button", { name: "Clear selection", exact: true })
      .first()
      .click();
    await expect(activeFilters(page)).toHaveCount(0);
    await expect(kpiValue(page, "Total units")).toHaveText(fmt(sumUnits(SALES)));
    await expect.poll(() => chartMap(page, "Units by product")).toEqual(by("product"));
  });

  // -- drill-down, drill-through ----------------------------------------------------------------

  test("drill down two levels, then drill through to the grid filtered by the whole path", async ({
    page,
    api,
    project,
  }) => {
    const name = unique("hier");
    names.push(name);
    const ds = await api.importPath(sample("sales_hierarchy.csv"), { name });
    const chart = barChart(
      { kind: "dataset", id: ds.id },
      {
        name: "Revenue",
        x: "region",
        y: "revenue_k",
        drill: ["region", "category", "product"],
      },
    );
    await seedProject(
      api,
      project.id,
      [ds],
      [chart],
      [
        dashboard("Drill", [
          { name: "Tab 1", items: [chartItem(chart, { x: 0, y: 0, w: 8, h: 7 })] },
        ]),
      ],
    );
    await openDashboards(page);

    // level 0: regions. The table view lets a keyboard (and this test) drill without the canvas.
    const regions = (await chartRows(page, "Revenue")).map((r) => r[0]);
    expect(regions).toContain("Americas");
    await widget(page, "Revenue").getByRole("button", { name: "Drill into Americas" }).click();
    const crumbs = widget(page, "Revenue").getByRole("navigation", { name: "Drill-down" });
    await expect(crumbs).toContainText("Americas");
    // level 1: categories of Americas only
    await expect
      .poll(async () => (await chartRows(page, "Revenue")).map((r) => r[0]).sort())
      .toEqual(["Hardware", "Services", "Software"]);
    await widget(page, "Revenue").getByRole("button", { name: "Drill into Hardware" }).click();
    // level 2: the products of Hardware within Americas
    await expect
      .poll(async () => (await chartRows(page, "Revenue")).map((r) => r[0]).sort())
      .toEqual(["Laptops", "Phones"]);
    await shot(page, "dashboard-4-drilled");

    // drill through to the data grid: the path (region AND category), not just the leaf level
    await widget(page, "Revenue")
      .getByRole("button", { name: "Drill through", exact: true })
      .click();
    await page.getByRole("menuitem", { name: "Open data grid" }).click();
    await expect(page).toHaveURL(/\/data$/);
    const grid = page.getByRole("region", { name: `${name} data grid` });
    await expect(grid).toBeVisible();
    // 20 rows in the file; Hardware appears in 7 of them across regions, Americas+Hardware in 2.
    await expect(page.getByText("2 of 20 rows")).toBeVisible();
    await expect(grid.getByRole("cell", { name: "Laptops" })).toBeVisible();
    await expect(grid.getByRole("cell", { name: "Phones" })).toBeVisible();
    await expect(grid.getByRole("cell", { name: "Americas" })).toHaveCount(2);
    await expect(grid.getByRole("cell", { name: "Software" })).toHaveCount(0);
    await shot(page, "dashboard-5-grid-drill-through");
  });

  // -- filters ------------------------------------------------------------------------------------

  test("dropdown, date-range and slider filters narrow every widget", async ({
    page,
    api,
    project,
  }) => {
    const ds = await importSales(api, "flt");
    const source = { kind: "dataset", id: ds.id } as const;
    const a = barChart(source, { name: "Units by region", x: "region", y: "units" });
    const b = barChart(source, { name: "Units by product", x: "product", y: "units" });
    const region = {
      id: "f-region",
      label: "Region",
      field: "region",
      fieldMap: {},
      predicate: null,
      control: "dropdown",
    } as const;
    const when = {
      id: "f-date",
      label: "Order date",
      field: "order_date",
      fieldMap: {},
      predicate: null,
      control: "date_range",
    } as const;
    const size = {
      id: "f-units",
      label: "Units",
      field: "units",
      fieldMap: {},
      predicate: null,
      control: "slider",
    } as const;
    const board = dashboard(
      "Filters",
      [
        {
          name: "Tab 1",
          items: [
            chartItem(a, { x: 0, y: 0, w: 6, h: 6 }),
            chartItem(b, { x: 6, y: 0, w: 6, h: 6 }),
            kpiItem("Total units", source, { x: 0, y: 6, w: 3, h: 3 }),
            // the date filter is placed on the grid as a widget; the others sit in the toolbar
            item(
              { kind: "filter", filterId: when.id, control: "date_range" },
              { x: 3, y: 6, w: 6, h: 2 },
            ),
          ],
        },
      ],
      { filters: [region, when, size] },
    );
    await seedProject(api, project.id, [ds], [a, b], [board]);
    await openDashboards(page);
    await expect(kpiValue(page, "Total units")).toHaveText(fmt(sumUnits(SALES)));

    // dropdown: North only, on both charts and the KPI
    await page.getByRole("button", { name: "Region filter" }).click();
    await page.getByRole("checkbox", { name: /^North/ }).click();
    const north = inRegions(["North"]);
    await expect(kpiValue(page, "Total units")).toHaveText(fmt(sumUnits(north)));
    await expect.poll(() => chartMap(page, "Units by region")).toEqual({ North: sumUnits(north) });
    await expect.poll(() => chartMap(page, "Units by product")).toEqual(by("product", north));
    await page.getByRole("button", { name: "Region filter" }).click();
    await page.getByRole("button", { name: "Clear", exact: true }).click();
    await page.keyboard.press("Escape");
    await expect(kpiValue(page, "Total units")).toHaveText(fmt(sumUnits(SALES)));

    // date range (a filter widget on the grid): the middle of the data's own date range
    const dates = SALES.map((r) => r.date).sort();
    const from = dates[Math.floor(dates.length * 0.3)] ?? "";
    const to = dates[Math.floor(dates.length * 0.7)] ?? "";
    // one end at a time: the filter commits after a short pause (see the date-input bug below)
    await page.getByLabel("Order date from").fill(from);
    const fromOnly = SALES.filter((r) => r.date >= from);
    await expect(kpiValue(page, "Total units")).toHaveText(fmt(sumUnits(fromOnly)));
    await page.getByLabel("Order date to").fill(to);
    const windowed = SALES.filter((r) => r.date >= from && r.date <= to);
    expect(windowed.length).toBeGreaterThan(0);
    await expect(kpiValue(page, "Total units")).toHaveText(fmt(sumUnits(windowed)));
    await expect.poll(() => chartMap(page, "Units by region")).toEqual(by("region", windowed));
    await expect.poll(() => chartMap(page, "Units by product")).toEqual(by("product", windowed));
    await expect(activeFilters(page)).toHaveCount(0); // a filter control is not a chart selection

    // slider on top of the date range: the maximum handle moves down
    const slider = page.getByRole("slider", { name: "Units maximum" });
    const top = Math.max(...SALES.map((r) => r.units));
    const low = Math.min(...SALES.map((r) => r.units));
    await slider.fill(String(low + (top - low) * 0.5));
    const label = page.getByText(/^[\d.,]+ to [\d.,]+$/).last();
    await expect(label).toBeVisible();
    const hi = Number(((await label.textContent()) ?? "").split(" to ")[1]?.replace(/,/g, ""));
    expect(hi).toBeLessThan(top);
    const narrowed = windowed.filter((r) => r.units <= hi);
    expect(narrowed.length).toBeLessThan(windowed.length);
    await expect(kpiValue(page, "Total units")).toHaveText(fmt(sumUnits(narrowed)));
    await expect.poll(() => chartMap(page, "Units by product")).toEqual(by("product", narrowed));
    await shot(page, "dashboard-6-filters");
  });

  test("a global date filter added in the dialog follows the viewer to another dashboard", async ({
    page,
    api,
    project,
  }) => {
    const ds = await importSales(api, "glob");
    const source = { kind: "dataset", id: ds.id } as const;
    const a = barChart(source, { name: "Units by region", x: "region", y: "units" });
    const board = (name: string) =>
      dashboard(name, [
        {
          name: "Tab 1",
          items: [
            chartItem(a, { x: 0, y: 0, w: 6, h: 6 }),
            kpiItem("Total units", source, { x: 6, y: 0, w: 3, h: 3 }),
          ],
        },
      ]);
    const first = board("First");
    const second = board("Second");
    await seedProject(api, project.id, [ds], [a], [first, second]);
    await openDashboards(page, first.id);

    await page.getByRole("button", { name: "Global filters", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Global filters" });
    await dialog.getByRole("textbox", { name: "Label" }).fill("Order date");
    await dialog.getByRole("textbox", { name: "Date field" }).fill("order_date");
    await dialog.getByRole("button", { name: "Add", exact: true }).click();
    await dialog.getByRole("button", { name: "Done", exact: true }).click();

    const dates = SALES.map((r) => r.date).sort();
    const from = dates[Math.floor(dates.length * 0.4)] ?? "";
    const windowed = SALES.filter((r) => r.date >= from);
    await page.getByLabel("Order date from").fill(from);
    await expect(kpiValue(page, "Total units")).toHaveText(fmt(sumUnits(windowed)));

    await page.getByRole("combobox", { name: "Dashboard" }).selectOption({ label: "Second" });
    await expect(page).toHaveURL(new RegExp(`/dashboards/${second.id}$`));
    await expect(page.getByLabel("Order date from")).toHaveValue(from);
    await expect(kpiValue(page, "Total units")).toHaveText(fmt(sumUnits(windowed)));
  });

  // -- share, embed -----------------------------------------------------------------------------

  test("share and embed links render the dashboard read-only for an anonymous visitor", async ({
    page,
    browser,
    api,
    project,
  }) => {
    const ds = await importSales(api, "share");
    const source = { kind: "dataset", id: ds.id } as const;
    const a = barChart(source, { name: "Units by region", x: "region", y: "units" });
    const board = dashboard("Shared board", [
      {
        name: "Tab 1",
        items: [
          chartItem(a, { x: 0, y: 0, w: 7, h: 6 }),
          kpiItem("Total units", source, { x: 7, y: 0, w: 3, h: 3 }),
          item({ kind: "text", markdown: "Public note" }, { x: 7, y: 3, w: 4, h: 2 }),
        ],
      },
    ]);
    await seedProject(api, project.id, [ds], [a], [board]);
    await openDashboards(page);

    await page.getByRole("button", { name: "Share", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: /Share "Shared board"/ });
    await dialog.getByRole("button", { name: "Create link", exact: true }).click();
    const shareUrl = await dialog.getByRole("textbox", { name: "Share URL" }).inputValue();
    const snippet = await dialog.getByRole("textbox", { name: "Embed snippet" }).inputValue();
    expect(shareUrl).toMatch(/\/share\/[\w-]{20,}$/);
    expect(snippet).toMatch(/^<iframe /);
    const embedUrl = /src="([^"]+)"/.exec(snippet)?.[1] ?? "";
    expect(embedUrl).toMatch(/\/embed\/[\w-]{20,}$/);
    await shot(page, "dashboard-7-share-dialog");

    // a fresh browser context has none of this app's storage or cookies
    const visitor = await browser.newContext();
    try {
      const guest = await visitor.newPage();
      await guest.goto(shareUrl);
      expect(await guest.evaluate(() => localStorage.getItem("viz.lastProject"))).toBeNull();
      await expect(guest.getByRole("heading", { level: 1, name: "Shared board" })).toBeVisible();
      await expect(kpiValue(guest, "Total units")).toHaveText(fmt(sumUnits(SALES)));
      await expect(widget(guest, "Text")).toContainText("Public note");
      expect(await chartMap(guest, "Units by region")).toEqual(by("region"));
      // read-only: no editing, no app navigation
      for (const name of ["Edit", "Add widget", "New dashboard", "Share", "Delete dashboard"]) {
        await expect(guest.getByRole("button", { name, exact: true })).toHaveCount(0);
      }
      await expect(guest.getByRole("navigation", { name: "Primary" })).toHaveCount(0);
      await expect(guest.getByRole("button", { name: "Print", exact: true })).toBeVisible();
      await shot(guest, "dashboard-8-share-view");

      // the embed link, opened directly: no page chrome
      await guest.goto(embedUrl);
      await expect(kpiValue(guest, "Total units")).toHaveText(fmt(sumUnits(SALES)));
      await expect(guest.getByRole("heading", { level: 1 })).toHaveCount(0);
      await expect(guest.getByRole("button", { name: "Print", exact: true })).toHaveCount(0);

      // and the copied iframe snippet, inside a page that is not this app
      // (a stand-in host page, served from a route so the iframe has a real http parent)
      const host = new URL(embedUrl).origin;
      await guest.route(`${host}/e2e-embed-host`, (route) =>
        route.fulfill({
          contentType: "text/html",
          body: `<!doctype html><title>Host</title><body style="margin:0">${snippet}</body>`,
        }),
      );
      await guest.goto(`${host}/e2e-embed-host`);
      const frame = guest.frameLocator("iframe");
      await expect(frame.locator("p[aria-live=polite]")).toHaveText(fmt(sumUnits(SALES)));
      await expect(frame.getByRole("region", { name: "Text" })).toContainText("Public note");
      await expect(frame.getByRole("button", { name: "Print", exact: true })).toHaveCount(0);
    } finally {
      await visitor.close();
    }
  });

  // DELETE /api/share/{token} answers 204 with no body; the dialog must still refresh and drop the
  // link. The server-side half (the link stops working) is asserted before the UI half.
  test("revoking a link kills it and the dialog says so", async ({
    page,
    browser,
    api,
    project,
  }) => {
    const ds = await importSales(api, "revoke");
    const a = barChart({ kind: "dataset", id: ds.id }, { name: "R", x: "region", y: "units" });
    const board = dashboard("Revocable", [
      { name: "Tab 1", items: [chartItem(a, { x: 0, y: 0, w: 6, h: 6 })] },
    ]);
    await seedProject(api, project.id, [ds], [a], [board]);
    await openDashboards(page);
    await page.getByRole("button", { name: "Share", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: /Share "Revocable"/ });
    await dialog.getByRole("button", { name: "Create link" }).click();
    const shareUrl = await dialog.getByRole("textbox", { name: "Share URL" }).inputValue();
    await dialog.getByRole("button", { name: "Revoke", exact: true }).click();

    const visitor = await browser.newContext();
    try {
      const guest = await visitor.newPage();
      await guest.goto(shareUrl);
      await expect(guest.getByRole("heading", { name: "This link isn't available" })).toBeVisible();
    } finally {
      await visitor.close();
    }
    await expect(dialog.getByText("No links yet.")).toBeVisible({ timeout: 3_000 });
  });

  // The date-range input keeps what was picked while the debounced commit is pending, so the
  // other end of the range can be filled straight away.
  test("a picked date stays in the date-range input while the filter commits", async ({
    page,
    api,
    project,
  }) => {
    const ds = await importSales(api, "datebug");
    const source = { kind: "dataset", id: ds.id } as const;
    const a = barChart(source, { name: "Units by region", x: "region", y: "units" });
    const when = {
      id: "f-date",
      label: "Order date",
      field: "order_date",
      fieldMap: {},
      predicate: null,
      control: "date_range",
    } as const;
    await seedProject(
      api,
      project.id,
      [ds],
      [a],
      [
        dashboard("Dates", [{ name: "Tab 1", items: [chartItem(a, { x: 0, y: 0, w: 6, h: 6 })] }], {
          filters: [when],
        }),
      ],
    );
    await openDashboards(page);
    const from = page.getByLabel("Order date from");
    const to = page.getByLabel("Order date to");
    await from.fill("2024-03-01");
    expect(await from.inputValue(), "no blink back to empty").toBe("2024-03-01");
    await to.fill("2024-04-01");
    await page.waitForTimeout(800);
    await expect(from).toHaveValue("2024-03-01");
    await expect(to).toHaveValue("2024-04-01");
  });

  // -- report and print -------------------------------------------------------------------------

  test("report downloads and the print layout hides the app chrome", async ({
    page,
    api,
    project,
  }) => {
    const ds = await importSales(api, "report");
    const source = { kind: "dataset", id: ds.id } as const;
    const a = barChart(source, { name: "Units by region", x: "region", y: "units" });
    const board = dashboard("Report board", [
      {
        name: "Tab 1",
        items: [
          chartItem(a, { x: 0, y: 0, w: 7, h: 6 }),
          kpiItem("Total units", source, { x: 7, y: 0, w: 3, h: 3 }),
        ],
      },
    ]);
    await seedProject(api, project.id, [ds], [a], [board]);
    await openDashboards(page);
    await expect(kpiValue(page, "Total units")).toHaveText(fmt(sumUnits(SALES)));
    await expect(widget(page, "Units by region").locator("canvas").first()).toBeVisible();

    // HTML report
    const html = page.waitForEvent("download", { timeout: 45_000 });
    await page.getByRole("button", { name: "Report", exact: true }).click();
    await page.getByRole("menuitem", { name: "HTML report" }).click();
    const htmlFile = await html;
    expect(htmlFile.suggestedFilename()).toBe("Report_board.html");
    const text = readFileSync((await htmlFile.path()) ?? "", "utf8");
    expect(text).toContain("Report board");
    expect(text).toContain("<img"); // the chart as a captured image
    expect(text).toContain(fmt(sumUnits(SALES))); // the KPI's number

    // PDF report
    const pdf = page.waitForEvent("download", { timeout: 60_000 });
    await page.getByRole("button", { name: "Report", exact: true }).click();
    await page.getByRole("menuitem", { name: "PDF report" }).click();
    const pdfFile = await pdf;
    expect(pdfFile.suggestedFilename()).toBe("Report_board.pdf");
    expect(
      readFileSync((await pdfFile.path()) ?? "")
        .subarray(0, 5)
        .toString(),
    ).toBe("%PDF-");

    // print layout: chrome and edit controls vanish, the widgets stay
    await page.emulateMedia({ media: "print" });
    await expect(page.getByRole("navigation", { name: "Primary" })).toBeHidden();
    await expect(page.getByRole("button", { name: "Share", exact: true })).toBeHidden();
    await expect(page.getByRole("button", { name: "Print", exact: true })).toBeHidden();
    await expect(widget(page, "Units by region")).toBeVisible();
    await expect(kpiValue(page, "Total units")).toBeVisible();
    await shot(page, "dashboard-9-print");
  });
});
