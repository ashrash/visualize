import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { deflateSync } from "node:zlib";
import { expect, type Locator, type Page } from "@playwright/test";
import type { components } from "../../src/api/schema";
import type { ChartSpec, Dataset, Project } from "../../src/api/types";
import type { Api } from "./api";
import { sample } from "./env";

/**
 * Dashboard e2e helpers. Setup goes through the REST API (a seeded project document); every
 * assertion about behaviour goes through the UI. ECharts draws on a canvas, so rendered data is
 * checked through the widget's "View as table" fallback, a KPI's number, the active-filter chips
 * and the data grid, never through axis text.
 */
type Schemas = components["schemas"];
export type Dashboard = Schemas["Dashboard"];
export type LayoutItem = Schemas["LayoutItem"];
export type DashboardFilter = Schemas["DashboardFilter"];
type Widget = LayoutItem["widget"];

// -- data facts ------------------------------------------------------------------------------

/** samples/sales.csv: order_id,order_date,region,product,channel,units,unit_price,sales */
export interface SaleRow {
  date: string;
  region: string;
  product: string;
  channel: string;
  units: number;
}

export const SALES: SaleRow[] = readFileSync(sample("sales.csv"), "utf8")
  .split(/\r?\n/)
  .filter(Boolean)
  .slice(1)
  .map((line) => line.split(","))
  .map((c) => ({
    date: c[1] ?? "",
    region: c[2] ?? "",
    product: c[3] ?? "",
    channel: c[4] ?? "",
    units: Number(c[5]),
  }));

export const sumUnits = (rows: SaleRow[]): number => rows.reduce((s, r) => s + r.units, 0);

/** What a KPI with format "number:0" shows (en-US, as the e2e browser runs). */
export const fmt = (n: number): string => n.toLocaleString("en-US", { maximumFractionDigits: 0 });

// -- documents -------------------------------------------------------------------------------

export const uid = (): string => randomUUID();

export function barChart(
  source: { kind: "dataset"; id: string },
  o: {
    name: string;
    x: string;
    y: string;
    agg?: "sum" | "mean" | "count";
    brush?: boolean;
    drill?: string[];
  },
): ChartSpec {
  return {
    id: uid(),
    name: o.name,
    plotType: "bar",
    source,
    encodings: { x: [{ field: o.x }], y: [{ field: o.y, agg: o.agg ?? "sum" }] },
    params: {},
    style: {},
    interactions: {
      zoom: true,
      brush: o.brush ?? false,
      clickFilter: true,
      drillHierarchy: o.drill ?? [],
    },
    filters: [],
  } as unknown as ChartSpec;
}

export const item = (
  widget: Widget,
  pos: { x: number; y: number; w: number; h: number },
): LayoutItem => ({ i: uid(), ...pos, widget });

export const chartItem = (chart: ChartSpec, pos: { x: number; y: number; w: number; h: number }) =>
  item({ kind: "chart", chartId: chart.id }, pos);

export const kpiItem = (
  title: string,
  source: { kind: "dataset"; id: string },
  pos: { x: number; y: number; w: number; h: number },
  measure: { field?: string; agg: "sum" | "count" } = { field: "units", agg: "sum" },
) =>
  item(
    {
      kind: "kpi",
      title,
      source,
      measure,
      time: null,
      comparison: "none",
      target: null,
      format: "number:0",
    } as Widget,
    pos,
  );

export function dashboard(
  name: string,
  tabs: { name: string; items: LayoutItem[] }[],
  extra: Partial<Dashboard> = {},
): Dashboard {
  return {
    id: uid(),
    name,
    tabs: tabs.map((t) => ({ id: uid(), name: t.name, items: t.items })),
    filters: [],
    linkGroups: [],
    ...extra,
  } as Dashboard;
}

/** Every chart in one cross-filter group. */
export const linkAll = (charts: ChartSpec[]) => ({
  id: "all",
  chartIds: charts.map((c) => c.id),
});

/** Write charts and dashboards into the project (the app loads them on open). */
export async function seedProject(
  api: Api,
  projectId: string,
  datasets: Dataset[],
  charts: ChartSpec[],
  dashboards: Dashboard[],
): Promise<Project> {
  const current = await api.project(projectId);
  return api.saveProject({
    ...current,
    datasetIds: datasets.map((d) => d.id),
    charts,
    dashboards,
  });
}

/**
 * A dashboard with one of everything (two charts, KPI, text, image, a filter widget, toolbar
 * filters, two tabs, one link group), written into the project. For a11y and share tests.
 */
export async function seedRichBoard(
  api: Api,
  projectId: string,
  datasetName: string,
): Promise<{ dataset: Dataset; board: Dashboard; charts: ChartSpec[] }> {
  const dataset = await api.importPath(sample("sales.csv"), { name: datasetName });
  const source = { kind: "dataset", id: dataset.id } as const;
  const a = barChart(source, { name: "Units by region", x: "region", y: "units", brush: true });
  const b = barChart(source, { name: "Units by product", x: "product", y: "units" });
  const assetId = await api.uploadAsset(makePng(), "logo.png");
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
  const board = dashboard(
    "Rich board",
    [
      {
        name: "Overview",
        items: [
          chartItem(a, { x: 0, y: 0, w: 6, h: 6 }),
          chartItem(b, { x: 6, y: 0, w: 6, h: 6 }),
          kpiItem("Total units", source, { x: 0, y: 6, w: 3, h: 3 }),
          item(
            { kind: "text", markdown: "## Notes\n\nA **bold** claim." },
            { x: 3, y: 6, w: 4, h: 3 },
          ),
          item(
            { kind: "image", assetId, alt: "Company logo", fit: "contain" },
            { x: 7, y: 6, w: 3, h: 3 },
          ),
          item(
            { kind: "filter", filterId: when.id, control: "date_range" },
            { x: 0, y: 9, w: 6, h: 2 },
          ),
        ],
      },
      {
        name: "Details",
        items: [item({ kind: "text", markdown: "Second tab" }, { x: 0, y: 0, w: 4, h: 2 })],
      },
    ],
    { filters: [region, when], linkGroups: [linkAll([a, b])] },
  );
  await seedProject(api, projectId, [dataset], [a, b], [board]);
  return { dataset, board, charts: [a, b] };
}

// -- UI --------------------------------------------------------------------------------------

/** A widget's frame (every widget is a <section aria-label=title>). */
export const widget = (page: Page, title: string): Locator =>
  page.getByRole("region", { name: title, exact: true });

/** The big number of a KPI widget, as text. */
export const kpiValue = (page: Page, title: string): Locator =>
  widget(page, title).locator("[aria-live=polite]");

export const activeFilters = (page: Page): Locator =>
  page.getByRole("status", { name: "Active filters" });

/** Open the dashboard screen through the nav (client-side, so runtime state is kept). */
export async function openDashboards(page: Page, id?: string): Promise<void> {
  await page.goto(id ? `/dashboards/${id}` : "/dashboards");
  await expect(
    page.getByRole("banner").getByRole("button", { name: /project menu$/i }),
  ).not.toContainText("Loading");
}

/** Switch a chart widget to its table view; returns the data rows as text cells. */
export async function chartRows(page: Page, title: string): Promise<string[][]> {
  const frame = widget(page, title);
  const toggle = frame.getByRole("button", { name: "View as table" });
  if ((await toggle.getAttribute("aria-pressed")) !== "true") await toggle.click();
  const table = frame.getByRole("table");
  await expect(table).toBeVisible();
  return table
    .locator("tbody tr")
    .evaluateAll((rows) =>
      rows.map((r) => [...r.querySelectorAll("td")].map((c) => (c.textContent ?? "").trim())),
    );
}

/** Rows as {label -> number} (first cell is the category, the second the measure). */
export async function chartMap(page: Page, title: string): Promise<Record<string, number>> {
  const rows = await chartRows(page, title);
  return Object.fromEntries(rows.map((r) => [r[0] ?? "", Number((r[1] ?? "").replace(/,/g, ""))]));
}

/** The canvas box of a chart widget (it must be showing its chart, not the table). */
async function canvasBox(frame: Locator) {
  const canvas = frame.locator("canvas").first();
  await expect(canvas).toBeVisible();
  // The entry animation moves bars; wait for it before pointing at one.
  await frame.page().waitForTimeout(900);
  const box = await canvas.boundingBox();
  if (!box) throw new Error("chart canvas has no box");
  return box;
}

/**
 * Click the bar of the `index`-th category (of `count`). The plot area's exact geometry is not
 * known, so the click walks up from just above the x axis until the dashboard registers a
 * selection (an active-filter chip). Returns the chip text of the selection that was made.
 */
export async function clickBar(
  page: Page,
  title: string,
  index: number,
  count: number,
): Promise<string> {
  const frame = widget(page, title);
  const box = await canvasBox(frame);
  const left = box.x + 56;
  const band = (box.width - 56 - 24) / count;
  const x = left + band * (index + 0.5);
  const chip = activeFilters(page).locator("span.rounded-full").first();
  for (let up = 44; up < box.height * 0.55; up += 14) {
    await page.mouse.click(x, box.y + box.height - up);
    try {
      await expect(chip).toBeVisible({ timeout: 700 });
      return await chipLabel(chip);
    } catch {
      // nothing under the pointer; try higher
    }
  }
  throw new Error(`no bar found for category ${index} of ${count} in "${title}"`);
}

/** Brush across categories `from`..`to` (inclusive) with a horizontal drag. */
export async function brushBars(
  page: Page,
  title: string,
  from: number,
  to: number,
  count: number,
): Promise<void> {
  const frame = widget(page, title);
  const box = await canvasBox(frame);
  const left = box.x + 56;
  const band = (box.width - 56 - 24) / count;
  const y = box.y + box.height * 0.45;
  await page.mouse.move(left + band * (from + 0.3), y);
  await page.mouse.down();
  await page.mouse.move(left + band * (from + 0.6), y, { steps: 4 });
  await page.mouse.move(left + band * (to + 0.7), y, { steps: 8 });
  await page.mouse.up();
}

/**
 * Drag from the centre of an element by (dx, dy) in small timed steps, which is what
 * react-grid-layout needs to register a move or a resize (one big jump is dropped).
 */
export async function dragBy(page: Page, target: Locator, dx: number, dy: number): Promise<void> {
  const box = await target.boundingBox();
  if (!box) throw new Error("drag target has no box");
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  const steps = 12;
  for (let i = 1; i <= steps; i++) {
    await page.mouse.move(x + (dx * i) / steps, y + (dy * i) / steps);
    await page.waitForTimeout(30);
  }
  await page.mouse.up();
  await page.waitForTimeout(300);
}

/** A chip's label without its remove button ("x"). */
export const chipLabel = async (chip: Locator): Promise<string> =>
  (await chip.evaluate((el) => el.firstChild?.textContent ?? "")).trim();

/** Category names in a chip such as "region: East, North" -> ["East", "North"]. */
export const chipValues = (chip: string): string[] =>
  chip
    .replace(/^[^:=]+[:=]\s*/, "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

/** A valid 8x8 solid-colour PNG, built here so the upload is a real image. */
export function makePng(rgb: [number, number, number] = [0, 114, 178]): Buffer {
  const crcTable = Array.from({ length: 256 }, (_, n) => {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    return c >>> 0;
  });
  const crc = (buf: Buffer) => {
    let c = 0xffffffff;
    for (const b of buf) c = (crcTable[(c ^ b) & 0xff] ?? 0) ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  };
  const chunk = (type: string, data: Buffer) => {
    const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
    const out = Buffer.alloc(body.length + 8);
    out.writeUInt32BE(data.length, 0);
    body.copy(out, 4);
    out.writeUInt32BE(crc(body), body.length + 4);
    return out;
  };
  const size = 8;
  const header = Buffer.alloc(13);
  header.writeUInt32BE(size, 0);
  header.writeUInt32BE(size, 4);
  header.set([8, 2, 0, 0, 0], 8); // 8-bit RGB, no interlace
  const row = Buffer.concat([Buffer.from([0]), Buffer.from(Array(size).fill(rgb).flat())]);
  const raw = Buffer.concat(Array.from({ length: size }, () => row));
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}
