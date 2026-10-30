import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Project } from "../api/types";
import { COALESCE_MS, projectForSave, redo, undo, useProjectStore } from "./projectStore";

const base = (id = "p1"): Project => ({
  id,
  name: "Test",
  schemaVersion: 1,
  revision: 3,
  datasetIds: [],
  pipelines: [],
  charts: [],
});

const rename = (name: string) => useProjectStore.getState().update((p) => ({ ...p, name }));
const state = () => useProjectStore.getState();
/** Let the coalescing window pass so the next edit is its own undo step. */
const pause = () => vi.advanceTimersByTime(COALESCE_MS + 1);
const past = () => useProjectStore.temporal.getState().pastStates.length;

describe("projectStore", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    state().load(base());
  });
  afterEach(() => vi.useRealTimers());

  it("loads a project clean with its revision and empty history", () => {
    expect(state().project?.name).toBe("Test");
    expect(state().revision).toBe(3);
    expect(state().dirty).toBe(false);
    expect(past()).toBe(0);
  });

  it("marks edits dirty and undoes and redoes them", () => {
    rename("A");
    pause();
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

  it("coalesces a burst of edits (typing) into one undo step", () => {
    for (const text of ["N", "Ne", "New", "New ", "New n", "New na", "New nam", "New name"]) {
      rename(text);
      vi.advanceTimersByTime(50);
    }
    expect(past()).toBe(1);
    undo();
    expect(state().project?.name).toBe("Test");
    redo();
    expect(state().project?.name).toBe("New name");
  });

  it("starts a new undo step after a pause", () => {
    rename("A");
    pause();
    rename("B");
    pause();
    rename("C");
    expect(past()).toBe(3);
  });

  it("does not mark dirty or touch history when undo/redo have nothing to do", () => {
    undo();
    redo();
    expect(state().dirty).toBe(false);
    expect(past()).toBe(0);
  });

  it("keeps the confirmed revision across undo", () => {
    rename("A");
    state().markSaved({ ...base(), revision: 4 }, state().project ?? undefined);
    pause();
    rename("B");
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
    pause();
    rename("B");
    state().markSaved({ ...base(), revision: 4 }, sent);
    expect(state().dirty).toBe(true);
    expect(state().revision).toBe(4);
  });

  it("ignores a save result for a project that is no longer open", () => {
    rename("A");
    const oldProject = state().project ?? undefined;
    state().load(base("p2"));
    state().markSaved({ ...base("p1"), revision: 99 }, oldProject);
    expect(state().revision).toBe(3);
    expect(state().project?.id).toBe("p2");
    expect(state().dirty).toBe(false);
  });

  it("load clears undo history and the coalescing window", () => {
    rename("A");
    state().load(base());
    expect(past()).toBe(0);
    rename("B"); // immediately after load: still recorded
    expect(past()).toBe(1);
  });
});
