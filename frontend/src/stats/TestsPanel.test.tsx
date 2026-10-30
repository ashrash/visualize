import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Column } from "../api/types";
import { TestsPanel } from "./TestsPanel";

const columns: Column[] = [
  { name: "x", type: "DOUBLE", semantic: "quantitative", nullCount: 0, approxDistinct: 100 },
  { name: "y", type: "DOUBLE", semantic: "quantitative", nullCount: 0, approxDistinct: 100 },
];

const withClient = (ui: ReactElement) =>
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      {ui}
    </QueryClientProvider>,
  );

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("TestsPanel", () => {
  it("disables the form and Run while the Wave 2 endpoints are not merged (statsTests off)", () => {
    withClient(
      <TestsPanel source={{ kind: "dataset", id: "d1" }} pipelines={[]} columns={columns} />,
    );
    expect(screen.getByText(/coming soon/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Run" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByText("Not available yet.")).toBeTruthy();
  });

  it("runs a test and shows the result once statsTests is flipped on", async () => {
    localStorage.setItem("viz.features", JSON.stringify({ statsTests: true }));
    const fn = vi.fn(
      async (_url: string, _init?: RequestInit) =>
        new Response(
          JSON.stringify({
            values: { estimate: 0.42, pValue: 0.01 },
            method: "Pearson correlation",
            n: 50,
            sampled: false,
            warnings: [],
            nMissing: 0,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
    );
    vi.stubGlobal("fetch", fn);

    withClient(
      <TestsPanel source={{ kind: "dataset", id: "d1" }} pipelines={[]} columns={columns} />,
    );
    expect(screen.queryByText(/coming soon/i)).toBeNull();

    // Two checklists ("Fields", "Group by") both list column x ("xquantitative" as one label
    // text node); the first is the Fields one.
    fireEvent.click(screen.getAllByLabelText(/^x/)[0] as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: "Run" }));

    await waitFor(() => expect(document.body.textContent).toContain("Pearson correlation"));
    const [url, init] = fn.mock.calls.at(-1) ?? ["", undefined];
    expect(url).toBe("/api/stats/correlation");
    expect(JSON.parse((init as RequestInit).body as string)).toMatchObject({
      kind: "correlation",
      fields: ["x"],
    });
  });
});
