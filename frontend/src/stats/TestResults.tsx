import { Component, type ReactNode } from "react";
import { type StatTestKind, valuesOf } from "../api/stats";
import type {
  AnovaValues,
  AssumptionCheck,
  ChiSquareValues,
  ConfidenceIntervalValues,
  CorrelationValues,
  DistributionFitValues,
  OutlierValues,
  RankTestValues,
  RegressionValues,
  StatResult,
  TTestValues,
} from "../api/types";
import { StatResultView } from "./StatResultView";

const num = (v: number | null | undefined, digits = 4): string =>
  v === null || v === undefined || !Number.isFinite(v)
    ? "—"
    : v.toLocaleString(undefined, { maximumFractionDigits: digits });

const pval = (v: number | null | undefined): string =>
  v === null || v === undefined ? "—" : v < 0.0001 ? "< 0.0001" : v.toFixed(4);

const scalar = (v: string | number | boolean | null | undefined): string =>
  v === null || v === undefined ? "(missing)" : String(v);

const groupLabel = (g: (string | number | boolean | null)[] | null): string =>
  g === null ? "all" : g.map(scalar).join(", ");

const interval = (low: number | null, high: number | null) =>
  low === null && high === null ? "—" : `[${num(low)}, ${num(high)}]`;

function DataTable({
  caption,
  headers,
  rows,
}: {
  caption: string;
  headers: string[];
  rows: ReactNode[][];
}) {
  return (
    <div className="overflow-auto rounded-md border border-line">
      <table className="w-full border-collapse text-left text-xs text-fg">
        <caption className="sr-only">{caption}</caption>
        <thead className="bg-surface">
          <tr>
            {headers.map((h) => (
              <th key={h} scope="col" className="border-b border-line px-2 py-1 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((cells, r) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: rows are static, derived from the result.
            <tr key={r}>
              {cells.map((cell, c) =>
                c === 0 ? (
                  // biome-ignore lint/suspicious/noArrayIndexKey: static cells.
                  <th key={c} scope="row" className="border-b border-line/50 px-2 py-0.5">
                    {cell}
                  </th>
                ) : (
                  // biome-ignore lint/suspicious/noArrayIndexKey: static cells.
                  <td key={c} className="border-b border-line/50 px-2 py-0.5 tabular-nums">
                    {cell}
                  </td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Headline({ items }: { items: [string, string][] }) {
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
      {items.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted">{k}</dt>
          <dd className="tabular-nums">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function Checks({ checks }: { checks: AssumptionCheck[] }) {
  if (checks.length === 0) return null;
  return (
    <DataTable
      caption="Assumption checks"
      headers={["Check", "Statistic", "p-value", "Result"]}
      rows={checks.map((c) => [
        c.name,
        num(c.statistic),
        pval(c.pValue),
        c.passed === null ? (c.note ?? "not run") : c.passed ? "ok" : (c.note ?? "violated"),
      ])}
    />
  );
}

function Correlation({ v }: { v: CorrelationValues }) {
  return (
    <div className="flex flex-col gap-2">
      <DataTable
        caption={`${v.method} correlation matrix`}
        headers={["", ...v.fields]}
        rows={v.fields.map((f, i) => [
          f,
          ...v.fields.map((_, j) => {
            const r = v.coefficient[i]?.[j] ?? null;
            const p = v.pValue[i]?.[j] ?? null;
            return i === j ? "1" : `${num(r, 3)} (p ${pval(p)})`;
          }),
        ])}
      />
      <p className="text-xs text-muted">
        Each cell: coefficient and p-value; pairs use {v.missing} deletion.
      </p>
    </div>
  );
}

function Regression({ v }: { v: RegressionValues }) {
  return (
    <div className="flex flex-col gap-3">
      <Headline
        items={[
          ["R²", num(v.r2)],
          ["Adjusted R²", num(v.adjR2)],
          ["F", `${num(v.fStatistic)} (p ${pval(v.fPValue)})`],
          ["Residual SE", num(v.sigma)],
          ["AIC / BIC", `${num(v.aic)} / ${num(v.bic)}`],
        ]}
      />
      <DataTable
        caption={`Coefficients of ${v.y}${v.robust ? " (robust standard errors)" : ""}`}
        headers={[
          "Term",
          "Estimate",
          "Std. error",
          "t",
          "p-value",
          `${Math.round(v.ciLevel * 100)}% CI`,
          "VIF",
        ]}
        rows={v.terms.map((t) => [
          t.term,
          num(t.estimate),
          num(t.stdError),
          num(t.statistic, 3),
          pval(t.pValue),
          interval(t.ciLow, t.ciHigh),
          num(t.vif, 2),
        ])}
      />
      {v.referenceLevels.length > 0 ? (
        <p className="text-xs text-muted">
          Reference levels:{" "}
          {v.referenceLevels.map((r) => `${r.field} = ${scalar(r.level)}`).join("; ")}
        </p>
      ) : null}
      <Checks checks={v.checks} />
    </div>
  );
}

// v.estimate is the mean for a one-sample test, A - B for a two-sample test, and the mean of
// the differences for a paired test (contracts/stats.py's TTestValues.estimate) - label it
// accordingly instead of always calling it a "difference".
const ESTIMATE_LABEL: Record<TTestValues["kind"], string> = {
  one_sample: "Mean",
  welch: "Mean difference (A − B)",
  student: "Mean difference (A − B)",
  paired: "Mean of differences",
};

function TTest({ v }: { v: TTestValues }) {
  return (
    <div className="flex flex-col gap-3">
      <Headline
        items={[
          [ESTIMATE_LABEL[v.kind], num(v.estimate)],
          [`${Math.round(v.ciLevel * 100)}% CI`, interval(v.ciLow, v.ciHigh)],
          ["t", `${num(v.statistic, 3)} (df ${num(v.df, 2)})`],
          ["p-value", pval(v.pValue)],
          ["Cohen's d", num(v.cohensD, 3)],
        ]}
      />
      <DataTable
        caption="Samples"
        headers={["Sample", "n", "Mean", "SD", "SEM"]}
        rows={v.samples.map((s) => [s.label, num(s.n, 0), num(s.mean), num(s.std), num(s.sem)])}
      />
      <Checks checks={v.checks} />
    </div>
  );
}

function RankTest({ v }: { v: RankTestValues }) {
  return (
    <div className="flex flex-col gap-3">
      <Headline
        items={[
          ["Statistic", num(v.statistic)],
          ["p-value", pval(v.pValue)],
          [v.effectSizeName, num(v.effectSize, 3)],
        ]}
      />
      <DataTable
        caption="Samples"
        headers={["Sample", "n", "Median", "Mean rank"]}
        rows={v.samples.map((s) => [s.label, num(s.n, 0), num(s.median), num(s.meanRank, 2)])}
      />
    </div>
  );
}

function Anova({ v }: { v: AnovaValues }) {
  return (
    <div className="flex flex-col gap-3">
      <DataTable
        caption={`ANOVA of ${v.field} (type ${v.ssType} sums of squares)`}
        headers={["Term", "df", "Sum sq", "Mean sq", "F", "p-value", "Partial η²"]}
        rows={v.table.map((r) => [
          r.term,
          num(r.df, 0),
          num(r.sumSq),
          num(r.meanSq),
          num(r.statistic, 3),
          pval(r.pValue),
          num(r.partialEtaSq, 3),
        ])}
      />
      {v.welch ? (
        <p className="text-xs text-muted">
          Welch ANOVA: F = {num(v.welch.statistic, 3)}, p {pval(v.welch.pValue)}
        </p>
      ) : null}
      <DataTable
        caption="Group means"
        headers={["Group", "n", "Mean", "SD"]}
        rows={v.cells.map((s) => [s.label, num(s.n, 0), num(s.mean), num(s.std)])}
      />
      {v.postHoc && v.postHoc.length > 0 ? (
        <DataTable
          caption="Tukey HSD pairwise comparisons"
          headers={["Pair", "Difference", `${Math.round(v.ciLevel * 100)}% CI`, "Adj. p"]}
          rows={v.postHoc.map((c) => [
            `${scalar(c.b)} − ${scalar(c.a)}`,
            num(c.diff),
            interval(c.ciLow, c.ciHigh),
            pval(c.pValue),
          ])}
        />
      ) : null}
      <Checks checks={v.checks} />
    </div>
  );
}

function ChiSquare({ v }: { v: ChiSquareValues }) {
  const cols = v.columns ?? ["count"];
  return (
    <div className="flex flex-col gap-3">
      <Headline
        items={[
          ["χ²", `${num(v.statistic, 3)} (df ${v.df})`],
          ["p-value", pval(v.pValue)],
          ["Cramér's V", num(v.cramersV, 3)],
          ...(v.fisherPValue !== null
            ? ([
                ["Fisher exact p", pval(v.fisherPValue)],
                ["Odds ratio", num(v.oddsRatio, 3)],
              ] as [string, string][])
            : []),
        ]}
      />
      <DataTable
        caption="Observed counts (expected in brackets)"
        headers={["", ...cols.map(scalar)]}
        rows={v.rows.map((row, i) => [
          scalar(row),
          ...cols.map((_, j) => `${num(v.observed[i]?.[j], 0)} (${num(v.expected[i]?.[j], 1)})`),
        ])}
      />
      <DataTable
        caption="Standardized residuals"
        headers={["", ...cols.map(scalar)]}
        rows={v.rows.map((row, i) => [
          scalar(row),
          ...cols.map((_, j) => num(v.residuals[i]?.[j], 2)),
        ])}
      />
    </div>
  );
}

function Fit({ v }: { v: DistributionFitValues }) {
  return (
    <div className="flex flex-col gap-2">
      <DataTable
        caption={`Distributions fitted to ${v.field}, best AIC first`}
        headers={["Distribution", "AIC", "ΔAIC", "Weight", "KS", "KS p", "Parameters"]}
        rows={v.fits.map((f) => [
          f.label,
          num(f.aic, 1),
          num(f.deltaAic, 2),
          num(f.aicWeight, 3),
          num(f.ksStatistic, 3),
          pval(f.ksPValue),
          f.params.map((p) => `${p.name} = ${num(p.value, 3)}`).join(", "),
        ])}
      />
      {v.skipped.length > 0 ? (
        <p className="text-xs text-muted">
          Skipped: {v.skipped.map((s) => `${s.distribution} (${s.reason})`).join("; ")}
        </p>
      ) : null}
    </div>
  );
}

function Outliers({ v }: { v: OutlierValues }) {
  return (
    <div className="flex flex-col gap-3">
      <DataTable
        caption={`Outliers by ${v.method} (threshold ${v.threshold})`}
        headers={["Field", "Group", "n", "Low fence", "High fence", "Below", "Above", "Share"]}
        rows={v.summaries.map((s) => [
          s.field,
          groupLabel(s.group),
          num(s.n, 0),
          num(s.low),
          num(s.high),
          num(s.nLow, 0),
          num(s.nHigh, 0),
          s.share === null ? "—" : `${num(s.share * 100, 2)}%`,
        ])}
      />
      {v.rows.length > 0 ? (
        <DataTable
          caption="Most extreme outliers"
          headers={["Field", "Group", "Value", "Score", "Side"]}
          rows={v.rows.map((r) => [
            r.field,
            groupLabel(r.group),
            num(r.value),
            num(r.score, 2),
            r.side,
          ])}
        />
      ) : null}
    </div>
  );
}

function Intervals({ v }: { v: ConfidenceIntervalValues }) {
  return (
    <DataTable
      caption={`${Math.round(v.ciLevel * 100)}% confidence intervals for the ${v.statistic}`}
      headers={["Field", "Group", "n", "Estimate", "Interval", "Std. error", "Method"]}
      rows={v.intervals.map((i) => [
        i.field,
        groupLabel(i.group),
        num(i.n, 0),
        num(i.estimate),
        interval(i.ciLow, i.ciHigh),
        num(i.stdError),
        i.method,
      ])}
    />
  );
}

function Body({ kind, result }: { kind: StatTestKind; result: StatResult }) {
  switch (kind) {
    case "correlation":
      return <Correlation v={valuesOf<CorrelationValues>(result)} />;
    case "regression":
      return <Regression v={valuesOf<RegressionValues>(result)} />;
    case "ttest":
      return <TTest v={valuesOf<TTestValues>(result)} />;
    case "ranktest":
      return <RankTest v={valuesOf<RankTestValues>(result)} />;
    case "anova":
      return <Anova v={valuesOf<AnovaValues>(result)} />;
    case "chisquare":
      return <ChiSquare v={valuesOf<ChiSquareValues>(result)} />;
    case "fit":
      return <Fit v={valuesOf<DistributionFitValues>(result)} />;
    case "outliers":
      return <Outliers v={valuesOf<OutlierValues>(result)} />;
    case "ci":
      return <Intervals v={valuesOf<ConfidenceIntervalValues>(result)} />;
  }
}

/** If a dedicated renderer meets an unexpected values shape, fall back to the generic view. */
class Fallback extends Component<{ result: StatResult; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? <StatResultView result={this.props.result} /> : this.props.children;
  }
}

/** Per-test result view with the shared method / n / warnings frame around it. `runId` identifies
 * this particular run (not just its method+n, which two different runs can share), so the error
 * boundary below remounts for every new result instead of staying stuck in fallback mode after an
 * earlier run's renderer crash. */
export function TestResultView({
  kind,
  result,
  runId,
}: {
  kind: StatTestKind;
  result: StatResult;
  runId: number;
}) {
  const warnings = result.warnings ?? [];
  const total = result.nTotal ?? result.n;
  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-muted">
        {result.method} · n = {result.n.toLocaleString()}
        {result.sampled ? ` (sampled from ${total.toLocaleString()})` : ""}
        {result.nMissing > 0 ? ` · ${result.nMissing.toLocaleString()} rows dropped (missing)` : ""}
      </p>
      <Fallback result={result} key={runId}>
        <Body kind={kind} result={result} />
      </Fallback>
      {warnings.length > 0 ? (
        <ul className="list-disc pl-4 text-xs text-muted">
          {warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
