import { temporal } from "zundo";
import { create } from "zustand";
import type { Project } from "../api/types";

interface ProjectState {
  project: Project | null;
  /** Revision the server last confirmed. Kept outside undo history so undo never rewinds it. */
  revision: number;
  dirty: boolean;
  /** Replace the project after loading it; clears undo history. */
  load: (project: Project) => void;
  /** Apply an edit. Each edit is one undo step. */
  update: (edit: (project: Project) => Project) => void;
  /**
   * Adopt the revision of a successful save. `sent` is the project object that was saved: if the
   * user edited meanwhile, the store holds a newer object and stays dirty.
   */
  markSaved: (saved: Project, sent?: Project) => void;
}

/** Edits closer together than this are one undo step (typing is not 100 steps). */
export const COALESCE_MS = 800;
let lastEdit = 0;

export const useProjectStore = create<ProjectState>()(
  temporal(
    (set) => ({
      project: null,
      revision: 0,
      dirty: false,
      load: (project) => {
        set({ project, revision: project.revision, dirty: false });
        useProjectStore.temporal.getState().clear();
        lastEdit = 0;
      },
      update: (edit) =>
        set((state) => (state.project ? { project: edit(state.project), dirty: true } : state)),
      markSaved: (saved, sent) =>
        set((state) =>
          // A late response for a project that is no longer open must not touch this one.
          state.project?.id !== saved.id
            ? state
            : { revision: saved.revision, dirty: sent !== undefined && state.project !== sent },
        ),
    }),
    {
      partialize: (state) => ({ project: state.project }),
      // Only a changed project is an undo step (dirty/revision updates are not).
      equality: (past, current) => past.project === current.project,
      handleSet: (handleSet) => (past, replace, current, delta) => {
        const changed = (past as Partial<ProjectState>).project !== current.project;
        if (!changed) return;
        const now = Date.now();
        const quiet = now - lastEdit >= COALESCE_MS;
        lastEdit = now; // a continuous burst stays one step until a pause
        // zundo types this callback as setState; at runtime it takes (past, replace, current, delta).
        if (quiet) (handleSet as (...args: unknown[]) => void)(past, replace, current, delta);
      },
      limit: 100,
    },
  ),
);

// Undo/redo mark the project dirty first so subscribers (autosave) see a dirty change.
export function undo(): void {
  if (useProjectStore.temporal.getState().pastStates.length === 0) return;
  useProjectStore.setState({ dirty: true });
  useProjectStore.temporal.getState().undo();
}

export function redo(): void {
  if (useProjectStore.temporal.getState().futureStates.length === 0) return;
  useProjectStore.setState({ dirty: true });
  useProjectStore.temporal.getState().redo();
}

/** The project as it should be sent to the server (with the confirmed revision). */
export function projectForSave(): Project | null {
  const { project, revision } = useProjectStore.getState();
  return project ? { ...project, revision } : null;
}
