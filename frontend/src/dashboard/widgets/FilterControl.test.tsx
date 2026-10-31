// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DashboardEnvProvider } from "../env";
import { createDashboard } from "../model";
import { useDashboardRuntime } from "../runtimeStore";
import type { DashboardFilter } from "../types";

vi.mock("../../data/hooks", () => ({ useDatasets: () => ({ data: [] }) }));
vi.mock("../../api/datasets", () => ({
  fetchColumnValues: vi.fn(async () => ({
    field: "region",
    values: [
      { value: "North", count: 3 },
      { value: "South", count: 2 },
    ],
    truncated: false,
  })),
}));

import { FilterControl } from "./FilterControl";

const dateFilter: DashboardFilter = {
  id: "f-date",
  label: "Order date",
  field: "order_date",
  fieldMap: {},
  predicate: null,
  control: "date_range",
};
const regionFilter: DashboardFilter = {
  id: "f-region",
  label: "Region",
  field: "region",
  fieldMap: {},
  predicate: null,
  control: "dropdown",
};

function show(filter: DashboardFilter) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <DashboardEnvProvider
        value={{
          dashboard: { ...createDashboard(), filters: [filter] },
          charts: [
            {
              id: "c",
              name: "c",
              plotType: "bar",
              source: { kind: "dataset", id: "ds" },
              encodings: {},
            } as never,
          ],
          pipelines: [],
          globalFilters: [],
          mode: "view",
          columnsOf: () => undefined,
        }}
      >
        <FilterControl filter={filter} control={filter.control ?? "date_range"} />
      </DashboardEnvProvider>
    </QueryClientProvider>,
  );
}

const stored = (id: string) => useDashboardRuntime.getState().filterValues[id];

beforeEach(() => {
  vi.useFakeTimers();
  useDashboardRuntime.setState({ filterValues: {}, globalValues: {} });
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("date range control", () => {
  const from = () => screen.getByLabelText("Order date from") as HTMLInputElement;
  const to = () => screen.getByLabelText("Order date to") as HTMLInputElement;

  it("keeps a picked date while the filter commits (no blink back to empty)", () => {
    show(dateFilter);
    fireEvent.change(from(), { target: { value: "2024-03-01" } });
    expect(from().value).toBe("2024-03-01");
    // still inside the debounce window: other renders must not put the old value back
    act(() => vi.advanceTimersByTime(60));
    expect(from().value).toBe("2024-03-01");
  });

  it("keeps both ends when the second is filled inside the first one's debounce window", () => {
    show(dateFilter);
    fireEvent.change(from(), { target: { value: "2024-03-01" } });
    act(() => vi.advanceTimersByTime(60));
    fireEvent.change(to(), { target: { value: "2024-04-01" } });
    act(() => vi.advanceTimersByTime(800));
    expect(from().value).toBe("2024-03-01");
    expect(to().value).toBe("2024-04-01");
    expect(stored("f-date")).toMatchObject({
      op: "between_dates",
      field: "order_date",
      min: "2024-03-01",
      max: "2024-04-02", // the whole last day: exclusive end
    });
  });

  it("follows the stored value when it changes from outside (a reset)", () => {
    show(dateFilter);
    fireEvent.change(from(), { target: { value: "2024-03-01" } });
    act(() => vi.advanceTimersByTime(300));
    expect(stored("f-date")).toBeTruthy();
    act(() => useDashboardRuntime.getState().setFilter("f-date", null));
    expect(from().value).toBe("");
    expect(to().value).toBe("");
  });
});

describe("dropdown control", () => {
  it("names the trigger with its visible value, so the name contains the text on screen", () => {
    show(regionFilter);
    const trigger = screen.getByRole("button", { name: /Region filter/ });
    expect(trigger.textContent).toBe("All");
    expect(trigger.getAttribute("aria-label")).toBe("Region filter: All");
    act(() =>
      useDashboardRuntime
        .getState()
        .setFilter("f-region", { op: "in", field: "region", values: ["North"] }),
    );
    const named = screen.getByRole("button", { name: /Region filter/ });
    expect(named.textContent).toBe("North");
    expect(named.getAttribute("aria-label")).toBe("Region filter: North");
    expect(named.getAttribute("aria-label")).toContain(named.textContent ?? "never");
  });
});
