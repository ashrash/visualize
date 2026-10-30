import { create } from "zustand";
import { ApiError } from "../api/client";
import { saveProject } from "../api/projects";
import { projectForSave, useProjectStore } from "./projectStore";

export type SaveStatus = "saved" | "saving" | "unsaved" | "error" | "conflict";

interface SaveState {
  status: SaveStatus;
  message: string | null;
  setStatus: (status: SaveStatus, message?: string | null) => void;
}

export const useSaveState = create<SaveState>((set) => ({
  status: "saved",
  message: null,
  setStatus: (status, message = null) => set({ status, message }),
}));

export const AUTOSAVE_DELAY_MS = 2000;

export interface AutosaverOptions {
  delayMs?: number;
  /** Persist the current project. Resolve when stored; throw ApiError(409) on a stale revision. */
  save: (autosave: boolean) => Promise<void>;
  setStatus: (status: SaveStatus, message?: string | null) => void;
  /** True when edits remain that the last save did not include. */
  isDirty: () => boolean;
}

/** Debounced saver: `schedule()` after each edit; `flush()` to save immediately. */
export function createAutosaver({
  delayMs = AUTOSAVE_DELAY_MS,
  save,
  setStatus,
  isDirty,
}: AutosaverOptions) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let inflight: Promise<void> | undefined;
  let paused = false;

  const clear = () => {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
  };

  async function run(autosave: boolean): Promise<void> {
    setStatus("saving");
    try {
      await save(autosave);
      inflight = undefined; // before rescheduling, so schedule() sees no save in flight
      if (isDirty()) {
        setStatus("unsaved");
        schedule();
      } else {
        setStatus("saved");
      }
    } catch (error) {
      inflight = undefined;
      if (error instanceof ApiError && error.status === 409) {
        paused = true;
        setStatus("conflict", error.message);
      } else {
        setStatus("error", error instanceof Error ? error.message : "Save failed");
      }
    }
  }

  function schedule(): void {
    if (paused) return;
    clear();
    if (inflight) {
      // The running save will notice the dirty store and reschedule itself.
      setStatus("saving");
      return;
    }
    setStatus("unsaved");
    timer = setTimeout(() => void flush(true), delayMs);
  }

  async function flush(autosave = false): Promise<void> {
    clear();
    if (inflight) return inflight;
    // A manual save of a clean project is allowed: it records a snapshot.
    if (autosave && !isDirty()) return;
    inflight = run(autosave);
    return inflight;
  }

  return {
    schedule,
    flush,
    /** Resume after the user reloaded the project from the server. */
    resume: () => {
      paused = false;
      clear();
      setStatus("saved");
    },
    dispose: clear,
  };
}

async function saveCurrent(autosave: boolean): Promise<void> {
  const body = projectForSave();
  const sent = useProjectStore.getState().project;
  if (!body || !sent) return;
  const saved = await saveProject(body, { autosave });
  useProjectStore.getState().markSaved(saved, sent);
}

/** The app-wide autosaver. */
export const autosaver = createAutosaver({
  save: saveCurrent,
  setStatus: (status, message) => useSaveState.getState().setStatus(status, message),
  isDirty: () => useProjectStore.getState().dirty,
});

/** Subscribe the autosaver to project edits. Returns an unsubscribe function. */
export function startAutosave(): () => void {
  const unsubscribe = useProjectStore.subscribe((state, previous) => {
    if (state.dirty && state.project !== previous.project) autosaver.schedule();
  });
  return () => {
    unsubscribe();
    autosaver.dispose();
  };
}
