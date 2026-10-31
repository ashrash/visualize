import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Column } from "../api/types";
import { TestsPanel } from "./TestsPanel";

const columns: Column[] = [
  { name: "x", type: "DOUBLE", semantic: "quantitative", nullCount: 0, approxDistinct: 100 },
  { name: "y", type: "DOUBLE", semantic: "quantitative", nullCount: 0, approxDistinct: 100 },
  { name: "a", type: "VARCHAR", semantic: "nominal", nullCount: 0, approxDistinct: 3 },
  { name: "b", type: "VARCHAR", semantic: "nominal", nullCount: 0, approxDistinct: 2 },
];

const withClient = (ui: ReactElement) =>
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      {ui}
    </QueryClientProvider>,
  );

const panel = () =>
  withClient(
    <TestsPanel source={{ kind: "dataset", id: "d1" }} pipelines={[]} columns={columns} />,
  );

const base = { n: 50, sampled: false, warnings: [], nMissing: 0 };

function stubFetch(body: unknown) {
  const fn = vi.fn(
    async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
  );
  vi.stubGlobal("fetch", fn);
  return fn;
}

async function sentRequest(fn: ReturnType<typeof stubFetch>) {
  const [input] = fn.mock.calls.at(-1) ?? [undefined];
  if (!(input instanceof Request)) throw new Error("expected a Request");
  return { path: new URL(input.url).pathname, body: JSON.parse(await input.clone().text()) };
}

const pick = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("TestsPanel", () => {
  it("keeps Run disabled and says what is missing until the form is complete", () => {
    panel();
    expect(screen.getByRole("button", { name: "Run" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByText("Pick at least two fields.")).toBeTruthy();
  });

  it("runs chi-square with two categorical fields and renders the contingency table", async () => {
    const fn = stubFetch({
      ...base,
      method: "Pearson chi-square test of independence",
      values: {
        kind: "independence",
        statistic: 4.2,
        df: 1,
        pValue: 0.04,
        rows: ["u", "v"],
        columns: ["p", "q"],
        observed: [
          [10, 20],
          [30, 40],
        ],
        expected: [
          [12, 18],
          [28, 42],
        ],
        residuals: [
          [-1, 1],
          [1, -1],
        ],
        cramersV: 0.2,
        yates: true,
        fisherPValue: 0.05,
        oddsRatio: 0.67,
        minExpected: 12,
      },
    });
    panel();
    pick("Test", "chisquare");
    pick("Row field", "a");
    pick("Column field", "b");
    fireEvent.click(screen.getByRole("button", { name: "Run" }));

    await waitFor(() =>
      expect(screen.getByRole("table", { name: /observed counts/i })).toBeTruthy(),
    );
    const sent = await sentRequest(fn);
    expect(sent.path).toBe("/api/stats/chisquare");
    expect(sent.body).toMatchObject({ kind: "independence", field: "a", columnField: "b" });
    expect(document.body.textContent).toContain("10 (12)");
  });

  it("runs a regression with an explicit y and x and shows the coefficient table", async () => {
    const fn = stubFetch({
      ...base,
      method: "OLS",
      values: {
        y: "y",
        terms: [
          {
            term: "x",
            field: "x",
            level: null,
            estimate: 2,
            stdError: 0.1,
            statistic: 20,
            pValue: 0.00001,
            ciLow: 1.8,
            ciHigh: 2.2,
            vif: null,
          },
        ],
        referenceLevels: [],
        r2: 0.9,
        adjR2: 0.89,
        fStatistic: 400,
        fPValue: 0,
        dfModel: 1,
        dfResid: 48,
        sigma: 1,
        logLikelihood: null,
        aic: 1,
        bic: 2,
        conditionNumber: null,
        ciLevel: 0.95,
        robust: false,
        checks: [],
      },
    });
    panel();
    pick("Test", "regression");
    pick("Predict (y)", "y");
    fireEvent.click(screen.getByLabelText(/^x/));
    fireEvent.click(screen.getByRole("button", { name: "Run" }));

    await waitFor(() =>
      expect(screen.getByRole("table", { name: /coefficients of y/i })).toBeTruthy(),
    );
    expect((await sentRequest(fn)).body).toMatchObject({ y: "y", x: ["x"], robust: false });
  });

  it("falls back to the generic view when the values shape is unexpected", async () => {
    stubFetch({ ...base, method: "Odd result", values: { surprise: 1 } });
    panel();
    pick("Test", "fit");
    pick("Value field", "x");
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(document.body.textContent).toContain("surprise"));
  });

  it("surfaces a server error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "field has no numeric values" }), {
            status: 422,
            headers: { "Content-Type": "application/json" },
          }),
      ),
    );
    panel();
    pick("Test", "fit");
    pick("Value field", "x");
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
  });
});
