import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FollowHandlers } from "../api/jobs";
import type { Job, StagedFile } from "../api/types";

const followers: { jobId: string; handlers: FollowHandlers }[] = [];

vi.mock("../api/jobs", async (original) => ({
  ...(await original<typeof import("../api/jobs")>()),
  followJob: (jobId: string, handlers: FollowHandlers) => {
    followers.push({ jobId, handlers });
    return () => undefined;
  },
}));
vi.mock("../api/datasets", () => ({
  stageFile: vi.fn(),
  startImport: vi.fn(),
  deleteStaged: vi.fn(async () => undefined),
  getDataset: vi.fn(async (id: string) => ({
    id,
    name: id,
    source: { kind: "csv", fileName: "x" },
  })),
  cancelJob: vi.fn(async () => undefined),
  getJob: vi.fn(),
}));
vi.mock("../api/datasetRows", () => ({ previewImport: vi.fn() }));
vi.mock("../app/queryClient", () => ({
  queryClient: { invalidateQueries: vi.fn(async () => undefined) },
}));

import { previewImport } from "../api/datasetRows";
import { deleteStaged, stageFile, startImport } from "../api/datasets";
import { useToastStore } from "../components/ui/toast";
import { resetLocalJobs, useJobsStore } from "../state/jobs";
import { useProjectStore } from "../state/projectStore";
import { ImportWizardDialog } from "./ImportWizard";

const staged: StagedFile = {
  stagedId: "stg-1",
  fileName: "sales.csv",
  kind: "csv",
  sizeBytes: 10,
  sheets: [],
};
const preview = {
  columns: { a: [1, 2] },
  kinds: { a: "number" as const },
  rowCount: 2,
  meta: {
    kind: "csv" as const,
    detected: {},
    sheets: [],
    columns: [
      {
        name: "a",
        type: "BIGINT",
        semantic: "quantitative" as const,
        nullCount: 0,
        approxDistinct: 2,
      },
    ],
    rowsShown: 2,
    estimatedRows: 2,
    warnings: [],
  },
};
let counter = 0;
const job = (over: Partial<Job> = {}): Job => ({
  id: `job-${++counter}`,
  kind: "import",
  title: "Import sales",
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

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

function setup() {
  const onImported = vi.fn();
  const onOpenChange = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ImportWizardDialog open onOpenChange={onOpenChange} onImported={onImported} />
    </QueryClientProvider>,
  );
  return { onImported, onOpenChange };
}

async function chooseFileAndImport() {
  const input = document.querySelector<HTMLInputElement>("input[type=file]");
  if (!input) throw new Error("no file input");
  fireEvent.change(input, { target: { files: [new File(["a\n1\n2"], "sales.csv")] } });
  await screen.findByLabelText("Dataset name");
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Import" })).toHaveProperty("disabled", false),
  );
  fireEvent.click(screen.getByRole("button", { name: "Import" }));
}

/** Push a snapshot into the most recent stream for a job, as the SSE connection would. */
function emit(j: Job) {
  const follower = followers.findLast((f) => f.jobId === j.id);
  if (!follower) throw new Error(`no stream for ${j.id}`);
  act(() => follower.handlers.onJob(j));
}

beforeEach(() => {
  followers.length = 0;
  sessionStorage.clear();
  resetLocalJobs();
  useJobsStore.setState({ jobs: {} });
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  vi.mocked(stageFile).mockReset().mockResolvedValue(staged);
  vi.mocked(startImport).mockReset();
  vi.mocked(deleteStaged).mockClear();
  vi.mocked(previewImport).mockReset().mockResolvedValue(preview);
  useProjectStore.getState().load({
    id: "p",
    name: "P",
    schemaVersion: 1,
    revision: 1,
    datasetIds: [],
    pipelines: [],
    charts: [],
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("import wizard: back to options after a cancel or failure", () => {
  it("cancel -> back previews the same staged upload and can import again", async () => {
    const first = job();
    const second = job();
    vi.mocked(startImport).mockResolvedValueOnce(first).mockResolvedValueOnce(second);
    setup();
    await chooseFileAndImport();
    await screen.findByLabelText("Import progress");

    emit({ ...first, status: "cancelled", seq: 5 });
    expect(await screen.findByText("Import of sales was cancelled")).toBeTruthy();
    // A user-initiated cancel is not an error: neutral status, not a red alert.
    expect(screen.queryByRole("alert")).toBeNull();
    await new Promise((r) => setTimeout(r, 20));
    expect(deleteStaged).not.toHaveBeenCalled(); // the upload must survive for "Back"

    vi.mocked(previewImport).mockClear();
    fireEvent.click(screen.getByRole("button", { name: "Back to options" }));
    await waitFor(() => expect(previewImport).toHaveBeenCalled());
    expect(vi.mocked(previewImport).mock.calls[0]?.[0]).toMatchObject({ stagedId: "stg-1" });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Import" })).toHaveProperty("disabled", false),
    );

    fireEvent.click(screen.getByRole("button", { name: "Import" }));
    await waitFor(() => expect(startImport).toHaveBeenCalledTimes(2));
    expect(vi.mocked(startImport).mock.calls[1]?.[0]).toMatchObject({ stagedId: "stg-1" });
    expect(stageFile).toHaveBeenCalledTimes(1); // staged once, never re-uploaded
  });

  it("failure -> back -> change delimiter -> import succeeds and then frees the upload when the wizard closes", async () => {
    const first = job();
    const second = job();
    vi.mocked(startImport).mockResolvedValueOnce(first).mockResolvedValueOnce(second);
    setup();
    await chooseFileAndImport();
    await screen.findByLabelText("Import progress");

    emit({ ...first, status: "failed", error: "could not parse the file", seq: 5 });
    expect(await screen.findByRole("alert")).toBeTruthy();
    await new Promise((r) => setTimeout(r, 20));
    expect(deleteStaged).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Back to options" }));
    await screen.findByLabelText("Delimiter");
    fireEvent.change(screen.getByLabelText("Delimiter"), { target: { value: ";" } });
    await waitFor(() =>
      expect(vi.mocked(previewImport).mock.calls.at(-1)?.[0]).toMatchObject({
        stagedId: "stg-1",
        options: { delimiter: ";" },
      }),
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Import" })).toHaveProperty("disabled", false),
    );

    fireEvent.click(screen.getByRole("button", { name: "Import" }));
    await waitFor(() => expect(startImport).toHaveBeenCalledTimes(2));
    expect(vi.mocked(startImport).mock.calls[1]?.[0]).toMatchObject({
      stagedId: "stg-1",
      options: { delimiter: ";" },
    });

    emit({
      ...second,
      status: "succeeded",
      seq: 6,
      result: { datasetId: "ds-1", version: 1, pipelineId: null, export: null, warnings: [] },
    });
    await waitFor(() => expect(screen.getByText(/Imported 1 dataset/)).toBeTruthy());
    expect(useProjectStore.getState().project?.datasetIds).toEqual(["ds-1"]);
    // An earlier failure kept the upload for retries; the automatic close after success
    // releases it, like the close button does.
    await waitFor(() => expect(deleteStaged).toHaveBeenCalledWith("stg-1"));
  });

  it("closing the wizard mid-import leaves the delete to the jobs store, after the job settles", async () => {
    const running = job();
    vi.mocked(startImport).mockResolvedValueOnce(running);
    const { onOpenChange } = setup();
    await chooseFileAndImport();
    await screen.findByLabelText("Import progress");

    fireEvent.click(screen.getAllByRole("button", { name: "Close" })[0] as HTMLElement);
    expect(onOpenChange).toHaveBeenCalledWith(false);
    await new Promise((r) => setTimeout(r, 20));
    expect(deleteStaged).not.toHaveBeenCalled(); // the import is still reading it

    emit({ ...running, status: "cancelled", seq: 4 });
    await waitFor(() => expect(deleteStaged).toHaveBeenCalledWith("stg-1"));
  });
});

describe("closing the wizard while requests are in flight (race409 scenarios)", () => {
  const closeWizard = () =>
    fireEvent.click(screen.getAllByRole("button", { name: "Close" })[0] as HTMLElement);
  const deferred = <T,>() => {
    let resolve: (value: T) => void = () => undefined;
    let reject: (reason: unknown) => void = () => undefined;
    const promise = new Promise<T>((res, rej) => {
      resolve = res;
      reject = rej;
    });
    return { promise, resolve, reject };
  };

  it("A: close during the first preview aborts it and frees the upload", async () => {
    let previewSignal: AbortSignal | undefined;
    vi.mocked(previewImport).mockImplementation(((_request: unknown, signal?: AbortSignal) => {
      previewSignal = signal;
      return new Promise(() => undefined);
    }) as never);
    setup();
    const input = document.querySelector<HTMLInputElement>("input[type=file]");
    if (!input) throw new Error("no file input");
    fireEvent.change(input, { target: { files: [new File(["a"], "sales.csv")] } });
    await screen.findByLabelText("Dataset name");
    await waitFor(() => expect(previewSignal).toBeTruthy());
    closeWizard();
    await waitFor(() => expect(deleteStaged).toHaveBeenCalledWith("stg-1"));
    expect(previewSignal?.aborted).toBe(true);
  });

  it("B: close while POST /import is in flight lets the import finish, attach, and then frees the upload", async () => {
    const reply = deferred<Job>();
    vi.mocked(startImport).mockReturnValueOnce(reply.promise);
    setup();
    await chooseFileAndImport();
    await waitFor(() => expect(startImport).toHaveBeenCalled());
    closeWizard();
    await new Promise((r) => setTimeout(r, 20));
    // The request was not abandoned, and the upload is not deleted under it.
    expect(vi.mocked(startImport).mock.calls[0]?.[1]).toBeUndefined();
    expect(deleteStaged).not.toHaveBeenCalled();

    const running = job();
    reply.resolve(running);
    await waitFor(() =>
      expect(useToastStore.getState().toasts.map((x) => x.title)).toContain(
        "Import of sales continues in the background",
      ),
    );
    expect(deleteStaged).not.toHaveBeenCalled(); // the job is running now
    emit({
      ...running,
      status: "succeeded",
      seq: 5,
      result: { datasetId: "late-ds", version: 1, pipelineId: null, export: null, warnings: [] },
    });
    await waitFor(() => expect(deleteStaged).toHaveBeenCalledWith("stg-1"));
    // It was started here, so it joins the project even though the dialog is gone.
    expect(useProjectStore.getState().project?.datasetIds).toEqual(["late-ds"]);
  });

  it("B2: a rejected POST after close just frees the upload", async () => {
    const reply = deferred<Job>();
    vi.mocked(startImport).mockReturnValueOnce(reply.promise);
    setup();
    await chooseFileAndImport();
    await waitFor(() => expect(startImport).toHaveBeenCalled());
    closeWizard();
    reply.reject(new Error("500"));
    await waitFor(() => expect(deleteStaged).toHaveBeenCalledWith("stg-1"));
  });

  it("C: cancel then close frees the upload once the job is cancelled", async () => {
    const running = job();
    vi.mocked(startImport).mockResolvedValueOnce(running);
    setup();
    await chooseFileAndImport();
    await screen.findByLabelText("Import progress");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    emit({ ...running, status: "cancelled", seq: 4 });
    await screen.findByText("Import of sales was cancelled");
    closeWizard();
    await waitFor(() => expect(deleteStaged).toHaveBeenCalledWith("stg-1"));
  });

  it("D: change an option (new preview in flight) then close aborts it and frees the upload", async () => {
    setup();
    const input = document.querySelector<HTMLInputElement>("input[type=file]");
    if (!input) throw new Error("no file input");
    fireEvent.change(input, { target: { files: [new File(["a"], "sales.csv")] } });
    await screen.findByLabelText("Dataset name");
    await waitFor(() => expect(previewImport).toHaveBeenCalledTimes(1));
    let secondSignal: AbortSignal | undefined;
    vi.mocked(previewImport).mockImplementation(((_request: unknown, signal?: AbortSignal) => {
      secondSignal = signal;
      return new Promise(() => undefined);
    }) as never);
    fireEvent.change(screen.getByLabelText("Delimiter"), { target: { value: ";" } });
    await waitFor(() => expect(secondSignal).toBeTruthy());
    closeWizard();
    await waitFor(() => expect(deleteStaged).toHaveBeenCalledWith("stg-1"));
    expect(secondSignal?.aborted).toBe(true);
  });
});
