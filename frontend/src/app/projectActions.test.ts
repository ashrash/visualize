import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Project } from "../api/types";
import { autosaver, useSaveState } from "../state/autosave";
import { COALESCE_MS, undo, useProjectStore } from "../state/projectStore";

vi.mock("../api/projects", () => ({
  getProject: vi.fn(),
  saveProject: vi.fn(),
  createProject: vi.fn(),
  listProjects: vi.fn(),
  renameProject: vi.fn(),
  deleteProject: vi.fn(),
  duplicateProject: vi.fn(),
  restoreVersion: vi.fn(),
  exportProjectUrl: (id: string) => `/api/projects/${id}/export`,
}));

import { deleteProject, renameProject, saveProject } from "../api/projects";
import {
  deleteCurrentProject,
  downloadProjectExport,
  renameCurrentProject,
} from "./projectActions";

const project = (name = "Old"): Project => ({
  id: "p",
  name,
  schemaVersion: 1,
  revision: 1,
  datasetIds: [],
  pipelines: [],
  charts: [],
});
const store = () => useProjectStore.getState();
const edit = () => store().update((p) => ({ ...p, datasetIds: [...(p.datasetIds ?? []), "d"] }));

describe("project actions", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(saveProject).mockReset();
    vi.mocked(renameProject).mockReset();
    vi.mocked(deleteProject).mockReset();
    vi.mocked(saveProject).mockImplementation(async (p) => ({ ...p, revision: p.revision + 1 }));
    vi.mocked(renameProject).mockImplementation(async (_id, name, revision) => ({
      ...project(name),
      revision: revision + 1,
    }));
    store().load(project());
    useSaveState.getState().setStatus("saved");
  });
  afterEach(() => vi.useRealTimers());

  it("undoing an earlier edit after a rename keeps the new name, and no save sends the old one", async () => {
    edit();
    vi.advanceTimersByTime(COALESCE_MS + 1);
    edit();
    await renameCurrentProject("Q3");
    vi.mocked(saveProject).mockClear(); // saves before the rename legitimately carry the old name
    expect(store().project?.name).toBe("Q3");
    undo();
    expect(store().project?.name).toBe("Q3");
    await autosaver.flush(false);
    for (const [saved] of vi.mocked(saveProject).mock.calls) expect(saved.name).not.toBe("Old");
    expect(vi.mocked(saveProject).mock.calls.at(-1)?.[0].name).toBe("Q3");
  });

  it("renaming does not add an undo step or drop redo history", async () => {
    edit();
    vi.advanceTimersByTime(COALESCE_MS + 1);
    edit();
    undo();
    const before = useProjectStore.temporal.getState();
    const [past, future] = [before.pastStates.length, before.futureStates.length];
    await renameCurrentProject("Q3");
    const after = useProjectStore.temporal.getState();
    expect([after.pastStates.length, after.futureStates.length]).toEqual([past, future]);
    expect(after.futureStates.every((s) => s.project?.name === "Q3")).toBe(true);
  });

  it("export saves first, and with discard just downloads the server copy", async () => {
    vi.mocked(saveProject).mockRejectedValue(new Error("offline"));
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);
    edit();
    await expect(downloadProjectExport(false)).rejects.toThrow();
    expect(click).not.toHaveBeenCalled();
    await downloadProjectExport(false, { discard: true });
    expect(click).toHaveBeenCalledTimes(1);
    click.mockRestore();
  });

  it("a failed delete re-arms autosave for the unsaved edits", async () => {
    vi.mocked(deleteProject).mockRejectedValue(new Error("500"));
    edit();
    await expect(deleteCurrentProject()).rejects.toThrow();
    await vi.advanceTimersByTimeAsync(2100);
    expect(saveProject).toHaveBeenCalled();
  });
});
