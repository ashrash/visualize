import { readdirSync, readFileSync, statSync } from "node:fs";
import type { Column, ImportOptions } from "../src/api/types";
import { expect, test } from "./fixtures";
import { SAMPLES_DIR, sample } from "./helpers/env";
import { unique } from "./helpers/shots";

/**
 * Every committed sample imports cleanly through the real import endpoints (in place by path, and
 * as an upload for the formats that need special handling), with the right row count, column
 * names and column semantics. API-only: no browser. Wave 2 plot agents rely on these files, so a
 * regression in an importer shows up here first.
 */

interface Case {
  file: string;
  options?: ImportOptions;
  /** Dataset rows; counted from the file when omitted (CSV/TSV/NDJSON lines, JSON array length). */
  rows?: number;
  /** Column names that must exist (CSV/TSV also check the whole header against the file). */
  columns?: string[];
  temporal?: string[];
  quantitative?: string[];
  nominal?: string[];
  /** Columns detected as geographic coordinates (by name: lat/lon). */
  geo?: string[];
  /** Columns with at least one null. */
  nulls?: string[];
  /** Columns that need at least this many distinct values (approx). */
  manyDistinct?: Record<string, number>;
  /** Also upload the file instead of importing by path (multipart + staging). */
  upload?: boolean;
}

const CASES: Case[] = [
  {
    file: "sales.csv",
    rows: 500,
    columns: [
      "order_id",
      "order_date",
      "region",
      "product",
      "channel",
      "units",
      "unit_price",
      "sales",
    ],
    temporal: ["order_date"],
    quantitative: ["units", "unit_price", "sales"],
    nominal: ["region", "product", "channel"],
  },
  {
    // Request log: per-row UTC offsets must come out as one time-zone-aware timestamp column.
    file: "web_traffic.csv",
    temporal: ["timestamp"],
    nulls: ["revenue"],
    quantitative: ["load_ms", "size_kb"],
    manyDistinct: { page: 300 },
  },
  {
    file: "customers.tsv",
    upload: true,
    temporal: ["signup_date", "last_purchase"],
    nulls: ["age", "income", "credit_score", "last_purchase", "satisfaction", "notes"],
    manyDistinct: { company: 1000, city: 250, customer_id: 2500 },
  },
  {
    // BOM + CJK, accents and symbols in headers; nulls everywhere.
    file: "survey_unicode.csv",
    upload: true,
    columns: [
      "名前",
      "年齢",
      "Größe (cm)",
      "Café visits / week",
      "prix €",
      "País",
      "Satisfaction ★",
      "Réponse",
    ],
    nulls: ["Größe (cm)", "Café visits / week", "prix €", "Satisfaction ★", "Réponse"],
  },
  {
    // Windows-1252, not UTF-8: must be transcoded, not mangled.
    file: "ventes_europe_cp1252.csv",
    upload: true,
    rows: 400,
    columns: ["Commande", "Date", "Région", "Ville", "Produit", "Quantité", "Prix (€)"],
    temporal: ["Date"],
  },
  {
    file: "products.json",
    upload: true,
    rows: 120,
    columns: [
      "sku",
      "category",
      "price",
      "rating",
      "discontinued",
      "launched",
      "tags",
      "dimensions",
    ],
    temporal: ["launched"],
    nulls: ["rating"],
  },
  { file: "events.ndjson", upload: true, rows: 2000, temporal: ["ts"], nulls: ["value"] },
  {
    file: "stocks_ohlc.csv",
    rows: 2080,
    temporal: ["date"],
    quantitative: ["open", "high", "low", "close", "volume"],
    nominal: ["ticker", "sector"],
  },
  {
    file: "weather_daily.csv",
    temporal: ["date"],
    nominal: ["city"],
    quantitative: ["temp_c", "precip_mm"],
  },
  {
    file: "monthly_demand.csv",
    rows: 168,
    temporal: ["month"],
    quantitative: ["demand", "temp_c"],
  },
  { file: "macro_quarterly.csv", rows: 160, temporal: ["quarter"] },
  {
    file: "survival.csv",
    rows: 600,
    columns: ["time_months", "event", "treatment", "stage"],
    quantitative: ["time_months"],
  },
  {
    file: "panel_event_study.csv",
    rows: 3000,
    columns: ["unit", "year", "outcome", "event_time", "post"],
    nulls: ["treat_year", "event_time"],
  },
  {
    file: "churn.csv",
    rows: 2000,
    quantitative: ["monthly_charges", "score_model_a", "score_model_b"],
  },
  { file: "studies.csv", rows: 16, quantitative: ["effect", "ci_low", "ci_high"] },
  {
    file: "specimens.csv",
    rows: 450,
    nulls: ["sex", "bill_length_mm"],
    nominal: ["species", "island"],
  },
  { file: "points_2d.csv", rows: 6000, quantitative: ["x", "y_linear", "y_cluster"] },
  {
    file: "geo_events.csv",
    rows: 4000,
    temporal: ["event_time"],
    geo: ["lat", "lon"],
    quantitative: ["magnitude"],
  },
  {
    file: "countries.csv",
    columns: ["iso3", "country", "continent", "lat", "lon", "population_m"],
  },
  { file: "us_states.csv", rows: 51, columns: ["state", "usps", "region", "population_m"] },
  {
    file: "cities.csv",
    columns: ["city", "iso3", "lat", "lon", "population_m"],
    geo: ["lat", "lon"],
  },
  { file: "flows.csv", columns: ["source", "target", "value", "layer", "quarter"] },
  { file: "journeys.csv", columns: ["entry", "page", "intent", "outcome", "users"] },
  { file: "tasks_gantt.csv", temporal: ["start", "end"] },
  { file: "sales_hierarchy.csv", columns: ["region", "category", "product", "revenue_k"] },
  { file: "pnl_waterfall.csv", rows: 11 },
  { file: "funnel.csv", rows: 6 },
  { file: "defects.csv", rows: 8 },
];

function expectedRows(file: string): number {
  const path = sample(file);
  const text = readFileSync(path, "utf8");
  if (file.endsWith(".json")) return (JSON.parse(text) as unknown[]).length;
  const lines = text.split(/\r?\n/).filter((l) => l.length > 0).length;
  return file.endsWith(".ndjson") ? lines : lines - 1;
}

function headerOf(file: string): string[] {
  const encoding = file.includes("cp1252") ? "windows-1252" : "utf-8";
  // Node maps "windows-1252" to latin-1 for 0x80..0x9F, so the euro sign (0x80) needs fixing up.
  const text = new TextDecoder(encoding)
    .decode(readFileSync(sample(file)))
    .replace(/^﻿/, "")
    .replace(/\u0080/g, "€");
  const first = text.split(/\r?\n/, 1)[0] ?? "";
  return first.split(file.endsWith(".tsv") ? "\t" : ",");
}

function byName(columns: Column[]): Map<string, Column> {
  return new Map(columns.map((c) => [c.name, c]));
}

test.describe("sample datasets import", () => {
  for (const c of CASES) {
    const modes = c.upload ? (["path", "upload"] as const) : (["path"] as const);
    for (const mode of modes) {
      test(`${c.file} (${mode})`, async ({ api }) => {
        const name = unique(c.file.replace(/\W/g, "_"));
        const dataset =
          mode === "path"
            ? await api.importPath(sample(c.file), { name, options: c.options })
            : await api.uploadFile(sample(c.file), { name, options: c.options });
        try {
          const version = dataset.versions?.at(-1);
          expect(version, "a version was written").toBeDefined();
          if (!version) return;
          expect(version.rowCount).toBe(c.rows ?? expectedRows(c.file));

          const columns = byName(version.columns);
          const names = version.columns.map((x) => x.name);
          if (/\.(csv|tsv)$/.test(c.file)) expect(names).toEqual(headerOf(c.file));
          for (const column of c.columns ?? []) expect(names, `column ${column}`).toContain(column);
          for (const column of c.temporal ?? []) {
            expect(columns.get(column)?.semantic, `${column} is temporal`).toBe("temporal");
          }
          for (const column of c.quantitative ?? []) {
            expect(columns.get(column)?.semantic, `${column} is quantitative`).toBe("quantitative");
          }
          for (const column of c.geo ?? []) {
            expect(columns.get(column)?.semantic, `${column} is geo`).toBe("geo");
          }
          for (const column of c.nominal ?? []) {
            expect(columns.get(column)?.semantic, `${column} is nominal`).toBe("nominal");
          }
          for (const column of c.nulls ?? []) {
            expect(columns.get(column)?.nullCount, `${column} has nulls`).toBeGreaterThan(0);
          }
          for (const [column, min] of Object.entries(c.manyDistinct ?? {})) {
            expect(
              columns.get(column)?.approxDistinct,
              `${column} is high-cardinality`,
            ).toBeGreaterThanOrEqual(min);
          }
        } finally {
          await api.deleteDataset(dataset.id).catch(() => undefined);
        }
      });
    }
  }

  test("cp1252 text keeps its accents after import", async ({ api, request }) => {
    const dataset = await api.uploadFile(sample("ventes_europe_cp1252.csv"), {
      name: unique("ventes"),
    });
    try {
      const response = await request.post(`/api/datasets/${dataset.id}/values`, {
        data: { field: "Produit", limit: 20 },
      });
      expect(response.ok()).toBe(true);
      const values = ((await response.json()) as { values: { value: string }[] }).values.map(
        (v) => v.value,
      );
      expect(values).toEqual(
        expect.arrayContaining(["Café moulu", "Thé vert", "Crème brûlée", "Pain d'épices"]),
      );
      expect(values.some((v) => v.includes("�"))).toBe(false);
    } finally {
      await api.deleteDataset(dataset.id).catch(() => undefined);
    }
  });

  test("quarterly_report.xlsx: both sheets import as their own datasets", async ({ api }) => {
    const revenue = await api.uploadFile(sample("quarterly_report.xlsx"), {
      name: unique("xlsx-revenue"),
      options: { sheet: "Revenue" },
    });
    const headcount = await api.importPath(sample("quarterly_report.xlsx"), {
      name: unique("xlsx-headcount"),
      options: { sheet: "Headcount" },
    });
    try {
      const rev = revenue.versions?.at(-1);
      const head = headcount.versions?.at(-1);
      expect(rev?.rowCount).toBe(48);
      expect(rev?.columns.map((c) => c.name)).toEqual([
        "Period",
        "Region",
        "Revenue",
        "Cost",
        "Margin",
      ]);
      expect(byName(rev?.columns ?? []).get("Period")?.semantic).toBe("temporal");
      expect(byName(rev?.columns ?? []).get("Revenue")?.semantic).toBe("quantitative");
      expect(head?.rowCount).toBe(8);
      expect(head?.columns.map((c) => c.name)).toEqual(["Department", "Q1", "Q2", "Q3", "Q4"]);
    } finally {
      await api.deleteDataset(revenue.id).catch(() => undefined);
      await api.deleteDataset(headcount.id).catch(() => undefined);
    }
  });

  test("every file in samples/ is covered by this test, and the folder stays under 5 MB", () => {
    const files = readdirSync(SAMPLES_DIR).filter((f) => !f.endsWith(".md"));
    const covered = new Set([...CASES.map((c) => c.file), "quarterly_report.xlsx"]);
    expect(files.filter((f) => !covered.has(f))).toEqual([]);
    const total = files.reduce((sum, f) => sum + statSync(sample(f)).size, 0);
    expect(total).toBeLessThan(5 * 1024 * 1024);
  });
});
