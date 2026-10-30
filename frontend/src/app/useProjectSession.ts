import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { createProject, getProject, listProjects } from "../api/projects";
import type { Project } from "../api/types";
import { autosaver, startAutosave, useSaveState } from "../state/autosave";
import { redo, undo, useProjectStore } from "../state/projectStore";

const LAST_PROJECT_KEY = "viz.lastProject";

export function rememberProject(id: string): void {
  try {
    localStorage.setItem(LAST_PROJECT_KEY, id);
  } catch {
    // Storage unavailable; we'll fall back to the most recent project.
  }
}

function lastProject(): string | null {
  try {
    return localStorage.getItem(LAST_PROJECT_KEY);
  } catch {
    return null;
  }
}

/** Thrown when the open project still has changes that could not be saved. */
export class UnsavedChangesError extends Error {
  constructor() {
    super("The current project has changes that could not be saved.");
    this.name = "UnsavedChangesError";
  }
}

/** Save pending edits; refuse (unless `discard`) when they cannot be saved. */
async function ensureSaved(discard: boolean): Promise<void> {
  const clean = await autosaver.flush(true);
  if (!clean && !discard) throw new UnsavedChangesError();
}

/** Replace the open project. Cancels any pending save timer and error state of the old one. */
function adopt(project: Project): void {
  autosaver.resume();
  useProjectStore.getState().load(project);
  rememberProject(project.id);
}

/**
 * Open a project. The current one is saved first; if that fails (or it is in conflict) this
 * throws UnsavedChangesError so the caller can ask before passing `discard: true`.
 */
export async function openProject(
  id: string,
  options: { discard?: boolean; signal?: AbortSignal } = {},
): Promise<void> {
  const discard = options.discard ?? false;
  await ensureSaved(discard);
  const project = await getProject(id, options.signal);
  // Edits made while the project was loading belong to the old project: save them too.
  await ensureSaved(discard);
  adopt(project);
}

/** Discard local edits and take the server's copy (used to resolve a 409 conflict). */
export async function reloadCurrentProject(): Promise<void> {
  const id = useProjectStore.getState().project?.id;
  if (!id) return;
  adopt(await getProject(id));
}

export async function createAndOpenProject(
  name: string,
  options: { discard?: boolean } = {},
): Promise<void> {
  await ensureSaved(options.discard ?? false);
  adopt(await createProject(name.trim() || "Untitled project"));
}

/** Load the last-opened project (or create one) once at startup. */
export function useProjectBootstrap() {
  return useQuery({
    queryKey: ["project-session"],
    staleTime: Number.POSITIVE_INFINITY,
    queryFn: async ({ signal }) => {
      const projects = await listProjects(signal);
      const target = projects.find((p) => p.id === lastProject()) ?? projects[0];
      const project = target
        ? await getProject(target.id, signal)
        : await createProject("My project", signal);
      useProjectStore.getState().load(project);
      rememberProject(project.id);
      return project.id;
    },
  });
}

export function useProjectList(enabled = true) {
  return useQuery({
    queryKey: ["projects"],
    queryFn: ({ signal }) => listProjects(signal),
    enabled,
  });
}

export function useInvalidateProjects() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: ["projects"] });
}

/** Autosave wiring plus keyboard: Ctrl/Cmd+S save, Ctrl/Cmd+Z undo, Ctrl/Cmd+Shift+Z or Ctrl+Y redo. */
export function useProjectShortcuts(): void {
  useEffect(() => startAutosave(), []);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey)) return;
      const key = event.key.toLowerCase();
      const editingText =
        event.target instanceof HTMLElement &&
        event.target.closest("input, textarea, [contenteditable=true], .cm-editor") !== null;
      // Undo/redo must not change the project behind an open dialog.
      const modalOpen = document.querySelector("[role=dialog], [role=alertdialog]") !== null;
      if (key === "s") {
        event.preventDefault();
        void autosaver.flush(false);
      } else if (!editingText && !modalOpen && key === "z") {
        event.preventDefault();
        if (event.shiftKey) redo();
        else undo();
      } else if (!editingText && !modalOpen && key === "y") {
        event.preventDefault();
        redo();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);
  // Closing the tab while edits are unsaved (or a save failed) would lose them silently.
  useEffect(() => {
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      if (useSaveState.getState().status === "saved" && !useProjectStore.getState().dirty) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, []);
}
