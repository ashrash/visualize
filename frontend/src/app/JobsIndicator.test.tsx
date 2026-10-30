import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetJobStreams } from "../api/jobs";
import type { Job, Project } from "../api/types";
import { markLocalJob, useJobsStore } from "../state/jobs";
import { useProjectStore } from "../state/projectStore";
import { JobsIndicator } from "./JobsIndicator";
import { queryClient } from "./queryClient";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readyState = 1;
  private listeners = new Map<string, ((event: { data: string }) => void)[]>();
  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, fn: (event: { data: string }) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]);
  }
  close() {
    this.readyState = 2;
  }
  emit(job: Job) {
    for (const fn of this.listeners.get("job") ?? []) fn({ data: JSON.stringify(job) });
  }
}

const running: Job = {
  id: "other-tab-job",
  kind: "import",
  title: "Import big.csv",
  status: "running",
  progress: 0.4,
  message: "Converting",
  seq: 3,
  createdAt: "2026-01-01T00:00:00Z",
  startedAt: null,
  finishedAt: null,
  error: null,
  result: null,
};
const project: Project = {
  id: "p",
  name: "P",
  schemaVersion: 1,
  revision: 1,
  datasetIds: [],
  pipelines: [],
  charts: [],
};

const requests: { url: string; method: string }[] = [];

beforeEach(() => {
  resetJobStreams();
  FakeEventSource.instances = [];
  requests.length = 0;
  useJobsStore.setState({ jobs: {} });
  useProjectStore.getState().load(project);
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const request = typeof input === "string" || input instanceof URL ? null : input;
      const url = new URL(request ? request.url : String(input));
      requests.push({ url: url.pathname + url.search, method: request?.method ?? "GET" });
      const dataset = url.pathname.match(/^\/api\/datasets\/(.+)$/);
      const body =
        url.pathname === "/api/jobs"
          ? [running]
          : dataset
            ? {
                id: dataset[1],
                name: `dataset ${dataset[1]}`,
                source: { kind: "csv", fileName: "x.csv" },
              }
            : { ...running, status: "cancelled" };
      return new Response(JSON.stringify(body), {
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const renderIndicator = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <JobsIndicator />
    </QueryClientProvider>,
  );

describe("JobsIndicator", () => {
  it("shows a running job started elsewhere and can cancel it", async () => {
    renderIndicator();
    const button = await screen.findByRole("button", { name: "1 running jobs" });
    fireEvent.click(button);
    expect(await screen.findByText("Import big.csv")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(requests).toContainEqual({ url: "/api/jobs/other-tab-job/cancel", method: "POST" }),
    );
  });

  it("a foreign job the poll found settles into the catalog, not into this project", async () => {
    const invalidate = vi.spyOn(queryClient, "invalidateQueries").mockResolvedValue();
    renderIndicator();
    await screen.findByRole("button", { name: "1 running jobs" });
    const stream = FakeEventSource.instances.find((s) => s.url.includes("other-tab-job"));
    expect(stream).toBeTruthy();

    // The stream reports success: settlement runs for real (catalog refresh + dataset lookup).
    stream?.emit({
      ...running,
      status: "succeeded",
      seq: 9,
      result: { datasetId: "foreign-ds", version: 1, pipelineId: null, export: null, warnings: [] },
    });
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ["datasets"] }));
    await waitFor(() => expect(requests.map((r) => r.url)).toContain("/api/datasets/foreign-ds"));
    await new Promise((r) => setTimeout(r, 20));
    expect(useProjectStore.getState().project?.datasetIds).toEqual([]);
    invalidate.mockRestore();
  });

  it("a job this tab started does attach to the project it was started from", async () => {
    const invalidate = vi.spyOn(queryClient, "invalidateQueries").mockResolvedValue();
    const mine: Job = { ...running, id: "my-job", title: "Import mine.csv", seq: 1 };
    markLocalJob(mine.id);
    useJobsStore.getState().track(mine);
    const stream = FakeEventSource.instances.find((s) => s.url.includes("my-job"));
    stream?.emit({
      ...mine,
      status: "succeeded",
      seq: 5,
      result: { datasetId: "my-ds", version: 1, pipelineId: null, export: null, warnings: [] },
    });
    await waitFor(() => expect(useProjectStore.getState().project?.datasetIds).toEqual(["my-ds"]));
    expect(invalidate).toHaveBeenCalled();
    invalidate.mockRestore();
  });

  it("renders nothing when no job is running", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("[]", { headers: { "Content-Type": "application/json" } })),
    );
    const { container } = renderIndicator();
    await new Promise((r) => setTimeout(r, 20));
    expect(container.textContent).toBe("");
  });
});
