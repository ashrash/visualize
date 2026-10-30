import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useJob } from "../data/useJob";
import { followJob, isTerminal } from "./jobs";
import type { Job } from "./types";

type Listener = (event: unknown) => void;

class MockEventSource {
  static instances: MockEventSource[] = [];
  readyState = 1;
  closed = false;
  private listeners = new Map<string, Listener[]>();
  constructor(readonly url: string) {
    MockEventSource.instances.push(this);
  }
  addEventListener(type: string, fn: Listener) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]);
  }
  close() {
    this.closed = true;
    this.readyState = 2;
  }
  emit(type: string, data?: unknown) {
    for (const fn of this.listeners.get(type) ?? []) fn({ data: JSON.stringify(data) });
  }
}

const job = (status: Job["status"], seq: number, extra: Partial<Job> = {}): Job => ({
  id: "j1",
  kind: "import",
  title: "Import",
  status,
  progress: null,
  message: null,
  seq,
  createdAt: "2026-01-01T00:00:00Z",
  startedAt: null,
  finishedAt: null,
  error: null,
  result: null,
  ...extra,
});
const source = () => MockEventSource.instances.at(-1) as MockEventSource;

describe("followJob", () => {
  beforeEach(() => {
    MockEventSource.instances = [];
  });

  it("connects to the job's event stream and forwards every snapshot", () => {
    const seen: Job[] = [];
    followJob("j 1", { onJob: (j) => seen.push(j) }, MockEventSource as never);
    expect(source().url).toBe("/api/jobs/j%201/events");
    source().emit("job", job("running", 1, { progress: 0.3 }));
    source().emit("job", job("running", 2, { progress: 0.6 }));
    expect(seen.map((j) => j.progress)).toEqual([0.3, 0.6]);
    expect(source().closed).toBe(false);
  });

  it("closes the stream at a terminal status and ignores later events", () => {
    const seen: Job[] = [];
    followJob("j1", { onJob: (j) => seen.push(j) }, MockEventSource as never);
    source().emit("job", job("succeeded", 3));
    expect(source().closed).toBe(true);
    source().emit("job", job("running", 4));
    expect(seen).toHaveLength(1);
  });

  it("reports a connection that closed before the job finished", () => {
    const onError = vi.fn();
    followJob("j1", { onJob: vi.fn(), onError }, MockEventSource as never);
    source().readyState = 2;
    source().emit("error");
    expect(onError).toHaveBeenCalledWith("Lost connection to the job");
  });

  it("does not treat a reconnecting source as a failure", () => {
    const onError = vi.fn();
    followJob("j1", { onJob: vi.fn(), onError }, MockEventSource as never);
    source().readyState = 0; // CONNECTING: EventSource retries by itself
    source().emit("error");
    expect(onError).not.toHaveBeenCalled();
  });

  it("stops following when asked", () => {
    const stop = followJob("j1", { onJob: vi.fn() }, MockEventSource as never);
    stop();
    expect(source().closed).toBe(true);
  });

  it("knows the terminal statuses", () => {
    expect(["queued", "running"].some((s) => isTerminal(s as Job["status"]))).toBe(false);
    expect(["succeeded", "failed", "cancelled"].every((s) => isTerminal(s as Job["status"]))).toBe(
      true,
    );
  });
});

describe("useJob", () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    vi.stubGlobal("EventSource", MockEventSource);
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("does nothing without a job id", () => {
    const { result } = renderHook(() => useJob(null));
    expect(result.current).toEqual({ job: null, error: null });
    expect(MockEventSource.instances).toHaveLength(0);
  });

  it("tracks snapshots and closes the stream on unmount", () => {
    const { result, unmount } = renderHook(() => useJob("j1"));
    act(() => source().emit("job", job("running", 1, { message: "Reading" })));
    expect(result.current.job?.message).toBe("Reading");
    act(() =>
      source().emit(
        "job",
        job("succeeded", 2, {
          result: { datasetId: "d1", version: 1, pipelineId: null, export: null, warnings: [] },
        }),
      ),
    );
    expect(result.current.job?.result?.datasetId).toBe("d1");
    expect(source().closed).toBe(true);
    unmount();
  });

  it("follows a new job when the id changes", () => {
    const { rerender } = renderHook(({ id }) => useJob(id), { initialProps: { id: "a" } });
    const first = source();
    rerender({ id: "b" });
    expect(first.closed).toBe(true);
    expect(source().url).toBe("/api/jobs/b/events");
  });

  it("surfaces a lost connection", () => {
    const { result } = renderHook(() => useJob("j1"));
    act(() => {
      source().readyState = 2;
      source().emit("error");
    });
    expect(result.current.error).toBe("Lost connection to the job");
  });
});
