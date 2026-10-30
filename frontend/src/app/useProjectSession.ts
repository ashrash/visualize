import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { createProject, getProject, listProjects } from "../api/projects";
import { autosaver, startAutosave } from "../state/autosave";
import { redo, undo, useProjectStore } from "../state/projectStore";

const LAST_PROJECT_KEY = "viz.lastProject";

function rememberProject(id: string): void {
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

/** Open a project: save pending edits to the current one first, then load and remember it. */
export async function openProject(id: string, signal?: AbortSignal): Promise<void> {
  await autosaver.flush(true);
  const project = await getProject(id, signal);
  useProjectStore.getState().load(project);
  rememberProject(project.id);
  autosaver.resume();
}

/** Discard local edits and take the server's copy (used to resolve a 409 conflict). */
export async function reloadCurrentProject(): Promise<void> {
  const id = useProjectStore.getState().project?.id;
  if (!id) return;
  const project = await getProject(id);
  useProjectStore.getState().load(project);
  autosaver.resume();
}

export async function createAndOpenProject(name: string): Promise<void> {
  await autosaver.flush(true);
  const project = await createProject(name.trim() || "Untitled project");
  useProjectStore.getState().load(project);
  rememberProject(project.id);
  autosaver.resume();
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
      if (key === "s") {
        event.preventDefault();
        void autosaver.flush(false);
      } else if (!editingText && key === "z") {
        event.preventDefault();
        if (event.shiftKey) redo();
        else undo();
      } else if (!editingText && key === "y") {
        event.preventDefault();
        redo();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);
}
