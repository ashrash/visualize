// @vitest-environment node
/**
 * Cross-filter refresh benchmark (CLAUDE.md section 6: a 6-chart refresh within 1 s on the 10M-row
 * fixture). It runs the real dashboard code path, minus drawing: the bus publishes a selection,
 * `filtersForChart` computes each chart's filters (own selection excluded), and the real
 * `fetchChartData` sends the six requests and decodes the Arrow responses.
 *
 * Skipped unless pointed at a server that already has the fixture imported:
 *
 *   VIZ_BENCH_URL=http://127.0.0.1:8765 VIZ_BENCH_DATASET=<dataset id> \
 *     npm --prefix frontend run test -- src/dashboard/bench
 *
 * Optional: VIZ_BENCH_ROUNDS (default 20), VIZ_BENCH_BUDGET_MS (default 1000).
 */
import { describe, expect, it, vi } from "vitest";
import type { ChartSpec, Predicate } from "../../api/types";
import { fetchChartData } from "../../charts/core/data";
import { EMPTY_BUS, filtersForChart, publish } from "../bus";
import type { Dashboard } from "../types";

const URL_BASE = process.env.VIZ_BENCH_URL;
const DATASET = process.env.VIZ_BENCH_DATASET;
const ROUNDS = Number(process.env.VIZ_BENCH_ROUNDS ?? 20);
const BUDGET_MS = Number(process.env.VIZ_BENCH_BUDGET_MS ?? 1000);
const enabled = Boolean(URL_BASE && DATASET);

type Enc = NonNullable<ChartSpec["encodings"]>[string][number];
const enc = (field: string | null, agg?: Enc["agg"], timeUnit?: Enc["timeUnit"]): Enc => ({
  field,
  ...(agg ? { agg } : {}),
  ...(timeUnit ? { timeUnit } : {}),
});

const chart = (id: string, plotType: string, encodings: ChartSpec["encodings"]): ChartSpec =>
  ({
    id,
    name: id,
    plotType,
    source: { kind: "dataset", id: DATASET ?? "" },
    encodings,
    interactions: {
      zoom: true,
      brush: false,
      clickFilter: true,
      drillHierarchy: [],
      linkGroup: "g",
    },
  }) as ChartSpec;

// The same six charts scripts/bench.py refreshes, so the two numbers are comparable.
const CHARTS: ChartSpec[] = [
  chart("c0", "bar", { x: [enc("region")], y: [enc("sales", "sum")] }),
  chart("c1", "bar", { x: [enc("category")], y: [enc(null, "count")] }),
  chart("c2", "line", { x: [enc("order_date", undefined, "month")], y: [enc("sales", "sum")] }),
  chart("c3", "pie", { x: [enc("channel")], y: [enc("units", "sum")] }),
  chart("c4", "bar", { x: [enc("segment")], y: [enc("sales", "mean")] }),
  chart("c5", "bar", { x: [enc("product")], y: [enc("sales", "sum")] }),
];
const DASHBOARD = { id: "d", name: "bench", tabs: [], filters: [], linkGroups: [] } as Dashboard;

function refresh(round: number, signal?: AbortSignal) {
  // A seventh widget (a clicked chart) publishes the selection; the six charts all receive it.
  const selection: Predicate = { op: "eq", field: "country", value: `Country ${(round % 40) + 1}` };
  const bus = publish(EMPTY_BUS, "g", "clicked", {
    sourceId: DATASET ?? "",
    predicates: [selection],
  });
  return Promise.all(
    CHARTS.map((spec) =>
      fetchChartData(
        {
          spec,
          filters: filtersForChart({
            dashboard: DASHBOARD,
            spec,
            widgetId: spec.id,
            bus,
            filterValues: {},
            globalFilters: [],
            globalValues: {},
            drill: [],
            columnsOf: () => undefined,
          }),
          viewport: { widthPx: 600, heightPx: 300 },
        } as never,
        signal,
      ),
    ),
  );
}

const percentile = (sorted: number[], p: number) =>
  sorted[Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1)] ?? Number.NaN;

describe.skipIf(!enabled)("6-chart cross-filter refresh against a live server", () => {
  it(`finishes within ${BUDGET_MS} ms (p95 over ${ROUNDS} rounds)`, async () => {
    const realFetch = globalThis.fetch;
    vi.stubGlobal("fetch", (input: string, init?: RequestInit) =>
      realFetch(new URL(input, URL_BASE), init),
    );
    await refresh(1000); // warm-up: connection setup, first-query planning
    const times: number[] = [];
    let cached = 0;
    for (let round = 0; round < ROUNDS; round++) {
      const started = performance.now();
      const results = await refresh(round);
      times.push(performance.now() - started);
      cached += results.filter((r) => r.meta.cached).length;
      expect(results).toHaveLength(6);
      for (const r of results) expect(r.rowCount).toBeGreaterThan(0);
    }
    const sorted = [...times].sort((a, b) => a - b);
    const report = {
      rounds: ROUNDS,
      p50: Math.round(percentile(sorted, 50)),
      p95: Math.round(percentile(sorted, 95)),
      max: Math.round(sorted.at(-1) ?? 0),
      min: Math.round(sorted[0] ?? 0),
      cachedResponses: cached,
    };
    console.log(`REFRESH_BENCH ${JSON.stringify(report)}`);
    expect(cached).toBe(0);
    expect(report.p95).toBeLessThanOrEqual(BUDGET_MS);
  }, 120_000);

  it("a newer interaction aborts the in-flight round, and the newer round still completes", async () => {
    const realFetch = globalThis.fetch;
    vi.stubGlobal("fetch", (input: string, init?: RequestInit) =>
      realFetch(new URL(input, URL_BASE), init),
    );
    const first = new AbortController();
    const stale = refresh(2001, first.signal).then(
      () => "completed",
      (error: unknown) => (error instanceof Error ? error.name : "rejected"),
    );
    first.abort(); // the user clicked again before the first round came back
    const started = performance.now();
    const fresh = await refresh(2002);
    const elapsed = performance.now() - started;
    expect(await stale).toBe("AbortError");
    expect(fresh).toHaveLength(6);
    console.log(`REFRESH_BENCH_ABORT ${JSON.stringify({ newerRoundMs: Math.round(elapsed) })}`);
    expect(elapsed).toBeLessThanOrEqual(BUDGET_MS * 2);
  }, 60_000);
});
