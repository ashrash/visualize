import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Column } from "../api/types";
import { FilterBody } from "./ColumnFilter";
import { draftFromPredicate, predicateFromDraft, setFilter } from "./filters";

afterEach(cleanup);

const col = (name: string, semantic: Column["semantic"]): Column => ({
  name,
  type: "X",
  semantic,
  nullCount: 0,
  approxDistinct: 3,
});
const region = col("region", "nominal");
const units = col("units", "quantitative");
const when = col("order_date", "temporal");

const values = {
  field: "region",
  values: [
    { value: "North", count: 120 },
    { value: "South", count: 80 },
    { value: null, count: 3 },
  ],
  truncated: false,
};

function renderBody(
  column: Column,
  onApply = vi.fn(),
  predicate?: Parameters<typeof FilterBody>[0]["predicate"],
) {
  const loadValues = vi.fn(async () => values);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <FilterBody column={column} predicate={predicate} loadValues={loadValues} onApply={onApply} />
    </QueryClientProvider>,
  );
  return { onApply, loadValues };
}

describe("FilterBody", () => {
  it("lists values with counts for a nominal column and applies a selection as `in`", async () => {
    const { onApply } = renderBody(region);
    fireEvent.click(await screen.findByLabelText(/North/));
    fireEvent.click(screen.getByLabelText(/South/));
    expect(screen.getByText("120")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith({ op: "in", field: "region", values: ["North", "South"] });
  });

  it("can select the empty value and shows it as (empty)", async () => {
    const { onApply } = renderBody(region);
    fireEvent.click(await screen.findByLabelText(/\(empty\)/));
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith({ op: "in", field: "region", values: [null] });
  });

  it("searches values server-side after a pause", async () => {
    const { loadValues } = renderBody(region);
    await screen.findByLabelText(/North/);
    fireEvent.change(screen.getByLabelText("Search values"), { target: { value: "sou" } });
    await waitFor(() => expect(loadValues).toHaveBeenLastCalledWith("sou", expect.anything()));
  });

  it("offers a numeric range for quantitative columns", () => {
    const { onApply } = renderBody(units);
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "20" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith({
      op: "range",
      field: "units",
      min: 5,
      max: 20,
      includeMin: true,
      includeMax: true,
    });
  });

  it("uses between_dates for temporal columns", () => {
    const { onApply } = renderBody(when);
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2024-01-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith(
      expect.objectContaining({ op: "between_dates", min: "2024-01-01", max: null }),
    );
  });

  it("filters on empty / not empty", () => {
    const { onApply } = renderBody(units);
    fireEvent.change(screen.getByLabelText("Filter units"), { target: { value: "null" } });
    fireEvent.change(screen.getByLabelText("Keep rows that are"), { target: { value: "notnull" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith({ op: "is_null", field: "units", isNull: false });
  });

  it("applies nothing for an empty draft and clears with Clear", () => {
    const { onApply } = renderBody(units);
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenLastCalledWith(null);
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(onApply).toHaveBeenLastCalledWith(null);
  });

  it("starts from the current predicate", () => {
    renderBody(units, vi.fn(), {
      op: "range",
      field: "units",
      min: 1,
      max: 9,
      includeMin: true,
      includeMax: true,
    });
    expect((screen.getByLabelText("From") as HTMLInputElement).value).toBe("1");
    expect((screen.getByLabelText("To") as HTMLInputElement).value).toBe("9");
  });
});

describe("filter helpers", () => {
  it("round-trips drafts and ignores unparseable numbers", () => {
    const p = predicateFromDraft(units, { mode: "range", min: "1", max: "2" });
    expect(draftFromPredicate(p ?? undefined, units)).toEqual({
      mode: "range",
      min: "1",
      max: "2",
    });
    expect(predicateFromDraft(units, { mode: "range", min: "abc", max: "" })).toBeNull();
  });

  it("sets and clears a column's filter without touching others", () => {
    const a = { op: "is_null", field: "a", isNull: true } as const;
    const b = { op: "is_null", field: "b", isNull: false } as const;
    const map = setFilter(setFilter({}, "a", a), "b", b);
    expect(Object.keys(setFilter(map, "a", null))).toEqual(["b"]);
  });
});
