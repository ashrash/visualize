import { beforeEach, describe, expect, it } from "vitest";
import type { Project } from "../api/types";
import { projectForSave, redo, undo, useProjectStore } from "./projectStore";

const base = (): Project => ({
  id: "p1",
  name: "Test",
  schemaVersion: 1,
  revision: 3,
  datasetIds: [],
  pipelines: [],
  charts: [],
});

const rename = (name: string) => useProjectStore.getState().update((p) => ({ ...p, name }));
const state = () => useProjectStore.getState();

describe("projectStore", () => {
  beforeEach(() => state().load(base()));

  it("loads a project clean with its revision and empty history", () => {
    expect(state().project?.name).toBe("Test");
    expect(state().revision).toBe(3);
    expect(state().dirty).toBe(false);
    expect(useProjectStore.temporal.getState().pastStates).toHaveLength(0);
  });

  it("marks edits dirty and undoes and redoes them", () => {
    rename("A");
    rename("B");
    expect(state().dirty).toBe(true);
    undo();
    expect(state().project?.name).toBe("A");
    undo();
    expect(state().project?.name).toBe("Test");
    redo();
    expect(state().project?.name).toBe("A");
    expect(state().dirty).toBe(true);
  });

  it("keeps the confirmed revision across undo", () => {
    rename("A");
    state().markSaved({ ...base(), revision: 4 }, state().project ?? undefined);
    undo();
    expect(state().revision).toBe(4);
    expect(projectForSave()?.revision).toBe(4);
  });

  it("clears dirty when the saved object is still the current one", () => {
    rename("A");
    state().markSaved({ ...base(), revision: 4 }, state().project ?? undefined);
    expect(state().dirty).toBe(false);
  });

  it("stays dirty when edited while the save was in flight", () => {
    rename("A");
    const sent = state().project ?? undefined;
    rename("B");
    state().markSaved({ ...base(), revision: 4 }, sent);
    expect(state().dirty).toBe(true);
    expect(state().revision).toBe(4);
  });

  it("load clears undo history", () => {
    rename("A");
    state().load(base());
    expect(useProjectStore.temporal.getState().pastStates).toHaveLength(0);
  });
});
