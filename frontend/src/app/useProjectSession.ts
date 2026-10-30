import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { api, unwrap } from "../api/client";
import { projectForSave, redo, undo, useProjectStore } from "../state/projectStore";

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

/** Load the last-opened project (or create one), and expose save. */
export function useProjectSession() {
  const load = useProjectStore((state) => state.load);
  const markSaved = useProjectStore((state) => state.markSaved);

  const session = useQuery({
    queryKey: ["project-session"],
    staleTime: Number.POSITIVE_INFINITY,
    queryFn: async () => {
      const projects = unwrap(await api.GET("/api/projects"));
      const target = projects.find((p) => p.id === lastProject()) ?? projects[0];
      const project = target
        ? unwrap(
            await api.GET("/api/projects/{project_id}", {
              params: { path: { project_id: target.id } },
            }),
          )
        : unwrap(await api.POST("/api/projects", { body: { name: "My project" } }));
      load(project);
      rememberProject(project.id);
      return project.id;
    },
  });

  const save = useMutation({
    mutationFn: async () => {
      const project = projectForSave();
      if (!project) throw new Error("No project loaded");
      return unwrap(
        await api.PUT("/api/projects/{project_id}", {
          params: { path: { project_id: project.id } },
          body: project,
        }),
      );
    },
    onSuccess: markSaved,
  });

  // Keyboard: Ctrl/Cmd+S save, Ctrl/Cmd+Z undo, Ctrl/Cmd+Shift+Z or Ctrl+Y redo.
  const { mutate } = save;
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey)) return;
      const key = event.key.toLowerCase();
      const editingText =
        event.target instanceof HTMLElement && event.target.closest("input, textarea") !== null;
      if (key === "s") {
        event.preventDefault();
        mutate();
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
  }, [mutate]);

  return { session, save };
}
