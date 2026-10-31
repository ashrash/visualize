import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = path.resolve(here, "..", "..");
/** Per-test results land here; global-teardown merges them into axe-report.{json,md} next to it. */
export const AXE_DIR = path.join(FRONTEND_DIR, "test-results", "axe");

/** CLAUDE.md §9 owners, as used in the report. */
export type Owner = "ui-data" | "ui-charts" | "ui-dashboard";

export interface AxeNodeResult {
  target: string;
  html: string;
  owner: Owner;
  summary: string;
}

export interface AxeViolation {
  id: string;
  impact: string;
  help: string;
  helpUrl: string;
  nodes: AxeNodeResult[];
}

export interface ScreenReport {
  screen: string;
  theme: string;
  url: string;
  violations: AxeViolation[];
  passes: number;
  incomplete: number;
}

/**
 * Which agent should fix a node. A heuristic from where the element sits in the DOM: chart
 * canvas, chart toolbar/export menu and the config panel belong to ui-charts; dashboards (the
 * grid, its toolbar, the share and embed pages, the widget-add, share and global-filter dialogs)
 * to ui-dashboard; everything else in the shell, data screens, dialogs and project menu to ui-data.
 */
const CHARTS_REGION = [
  'section[aria-label="Chart"]',
  "#chart-settings",
  '[aria-label="Export chart"]',
  '.dashboard-widget [role="img"][aria-label]',
  '.dashboard-widget [aria-label="Drill-down"]',
].join(", ");
const DASHBOARD_REGION = ".dashboard, .dashboard-chrome, .shared-page, .shared-embed";
/** Titles of the dialogs ui-dashboard owns. */
const DASHBOARD_DIALOG = '^(Add (KPI card|text|image|filter)|Share "|Global filters|Delete ")';

/** Run axe (WCAG 2.1 A/AA rules) on the current page state and save the result. */
export async function runAxe(page: Page, screen: string, theme: string): Promise<ScreenReport> {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();

  const violations: AxeViolation[] = [];
  for (const v of results.violations) {
    const nodes: AxeNodeResult[] = [];
    for (const n of v.nodes) {
      const target = n.target.map(String).join(" ");
      const owner = await page
        .evaluate(
          ({ selector, charts, dashboard, dialogTitle }) => {
            try {
              const element = document.querySelector(selector);
              if (element === null) return "ui-data";
              if (element.closest(charts) !== null) return "ui-charts";
              if (element.closest(dashboard) !== null) return "ui-dashboard";
              const dialog = element.closest('[role="dialog"]');
              const labelledBy = dialog?.getAttribute("aria-labelledby");
              const title = labelledBy ? document.getElementById(labelledBy)?.textContent : "";
              if (title && new RegExp(dialogTitle).test(title)) return "ui-dashboard";
              return "ui-data";
            } catch {
              return "ui-data";
            }
          },
          {
            selector: target,
            charts: CHARTS_REGION,
            dashboard: DASHBOARD_REGION,
            dialogTitle: DASHBOARD_DIALOG,
          },
        )
        .catch(() => "ui-data");
      // the generated HTML report is opened from a file and built by the dashboard module
      const isReport = page.url().startsWith("file:");
      nodes.push({
        target,
        html: n.html.slice(0, 240),
        owner: isReport ? "ui-dashboard" : (owner as Owner),
        summary: (n.failureSummary ?? "").replace(/\s+/g, " ").slice(0, 300),
      });
    }
    violations.push({
      id: v.id,
      impact: v.impact ?? "minor",
      help: v.help,
      helpUrl: v.helpUrl,
      nodes,
    });
  }

  const report: ScreenReport = {
    screen,
    theme,
    url: page.url(),
    violations,
    passes: results.passes.length,
    incomplete: results.incomplete.length,
  };
  mkdirSync(AXE_DIR, { recursive: true });
  writeFileSync(
    path.join(AXE_DIR, `${screen}-${theme}.json`.replace(/[^\w.-]+/g, "_")),
    JSON.stringify(report, null, 2),
  );
  return report;
}

export const criticalOf = (report: ScreenReport): AxeViolation[] =>
  report.violations.filter((v) => v.impact === "critical");

interface BaselineEntry {
  rule: string;
  screen: string;
}

function loadBaseline(): BaselineEntry[] {
  const file = path.join(here, "..", "axe-baseline.json");
  if (!existsSync(file)) return [];
  return (JSON.parse(readFileSync(file, "utf8")) as { entries?: BaselineEntry[] }).entries ?? [];
}

/**
 * Serious or critical findings that are not in e2e/axe-baseline.json. These fail the run: the
 * baseline holds today's known findings, so only regressions (or new screens) break the build.
 */
export function newBlockingViolations(report: ScreenReport): string[] {
  const baseline = loadBaseline();
  return report.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .filter((v) => !baseline.some((b) => b.rule === v.id && b.screen === report.screen))
    .map((v) => `${v.id} (${v.impact}) on ${report.screen}: ${v.nodes[0]?.target ?? ""}`);
}

interface Aggregate {
  rule: string;
  impact: string;
  help: string;
  helpUrl: string;
  owner: Owner;
  screens: string[];
  count: number;
  example: { target: string; html: string };
}

const IMPACT_ORDER = ["critical", "serious", "moderate", "minor"];

/** Merge every per-screen result into test-results/axe-report.json and axe-report.md. */
export function summarizeAxeReports(): void {
  if (!existsSync(AXE_DIR)) return;
  const reports = readdirSync(AXE_DIR)
    .filter((f) => f.endsWith(".json"))
    .map((f) => JSON.parse(readFileSync(path.join(AXE_DIR, f), "utf8")) as ScreenReport)
    .sort((a, b) => `${a.screen}${a.theme}`.localeCompare(`${b.screen}${b.theme}`));
  if (reports.length === 0) return;

  const merged = new Map<string, Aggregate>();
  for (const report of reports) {
    for (const violation of report.violations) {
      for (const node of violation.nodes) {
        const key = `${node.owner}|${violation.id}`;
        const label = `${report.screen} (${report.theme})`;
        const entry = merged.get(key) ?? {
          rule: violation.id,
          impact: violation.impact,
          help: violation.help,
          helpUrl: violation.helpUrl,
          owner: node.owner,
          screens: [],
          count: 0,
          example: { target: node.target, html: node.html },
        };
        entry.count += 1;
        if (!entry.screens.includes(label)) entry.screens.push(label);
        merged.set(key, entry);
      }
    }
  }
  const rows = [...merged.values()].sort(
    (a, b) =>
      a.owner.localeCompare(b.owner) ||
      IMPACT_ORDER.indexOf(a.impact) - IMPACT_ORDER.indexOf(b.impact) ||
      b.count - a.count,
  );

  writeFileSync(
    path.join(AXE_DIR, "..", "axe-report.json"),
    JSON.stringify({ generatedAt: new Date().toISOString(), reports, byOwner: rows }, null, 2),
  );

  const lines = ["# axe accessibility report (WCAG 2.1 A and AA)", ""];
  lines.push(`Screens checked: ${reports.length}`, "");
  lines.push(
    "| Screen | Theme | Violations (rules) | Critical | Serious |",
    "|---|---|---|---|---|",
  );
  for (const r of reports) {
    const count = (impact: string) => r.violations.filter((v) => v.impact === impact).length;
    lines.push(
      `| ${r.screen} | ${r.theme} | ${r.violations.length} | ${count("critical")} | ${count("serious")} |`,
    );
  }
  for (const owner of ["ui-data", "ui-charts", "ui-dashboard"] as Owner[]) {
    const mine = rows.filter((r) => r.owner === owner);
    if (mine.length === 0) continue;
    lines.push("", `## ${owner}`, "");
    for (const r of mine) {
      lines.push(
        `- **${r.rule}** (${r.impact}, ${r.count} node${r.count === 1 ? "" : "s"}): ${r.help}`,
        `  - screens: ${r.screens.join(", ")}`,
        `  - example: \`${r.example.target}\``,
        `  - ${r.helpUrl}`,
      );
    }
  }
  writeFileSync(path.join(AXE_DIR, "..", "axe-report.md"), `${lines.join("\n")}\n`);
}
