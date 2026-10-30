import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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
  deleteStaged: vi.fn(async () => undefined),
  getJob: vi.fn(),
}));
vi.mock("../app/queryClient", () => ({
  queryClient: { invalidateQueries: vi.fn(async () => undefined) },
}));

import { deleteStaged, getDataset, getJob } from "../api/datasets";
import { queryClient } from "../app/queryClient";
import {
  addStagedJob,
  beginStagedCleanup,
  closeStagedWizard,
  markLocalJob,
  resetLocalJobs,
  startLocalJob,
  useJobsStore,
} from "./jobs";
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
    sessionStorage.clear();
    resetLocalJobs();
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

  it("does not let an older snapshot overwrite a newer one", () => {
    const j = job({ seq: 5, status: "running" });
    useJobsStore.getState().merge(j);
    useJobsStore.getState().merge({ ...j, seq: 2, status: "queued" });
    expect(useJobsStore.getState().jobs[j.id]?.status).toBe("running");
  });

  describe("staged upload cleanup", () => {
    beforeEach(() => vi.mocked(deleteStaged).mockClear());
    const started = (stagedId: string) => {
      const j = job();
      markLocalJob(j.id);
      addStagedJob(stagedId, j);
      useJobsStore.getState().track(j);
      return j;
    };

    it("deletes the staged upload only after the last import settles successfully", async () => {
      beginStagedCleanup("stg1");
      const a = started("stg1");
      const b = started("stg1");
      followers[0]?.handlers.onJob(done(a.id, "d1"));
      await flush();
      expect(deleteStaged).not.toHaveBeenCalled(); // b is still reading the file
      followers[1]?.handlers.onJob(done(b.id, "d2"));
      await flush();
      expect(deleteStaged).toHaveBeenCalledTimes(1);
      expect(vi.mocked(deleteStaged).mock.calls[0]?.[0]).toBe("stg1");
    });

    it("keeps the upload after a failed or cancelled import while the wizard is open", async () => {
      beginStagedCleanup("stg2");
      const a = started("stg2");
      followers[0]?.handlers.onJob({ ...a, status: "cancelled", seq: 4 });
      await flush();
      expect(deleteStaged).not.toHaveBeenCalled(); // "Back to options" must still find it
      // A retry from the same upload that succeeds still leaves it for the wizard's close().
      const retry = started("stg2");
      followers[1]?.handlers.onJob(done(retry.id, "d1"));
      await flush();
      expect(deleteStaged).not.toHaveBeenCalled();
      closeStagedWizard("stg2");
      expect(deleteStaged).toHaveBeenCalledWith("stg2");
    });

    it("once the wizard is closed, any terminal status deletes it (after the last job)", async () => {
      beginStagedCleanup("stg3");
      const a = started("stg3");
      const b = started("stg3");
      closeStagedWizard("stg3"); // closed mid-import: the store owns the cleanup now
      expect(deleteStaged).not.toHaveBeenCalled();
      followers[0]?.handlers.onJob({ ...a, status: "failed", error: "x", seq: 4 });
      await flush();
      expect(deleteStaged).not.toHaveBeenCalled(); // b is still running
      followers[1]?.handlers.onJob({ ...b, status: "cancelled", seq: 4 });
      await flush();
      expect(deleteStaged).toHaveBeenCalledWith("stg3");
    });

    it("deletes at close when nothing was started, and ignores a failing delete", async () => {
      vi.mocked(deleteStaged).mockRejectedValueOnce(new Error("409 busy"));
      beginStagedCleanup("stg4");
      expect(() => closeStagedWizard("stg4")).not.toThrow();
      expect(deleteStaged).toHaveBeenCalledWith("stg4");
      await flush();
    });

    it("counts a job that already settled when it is registered", async () => {
      beginStagedCleanup("stg5");
      const a = job();
      markLocalJob(a.id);
      useJobsStore.getState().track(a);
      followers[0]?.handlers.onJob(done(a.id, "d1")); // settles before the POST handler registers it
      await flush();
      addStagedJob("stg5", done(a.id, "d1"));
      expect(deleteStaged).toHaveBeenCalledWith("stg5"); // all succeeded: delete right away
    });
  });

  describe("starting a job from this tab", () => {
    it("an early sheet that settles before its POST response is processed still attaches", async () => {
      let respond: (j: Job) => void = () => undefined;
      const pending = new Promise<Job>((resolve) => {
        respond = resolve;
      });
      const j = job();
      const starting = startLocalJob(() => pending);
      // Meanwhile the poll found the job and its stream already reports success.
      useJobsStore.getState().track(j);
      followers[0]?.handlers.onJob(done(j.id, "early-ds"));
      await flush();
      expect(useProjectStore.getState().project?.datasetIds).toEqual([]); // not decided yet
      respond(j);
      await starting;
      await flush();
      await flush();
      expect(useProjectStore.getState().project?.datasetIds).toEqual(["early-ds"]);
    });

    it("remembers the project across a reload (sessionStorage) so the attach still happens", async () => {
      const j = job();
      markLocalJob(j.id);
      expect(JSON.parse(sessionStorage.getItem("viz.localJobs") ?? "{}")).toEqual({ [j.id]: "p" });
      // The page reloads: module state is gone, sessionStorage and the reopened project remain.
      resetLocalJobs();
      useProjectStore.getState().load(project);
      useJobsStore.getState().track(j); // found again by the active-jobs poll
      followers[0]?.handlers.onJob(done(j.id, "after-reload"));
      await flush();
      expect(useProjectStore.getState().project?.datasetIds).toEqual(["after-reload"]);
    });

    it("survives unreadable sessionStorage content", () => {
      sessionStorage.setItem("viz.localJobs", "{not json");
      resetLocalJobs();
      expect(() => markLocalJob("x")).not.toThrow();
    });
  });

  describe("cleanup details", () => {
    beforeEach(() => {
      vi.useFakeTimers();
      vi.mocked(deleteStaged).mockReset().mockResolvedValue(undefined);
    });
    afterEach(() => vi.useRealTimers());

    it("retries a 409 from DELETE staged once after a few seconds, then gives up quietly", async () => {
      const { ApiError } = await import("../api/client");
      vi.mocked(deleteStaged).mockRejectedValue(new ApiError("busy", 409));
      beginStagedCleanup("stg-409");
      closeStagedWizard("stg-409");
      await vi.advanceTimersByTimeAsync(0);
      expect(deleteStaged).toHaveBeenCalledTimes(1);
      await vi.advanceTimersByTimeAsync(3100);
      expect(deleteStaged).toHaveBeenCalledTimes(2);
      await vi.advanceTimersByTimeAsync(60_000);
      expect(deleteStaged).toHaveBeenCalledTimes(2); // no endless retries
    });

    it("does not retry a 404", async () => {
      const { ApiError } = await import("../api/client");
      vi.mocked(deleteStaged).mockRejectedValue(new ApiError("gone", 404));
      beginStagedCleanup("stg-404");
      closeStagedWizard("stg-404");
      await vi.advanceTimersByTimeAsync(10_000);
      expect(deleteStaged).toHaveBeenCalledTimes(1);
    });

    it("closing waits for a start that is still in flight, then registers its job", async () => {
      let respond: (j: Job) => void = () => undefined;
      const pending = new Promise<Job>((resolve) => {
        respond = resolve;
      });
      beginStagedCleanup("stg-wait");
      const j = job();
      void startLocalJob(
        () => pending,
        (started) => addStagedJob("stg-wait", started),
        "stg-wait",
      );
      closeStagedWizard("stg-wait");
      await vi.advanceTimersByTimeAsync(0);
      expect(deleteStaged).not.toHaveBeenCalled();
      respond(j);
      await vi.advanceTimersByTimeAsync(0);
      expect(deleteStaged).not.toHaveBeenCalled(); // the job is now the one holding it
      useJobsStore.getState().track(j);
      followers.at(-1)?.handlers.onJob({ ...j, status: "cancelled", seq: 3 });
      await vi.advanceTimersByTimeAsync(0);
      expect(deleteStaged).toHaveBeenCalledWith("stg-wait");
    });

    it("forgets the persisted job entry once the attach decision is made", async () => {
      const j = job();
      markLocalJob(j.id);
      expect(Object.keys(JSON.parse(sessionStorage.getItem("viz.localJobs") ?? "{}"))).toContain(
        j.id,
      );
      useJobsStore.getState().track(j);
      followers.at(-1)?.handlers.onJob(done(j.id, "d-forget"));
      await vi.advanceTimersByTimeAsync(10);
      expect(useProjectStore.getState().project?.datasetIds).toEqual(["d-forget"]);
      expect(
        Object.keys(JSON.parse(sessionStorage.getItem("viz.localJobs") ?? "{}")),
      ).not.toContain(j.id);
    });
  });

  describe("a lost stream", () => {
    beforeEach(() => {
      vi.useFakeTimers();
      vi.mocked(getJob).mockReset();
      vi.mocked(deleteStaged).mockClear();
    });
    afterEach(() => vi.useRealTimers());

    it("falls back to polling the job and settles normally once it is terminal", async () => {
      const j = job();
      markLocalJob(j.id);
      beginStagedCleanup("stg6");
      addStagedJob("stg6", j);
      closeStagedWizard("stg6");
      const onSettled = vi.fn();
      useJobsStore.getState().track(j, onSettled);
      vi.mocked(getJob)
        .mockResolvedValueOnce({ ...j, seq: 2 })
        .mockResolvedValue(done(j.id, "polled-ds"));
      followers[0]?.handlers.onError?.("Lost connection to the job");
      await vi.advanceTimersByTimeAsync(2100);
      expect(onSettled).not.toHaveBeenCalled(); // still running
      await vi.advanceTimersByTimeAsync(2100);
      expect(onSettled).toHaveBeenCalledWith(expect.objectContaining({ status: "succeeded" }));
      expect(useProjectStore.getState().project?.datasetIds).toEqual(["polled-ds"]);
      expect(deleteStaged).toHaveBeenCalledWith("stg6"); // cleanup ran too
    });

    it("gives up after repeated failures and settles the job as failed", async () => {
      const j = job();
      const onSettled = vi.fn();
      useJobsStore.getState().track(j, onSettled);
      vi.mocked(getJob).mockRejectedValue(new Error("404"));
      followers[0]?.handlers.onError?.("Lost connection to the job");
      await vi.advanceTimersByTimeAsync(7000);
      expect(onSettled).toHaveBeenCalledWith(
        expect.objectContaining({ status: "failed", error: "Lost connection to the job" }),
      );
    });

    it("shares one poll per job across followers", async () => {
      const j = job();
      const a = vi.fn();
      const b = vi.fn();
      useJobsStore.getState().track(j, a);
      useJobsStore.getState().trackId(j.id, b);
      for (const f of followers) f.handlers.onError?.("Lost connection to the job");
      vi.mocked(getJob).mockResolvedValue(done(j.id, "shared-ds"));
      await vi.advanceTimersByTimeAsync(2100);
      expect(getJob).toHaveBeenCalledTimes(1); // not once per follower
      expect(a).toHaveBeenCalledWith(expect.objectContaining({ status: "succeeded" }));
      expect(b).toHaveBeenCalledWith(expect.objectContaining({ status: "succeeded" }));
    });

    it("resetLocalJobs clears running poll timers", async () => {
      const j = job();
      useJobsStore.getState().track(j);
      followers.at(-1)?.handlers.onError?.("Lost connection to the job");
      resetLocalJobs();
      vi.mocked(getJob).mockResolvedValue(done(j.id, "x"));
      await vi.advanceTimersByTimeAsync(6000);
      expect(getJob).not.toHaveBeenCalled();
    });

    it("stops polling when the caller stops", async () => {
      const j = job();
      const stop = useJobsStore.getState().track(j);
      followers[0]?.handlers.onError?.("Lost connection to the job");
      stop();
      vi.mocked(getJob).mockResolvedValue(done(j.id, "x"));
      await vi.advanceTimersByTimeAsync(6000);
      expect(getJob).not.toHaveBeenCalled();
    });
  });
});
