import { useQueryClient } from "@tanstack/react-query";
import { type RefObject, useCallback, useState } from "react";
import { useExportSink } from "../export/sinkContext";
import { describePredicate } from "./describe";
import { buildReportHtml, buildReportPdf, captureTiles, type ReportModel } from "./report";
import { useDashboardRuntime } from "./runtimeStore";
import type { Dashboard } from "./types";

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const frames = (n: number) =>
  new Promise<void>((resolve) => {
    const step = (left: number) =>
      left <= 0 ? resolve() : requestAnimationFrame(() => step(left - 1));
    step(n);
  });

/** Wait for the tab to finish loading (no chart request in flight) and animations to settle. */
async function settled(isFetching: () => number, timeoutMs = 20_000) {
  await frames(2);
  const start = Date.now();
  while (isFetching() > 0 && Date.now() - start < timeoutMs) await sleep(100);
  // ECharts animates new data in; capture after the animation.
  await sleep(700);
  await frames(2);
}

export type ReportFormat = "html" | "pdf";

/**
 * Build a report from the dashboard as shown: each tab is opened in turn, waited for, and
 * captured (charts as PNG, text, KPI lines). The viewer's own tab is restored afterwards.
 */
export function useReport(args: {
  root: RefObject<HTMLElement | null>;
  dashboard: Dashboard | undefined;
  projectName: string;
}) {
  const { root, dashboard, projectName } = args;
  const client = useQueryClient();
  const sink = useExportSink();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(
    async (format: ReportFormat) => {
      const container = root.current;
      if (!container || !dashboard) return;
      setBusy(true);
      setError(null);
      const runtime = useDashboardRuntime.getState();
      const original = runtime.activeTabId;
      try {
        const background =
          getComputedStyle(document.documentElement).getPropertyValue("--viz-bg").trim() ||
          "#ffffff";
        const tabs: ReportModel["tabs"] = [];
        for (const tab of dashboard.tabs ?? []) {
          runtime.setTab(tab.id);
          await settled(() => client.isFetching());
          const items = (tab.items ?? []).map((item) => ({
            i: item.i,
            x: item.x,
            y: item.y,
            w: item.w,
            h: item.h,
            kind: item.widget.kind,
          }));
          tabs.push({ name: tab.name, tiles: captureTiles(container, items, background) });
        }
        const live = useDashboardRuntime.getState();
        const model: ReportModel = {
          title: dashboard.name,
          subtitle: projectName,
          generatedAt: new Date().toLocaleString(),
          filters: [
            ...live.drill.map(describePredicate),
            ...Object.values(live.bus).flatMap((group) =>
              Object.values(group).flatMap((s) => s.predicates.map(describePredicate)),
            ),
          ],
          tabs,
        };
        const base = dashboard.name.replace(/[^\w.-]+/g, "_") || "dashboard";
        if (format === "html") {
          await sink.save(
            new Blob([buildReportHtml(model)], { type: "text/html" }),
            `${base}.html`,
          );
        } else {
          await sink.save(await buildReportPdf(model), `${base}.pdf`);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : "The report could not be created.");
      } finally {
        useDashboardRuntime.getState().setTab(original);
        setBusy(false);
      }
    },
    [root, dashboard, projectName, client, sink],
  );

  return { run, busy, error };
}
