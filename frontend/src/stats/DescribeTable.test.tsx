import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FieldSummary } from "../api/types";
import { TooltipProvider } from "../components/ui/tooltip";
import { DescribeTable } from "./DescribeTable";

const withTooltips = (ui: ReactElement) => render(<TooltipProvider>{ui}</TooltipProvider>);

afterEach(cleanup);

const numeric: FieldSummary = {
  field: "age",
  group: null,
  kind: "numeric",
  count: 100,
  missing: 2,
  distinct: 40,
  mean: 35.2,
  std: 10.1,
  variance: 102.0,
  sem: 1.1,
  sum: 3520,
  iqr: 12,
  min: 18,
  max: 80,
  median: 34,
  q1: 28,
  q3: 40,
  skewness: 0.2,
  kurtosis: -0.1,
  quantiles: [
    { p: 0.05, value: 20 },
    { p: 0.95, value: 60 },
  ],
  modes: [30],
  modeCount: 5,
};

const categorical: FieldSummary = {
  ...numeric,
  field: "region",
  kind: "categorical",
  mean: null,
  std: null,
  variance: null,
  sem: null,
  sum: null,
  iqr: null,
  min: null,
  max: null,
  median: null,
  q1: null,
  q3: null,
  skewness: null,
  kurtosis: null,
  quantiles: [],
  modes: ["east"],
};

describe("DescribeTable", () => {
  it("shows a placeholder with no results", () => {
    render(<DescribeTable summaries={[]} />);
    expect(screen.getByText("No results yet.")).toBeTruthy();
  });

  it("renders one row per summary with its stats and requested percentiles", () => {
    render(<DescribeTable summaries={[numeric]} />);
    expect(screen.getByText("age")).toBeTruthy();
    expect(screen.getByText("Overall")).toBeTruthy();
    expect(screen.getByText("P5")).toBeTruthy();
    expect(screen.getByText("P95")).toBeTruthy();
  });

  it("offers Plot this only for numeric fields, and calls back with that summary", () => {
    const onPlot = vi.fn();
    withTooltips(<DescribeTable summaries={[numeric, categorical]} onPlot={onPlot} />);
    const plotButtons = screen.getAllByRole("button", { name: /Plot/ });
    expect(plotButtons).toHaveLength(1);
    fireEvent.click(plotButtons[0] as HTMLElement);
    expect(onPlot).toHaveBeenCalledWith(numeric);
  });
});
