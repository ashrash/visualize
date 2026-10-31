// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import type { SharedView } from "./api";

const getSharedView = vi.fn();
vi.mock("./api", () => ({ getSharedView: (...a: unknown[]) => getSharedView(...a) }));
vi.mock("../DashboardView", () => ({
  DashboardView: ({ mode, dashboard }: { mode: string; dashboard: { name: string } }) => (
    <div data-testid="dashboard" data-mode={mode}>
      {dashboard.name}
    </div>
  ),
}));
vi.mock("../../charts", () => ({
  ChartView: ({ spec }: { spec: { name: string } }) => <div data-testid="chart">{spec.name}</div>,
}));
vi.mock("../print.css", () => ({}));

import { EmbedPage, SharePage } from "./SharedViewPage";

const view = (over: Partial<SharedView> = {}): SharedView => ({
  kind: "dashboard",
  projectId: "p",
  projectName: "Demo project",
  dashboard: { id: "d", name: "Sales board", tabs: [], filters: [], linkGroups: [] },
  charts: [],
  pipelines: [],
  datasetIds: [],
  themes: [],
  generatedAt: "2026-10-31T00:00:00Z",
  ...over,
});

function open(path: string) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/share/:token" element={<SharePage />} />
          <Route path="/embed/:token" element={<EmbedPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  getSharedView.mockReset();
});

describe("shared views", () => {
  it("shows a shared dashboard read-only with its title, project and a print button", async () => {
    getSharedView.mockResolvedValue(view());
    open("/share/tok1");
    expect((await screen.findByTestId("dashboard")).getAttribute("data-mode")).toBe("view");
    expect(getSharedView).toHaveBeenCalledWith("tok1", "share", expect.anything());
    expect(screen.getByRole("heading", { name: "Sales board" })).toBeTruthy();
    expect(screen.getByText("Demo project")).toBeTruthy();
    expect(screen.getByRole("button", { name: /print/i })).toBeTruthy();
  });

  it("embeds without page chrome", async () => {
    getSharedView.mockResolvedValue(view());
    open("/embed/tok2");
    expect((await screen.findByTestId("dashboard")).getAttribute("data-mode")).toBe("embed");
    expect(getSharedView).toHaveBeenCalledWith("tok2", "embed", expect.anything());
    expect(screen.queryByRole("heading", { name: "Sales board" })).toBeNull();
    expect(screen.queryByRole("button", { name: /print/i })).toBeNull();
  });

  it("shows a single shared chart", async () => {
    getSharedView.mockResolvedValue(
      view({
        kind: "chart",
        dashboard: null,
        charts: [{ id: "c", name: "Only chart" } as SharedView["charts"][number]],
      }),
    );
    open("/share/tok3");
    expect((await screen.findByTestId("chart")).textContent).toBe("Only chart");
  });

  it("says a bad, revoked or expired link is not available without revealing which", async () => {
    getSharedView.mockRejectedValue(new ApiError("share link not found", 404));
    open("/share/nope");
    expect(await screen.findByRole("heading", { name: "This link isn't available" })).toBeTruthy();
  });

  it("reports other failures with their message", async () => {
    getSharedView.mockRejectedValue(new ApiError("backend exploded", 500));
    open("/share/boom");
    expect(await screen.findByText("backend exploded")).toBeTruthy();
  });
});
