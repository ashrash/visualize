import { beforeEach, describe, expect, it, vi } from "vitest";
import type { FollowHandlers } from "../api/jobs";
import type { Job, Project } from "../api/types";

const followers: { jobId: string; handlers: FollowHandlers; stopped: boolean }[] = [];

vi.mock("../api/jobs", async (original) => ({
  ...(await original<typeof import("../api/jobs")>()),
  followJob: (jobId: string, handlers: FollowHandlers) => {
    const entry = { jobId, handlers, stopped: false };
    followers.push(entry);
    return () => {
      entry.stopped = true;
    };
  },
}));
vi.mock("../api/datasets", () => ({
  getDataset: vi.fn(async (id: string) => ({ id, name: id })),
}));
vi.mock("../app/queryClient", () => ({
  queryClient: { invalidateQueries: vi.fn(async () => undefined) },
}));

import { getDataset } from "../api/datasets";
import { queryClient } from "../app/queryClient";
import { markLocalJob, useJobsStore } from "./jobs";
import { useProjectStore } from "./projectStore";

const project: Project = {
  id: "p",
  name: "P",
  schemaVersion: 1,
  revision: 1,
  datasetIds: [],
  pipelines: [],
  charts: [],
};

let counter = 0;
const job = (over: Partial<Job> = {}): Job => ({
  id: `j${++counter}`,
  kind: "import",
  title: "Import",
  status: "running",
  progress: null,
  message: null,
  seq: 1,
  createdAt: "2026-01-01T00:00:00Z",
  startedAt: null,
  finishedAt: null,
  error: null,
  result: null,
  ...over,
});
const done = (id: string, datasetId: string, kind: Job["kind"] = "import"): Job =>
  job({
    id,
    kind,
    status: "succeeded",
    seq: 9,
    result: { datasetId, version: 1, pipelineId: null, export: null, warnings: [] },
  });
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("jobs store", () => {
  beforeEach(() => {
    followers.length = 0;
    vi.mocked(queryClient.invalidateQueries).mockClear();
    vi.mocked(getDataset).mockClear();
    useProjectStore.getState().load(project);
  });

  it("settles an import whose dialog is long gone: attaches the dataset and refreshes the catalog", async () => {
    const started = job();
    const onSettled = vi.fn();
    markLocalJob(started.id);
    useJobsStore.getState().track(started, onSettled);
    // No component is involved from here on; only the stream talks to the store.
    followers[0]?.handlers.onJob(done(started.id, "d1"));
    await flush();
    expect(useProjectStore.getState().project?.datasetIds).toEqual(["d1"]);
    expect(queryClient.invalidateQueries).toHaveBeenCalledWith({ queryKey: ["datasets"] });
    expect(onSettled).toHaveBeenCalledTimes(1);
  });

  it("attaches every succeeded job of a multi-sheet import", async () => {
    const a = job();
    const b = job();
    markLocalJob(a.id);
    markLocalJob(b.id);
    useJobsStore.getState().track(a);
    useJobsStore.getState().track(b);
    followers[0]?.handlers.onJob(done(a.id, "d1"));
    followers[1]?.handlers.onJob(done(b.id, "d2"));
    await flush();
    expect(useProjectStore.getState().project?.datasetIds).toEqual(["d1", "d2"]);
  });

  it("an import started in another tab only lands in the catalog", async () => {
    const foreign = job(); // never marked as started here
    useJobsStore.getState().track(foreign);
    followers[0]?.handlers.onJob(done(foreign.id, "d1"));
    await flush();
    expect(useProjectStore.getState().project?.datasetIds).toEqual([]);
    expect(queryClient.invalidateQueries).toHaveBeenCalled();
  });

  it("an import started before switching projects is not attached to the new project", async () => {
    const mine = job();
    markLocalJob(mine.id);
    useProjectStore.getState().load({ ...project, id: "other" });
    useJobsStore.getState().track(mine);
    followers[0]?.handlers.onJob(done(mine.id, "d1"));
    await flush();
    expect(useProjectStore.getState().project?.datasetIds).toEqual([]);
  });

  it("refresh jobs refresh the catalog but do not add a dataset to the project", async () => {
    const r = job({ kind: "refresh" });
    useJobsStore.getState().track(r);
    followers[0]?.handlers.onJob(done(r.id, "d1", "refresh"));
    await flush();
    expect(useProjectStore.getState().project?.datasetIds).toEqual([]);
    expect(queryClient.invalidateQueries).toHaveBeenCalled();
  });

  it("settles a job only once even when it is tracked twice", async () => {
    const j = job();
    markLocalJob(j.id);
    useJobsStore.getState().track(j);
    useJobsStore.getState().track(j);
    for (const f of followers) f.handlers.onJob(done(j.id, "d1"));
    await flush();
    expect(getDataset).toHaveBeenCalledTimes(1);
    expect(useProjectStore.getState().project?.datasetIds).toEqual(["d1"]);
  });

  it("a lost stream settles the job as failed instead of hanging", () => {
    const j = job();
    const onSettled = vi.fn();
    useJobsStore.getState().track(j, onSettled);
    followers[0]?.handlers.onError?.("Lost connection to the job");
    expect(onSettled).toHaveBeenCalledWith(
      expect.objectContaining({ status: "failed", error: "Lost connection to the job" }),
    );
    expect(useJobsStore.getState().jobs[j.id]?.status).toBe("failed");
  });

  it("trackId reports a lost stream and returns a working stop function", () => {
    const onSettled = vi.fn();
    const stop = useJobsStore.getState().trackId("m1", onSettled);
    followers[0]?.handlers.onError?.("Lost connection to the job");
    expect(onSettled).toHaveBeenCalledWith(expect.objectContaining({ id: "m1", status: "failed" }));
    stop();
    expect(followers[0]?.stopped).toBe(true);
  });

  it("does not let an older snapshot overwrite a newer one", () => {
    const j = job({ seq: 5, status: "running" });
    useJobsStore.getState().merge(j);
    useJobsStore.getState().merge({ ...j, seq: 2, status: "queued" });
    expect(useJobsStore.getState().jobs[j.id]?.status).toBe("running");
  });
});
