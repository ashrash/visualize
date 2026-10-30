import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { Project } from "../api/types";
import { useSaveState } from "../state/autosave";
import { useProjectStore } from "../state/projectStore";

vi.mock("../api/projects", () => ({
  getProject: vi.fn(),
  saveProject: vi.fn(),
  createProject: vi.fn(),
  listProjects: vi.fn(),
}));

import { createProject, getProject, saveProject } from "../api/projects";
import { createAndOpenProject, openProject, UnsavedChangesError } from "./useProjectSession";

const project = (id: string, revision = 1): Project => ({
  id,
  name: id,
  schemaVersion: 1,
  revision,
  datasetIds: [],
  pipelines: [],
  charts: [],
});
const edit = () => useProjectStore.getState().update((p) => ({ ...p, name: `${p.name}!` }));
const store = () => useProjectStore.getState();

describe("switching projects", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(getProject).mockReset();
    vi.mocked(saveProject).mockReset();
    vi.mocked(createProject).mockReset();
    // Stored by id, echoing back a bumped revision.
    vi.mocked(saveProject).mockImplementation(async (p) => ({ ...p, revision: p.revision + 1 }));
    vi.mocked(getProject).mockImplementation(async (id) => project(id, 7));
    store().load(project("a"));
    useSaveState.getState().setStatus("saved");
  });
  afterEach(() => vi.useRealTimers());

  it("saves pending edits before opening another project", async () => {
    edit();
    await openProject("b");
    expect(saveProject).toHaveBeenCalledTimes(1);
    expect(vi.mocked(saveProject).mock.calls[0]?.[0].id).toBe("a");
    expect(store().project?.id).toBe("b");
    expect(store().revision).toBe(7);
  });

  it("(B) refuses to switch when the save fails, keeping the dirty project", async () => {
    vi.mocked(saveProject).mockRejectedValue(new ApiError("server error", 500));
    edit();
    await expect(openProject("b")).rejects.toBeInstanceOf(UnsavedChangesError);
    expect(getProject).not.toHaveBeenCalled();
    expect(store().project?.id).toBe("a");
    expect(store().dirty).toBe(true);
  });

  it("(B) refuses to switch after a 409 conflict too", async () => {
    vi.mocked(saveProject).mockRejectedValue(new ApiError("stale", 409));
    edit();
    await expect(openProject("b")).rejects.toBeInstanceOf(UnsavedChangesError);
    await expect(createAndOpenProject("New")).rejects.toBeInstanceOf(UnsavedChangesError);
    expect(createProject).not.toHaveBeenCalled();
    expect(store().project?.id).toBe("a");
  });

  it("switches anyway when the caller confirmed discarding", async () => {
    vi.mocked(saveProject).mockRejectedValue(new ApiError("server error", 500));
    edit();
    await openProject("b", { discard: true });
    expect(store().project?.id).toBe("b");
    expect(store().dirty).toBe(false);
  });

  it("(A) saves an edit typed while another save was in flight", async () => {
    let release: (p: Project) => void = () => undefined;
    vi.mocked(saveProject)
      .mockImplementationOnce(() => new Promise<Project>((resolve) => (release = resolve)))
      .mockImplementation(async (p) => ({ ...p, revision: p.revision + 1 }));
    edit();
    const opening = openProject("b"); // starts the first save
    await vi.advanceTimersByTimeAsync(0);
    edit(); // typed during the save
    release({ ...project("a"), revision: 2 });
    await opening;
    expect(saveProject).toHaveBeenCalledTimes(2);
    expect(store().project?.id).toBe("b");
  });

  it("(C) a slow load does not let the old project's timer clobber the new one", async () => {
    let finishLoad: (p: Project) => void = () => undefined;
    vi.mocked(getProject).mockImplementation(
      () => new Promise((resolve) => (finishLoad = resolve)),
    );
    // Nothing dirty: opening goes straight to the (slow) fetch.
    const opening = openProject("b");
    await vi.advanceTimersByTimeAsync(5000);
    finishLoad(project("b", 7));
    await opening;
    await vi.advanceTimersByTimeAsync(5000);
    expect(saveProject).not.toHaveBeenCalled();
    expect(store().project?.id).toBe("b");
    expect(store().revision).toBe(7);
  });
});
