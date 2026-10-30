import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetJobStreams } from "../api/jobs";
import type { Job, Project } from "../api/types";
import { useJobsStore } from "../state/jobs";
import { useProjectStore } from "../state/projectStore";
import { JobsIndicator } from "./JobsIndicator";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readyState = 1;
  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener() {}
  close() {
    this.readyState = 2;
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
      const body = url.pathname === "/api/jobs" ? [running] : { ...running, status: "cancelled" };
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

  it("follows the job over a shared stream and does not attach it to this project", async () => {
    renderIndicator();
    await screen.findByRole("button", { name: "1 running jobs" });
    expect(FakeEventSource.instances.map((s) => s.url)).toEqual(["/api/jobs/other-tab-job/events"]);
    // Settling a job this tab never started must leave the project untouched.
    useJobsStore.getState().merge({ ...running, status: "succeeded", seq: 9 });
    expect(useProjectStore.getState().project?.datasetIds).toEqual([]);
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
