import { BarChart3 } from "lucide-react";
import type { FieldSummary } from "../api/types";
import { Button } from "../components/ui/button";
import { Tooltip } from "../components/ui/tooltip";
import { groupLabel } from "./fieldSummaries";

const number = (v: number | string | null | undefined, digits = 3) =>
  v === null || v === undefined
    ? "—"
    : typeof v === "string"
      ? v
      : v.toLocaleString(undefined, { maximumFractionDigits: digits });

const modeLabel = (s: FieldSummary) =>
  s.modes.length === 0 ? "—" : s.modes.map((m) => (m === null ? "(empty)" : String(m))).join(", ");

/**
 * One result row per (field, group): mean/median/mode, std/variance, quartiles and the
 * requested percentiles, skewness and kurtosis. Non-numeric fields show counts and modes only.
 */
export function DescribeTable({
  summaries,
  onPlot,
}: {
  summaries: FieldSummary[];
  /** "Plot this" shortcut for one numeric field's summary (MVP §5). Omit to hide the column. */
  onPlot?: (summary: FieldSummary) => void;
}) {
  if (summaries.length === 0) {
    return <p className="text-sm text-muted">No results yet.</p>;
  }
  const percentileLabels = [...new Set(summaries.flatMap((s) => s.quantiles.map((q) => q.p)))].sort(
    (a, b) => a - b,
  );

  return (
    <div className="overflow-auto rounded-md border border-line">
      <table className="w-full border-collapse text-left text-xs text-fg">
        <caption className="sr-only">Descriptive statistics</caption>
        <thead className="sticky top-0 bg-surface">
          <tr>
            {[
              "Field",
              "Group",
              "Count",
              "Missing",
              "Distinct",
              "Mean",
              "Median",
              "Mode",
              "Std",
              "Variance",
              "Min",
              "Q1",
              "Q3",
              "Max",
              "IQR",
              "Skewness",
              "Kurtosis",
              ...percentileLabels.map((p) => `P${Math.round(p * 100)}`),
            ].map((header) => (
              <th key={header} scope="col" className="border-b border-line px-2 py-1 font-medium">
                {header}
              </th>
            ))}
            {onPlot ? (
              <th scope="col" className="border-b border-line px-2 py-1 font-medium">
                <span className="sr-only">Actions</span>
              </th>
            ) : null}
          </tr>
        </thead>
        <tbody>
          {summaries.map((s, i) => {
            const isNumeric = s.kind === "numeric";
            const byP = new Map(s.quantiles.map((q) => [q.p, q.value]));
            return (
              // biome-ignore lint/suspicious/noArrayIndexKey: (field, group) repeats across calls
              <tr key={`${s.field}-${groupLabel(s.group)}-${i}`}>
                <td className="border-b border-line/50 px-2 py-0.5 font-medium">{s.field}</td>
                <td className="border-b border-line/50 px-2 py-0.5">{groupLabel(s.group)}</td>
                <td className="border-b border-line/50 px-2 py-0.5 tabular-nums">
                  {s.count.toLocaleString()}
                </td>
                <td className="border-b border-line/50 px-2 py-0.5 tabular-nums">
                  {s.missing.toLocaleString()}
                </td>
                <td className="border-b border-line/50 px-2 py-0.5 tabular-nums">
                  {s.distinct === null ? "—" : s.distinct.toLocaleString()}
                </td>
                <td className="border-b border-line/50 px-2 py-0.5 tabular-nums">
                  {number(s.mean)}
                </td>
                <td className="border-b border-line/50 px-2 py-0.5 tabular-nums">
                  {number(s.median)}
                </td>
                <td className="border-b border-line/50 px-2 py-0.5">{modeLabel(s)}</td>
                <td className="border-b border-line/50 px-2 py-0.5 tabular-nums">
                  {number(s.std)}
                </td>
                <td className="border-b border-line/50 px-2 py-0.5 tabular-nums">
                  {number(s.variance)}
                </td>
                <td className="border-b border-line/50 px-2 py-0.5 tabular-nums">
                  {number(s.min)}
                </td>
                <td className="border-b border-line/50 px-2 py-0.5 tabular-nums">{number(s.q1)}</td>
                <td className="border-b border-line/50 px-2 py-0.5 tabular-nums">{number(s.q3)}</td>
                <td className="border-b border-line/50 px-2 py-0.5 tabular-nums">
                  {number(s.max)}
                </td>
                <td className="border-b border-line/50 px-2 py-0.5 tabular-nums">
                  {number(s.iqr)}
                </td>
                <td className="border-b border-line/50 px-2 py-0.5 tabular-nums">
                  {number(s.skewness)}
                </td>
                <td className="border-b border-line/50 px-2 py-0.5 tabular-nums">
                  {number(s.kurtosis)}
                </td>
                {percentileLabels.map((p) => (
                  <td key={p} className="border-b border-line/50 px-2 py-0.5 tabular-nums">
                    {number(byP.get(p) ?? null)}
                  </td>
                ))}
                {onPlot ? (
                  <td className="border-b border-line/50 px-2 py-0.5">
                    {isNumeric ? (
                      <Tooltip content={`Plot ${s.field} (${groupLabel(s.group)})`}>
                        <Button
                          size="icon"
                          variant="ghost"
                          aria-label={`Plot ${s.field} (${groupLabel(s.group)})`}
                          onClick={() => onPlot(s)}
                        >
                          <BarChart3 size={14} aria-hidden="true" />
                        </Button>
                      </Tooltip>
                    ) : null}
                  </td>
                ) : null}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
