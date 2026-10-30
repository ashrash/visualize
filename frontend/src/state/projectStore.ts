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
  /** Adopt the revision of a successful save. */
  markSaved: (saved: Project) => void;
}

export const useProjectStore = create<ProjectState>()(
  temporal(
    (set) => ({
      project: null,
      revision: 0,
      dirty: false,
      load: (project) => {
        set({ project, revision: project.revision, dirty: false });
        useProjectStore.temporal.getState().clear();
      },
      update: (edit) =>
        set((state) => (state.project ? { project: edit(state.project), dirty: true } : state)),
      markSaved: (saved) => set({ revision: saved.revision, dirty: false }),
    }),
    { partialize: (state) => ({ project: state.project }), limit: 100 },
  ),
);

export function undo(): void {
  useProjectStore.temporal.getState().undo();
  useProjectStore.setState({ dirty: true });
}

export function redo(): void {
  useProjectStore.temporal.getState().redo();
  useProjectStore.setState({ dirty: true });
}

/** The project as it should be sent to the server (with the confirmed revision). */
export function projectForSave(): Project | null {
  const { project, revision } = useProjectStore.getState();
  return project ? { ...project, revision } : null;
}
