import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Dataset } from "../api/types";
import { DeleteDialog } from "./DatasetActions";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const dataset: Dataset = {
  id: "d1",
  name: "sales",
  source: { kind: "csv", fileName: "sales.csv" },
};

function setup() {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(
        typeof input === "string" ? input : "url" in input ? input.url : input.href,
      );
      calls.push(url.pathname + url.search);
      return url.search.includes("force=true")
        ? new Response(null, { status: 204 })
        : new Response(JSON.stringify({ detail: "sales is used by: Q3 report" }), {
            status: 409,
            headers: { "Content-Type": "application/json" },
          });
    }),
  );
  const onDeleted = vi.fn();
  const onOpenChange = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <DeleteDialog dataset={dataset} onOpenChange={onOpenChange} onDeleted={onDeleted} />
    </QueryClientProvider>,
  );
  return { calls, onDeleted, onOpenChange };
}

describe("DeleteDialog", () => {
  it("shows the server's 409 message as is, then retries with force after confirmation", async () => {
    const { calls, onDeleted, onOpenChange } = setup();
    fireEvent.click(screen.getByRole("button", { name: "Delete dataset" }));
    await screen.findByText("sales is used by: Q3 report");
    expect(onDeleted).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Delete anyway" }));
    await waitFor(() => expect(onDeleted).toHaveBeenCalledWith("d1"));
    expect(calls).toEqual(["/api/datasets/d1?force=false", "/api/datasets/d1?force=true"]);
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
