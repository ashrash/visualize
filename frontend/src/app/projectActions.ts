import { create } from "zustand";
import {
  createProject,
  deleteProject,
  duplicateProject,
  exportProjectUrl,
  listProjects,
  renameProject,
  restoreVersion,
} from "../api/projects";
import type { Project } from "../api/types";
import { toast } from "../components/ui/toast";
import { autosaver } from "../state/autosave";
import { useProjectStore } from "../state/projectStore";
import { openProject, rememberProject, UnsavedChangesError } from "./useProjectSession";

// Actions behind the project menu. Each one saves pending edits first so the server's copy is
// current. If that save fails, UnsavedChangesError tells the caller to ask before continuing
// with `discard: true` (see `runGuarded`).

type Options = { discard?: boolean };

async function requireSaved(discard = false): Promise<void> {
  const clean = await autosaver.flush(true);
  if (!clean && !discard) throw new UnsavedChangesError();
}

function current() {
  const { project, revision } = useProjectStore.getState();
  if (!project) throw new Error("No project is open");
  return { project, revision };
}

/**
 * Adopt the server's name and revision without touching local edits. The name is rewritten in
 * every undo and redo snapshot too, otherwise undoing an earlier edit would put the old name back
 * and the next autosave would quietly rename the project on the server.
 */
function adoptServerName(saved: { name: string; revision: number }): void {
  const temporal = useProjectStore.temporal;
  const rename = <T extends { project?: Project | null }>(snapshot: T): T =>
    snapshot.project
      ? { ...snapshot, project: { ...snapshot.project, name: saved.name } }
      : snapshot;
  temporal.getState().pause();
  useProjectStore.setState((state) =>
    state.project
      ? { project: { ...state.project, name: saved.name }, revision: saved.revision }
      : state,
  );
  temporal.setState((history) => ({
    pastStates: history.pastStates.map(rename),
    futureStates: history.futureStates.map(rename),
  }));
  temporal.getState().resume();
}

export async function renameCurrentProject(name: string, options: Options = {}): Promise<void> {
  await requireSaved(options.discard);
  const { project, revision } = current();
  adoptServerName(await renameProject(project.id, name.trim(), revision));
}

export async function duplicateCurrentProject(
  name: string | null,
  options: Options = {},
): Promise<void> {
  await requireSaved(options.discard);
  const copy = await duplicateProject(current().project.id, name?.trim() || null);
  autosaver.resume();
  useProjectStore.getState().load(copy);
  rememberProject(copy.id);
  toast(`Duplicated as "${copy.name}"`, { variant: "success" });
}

/** Delete the open project, then open another (or a fresh one when none is left). */
export async function deleteCurrentProject(): Promise<void> {
  const { project } = current();
  autosaver.dispose(); // a pending autosave must not resurrect the project being deleted
  try {
    await deleteProject(project.id);
  } catch (error) {
    // The project is still here: keep protecting its unsaved edits.
    if (useProjectStore.getState().dirty) autosaver.schedule();
    throw error;
  }
  const rest = (await listProjects()).filter((p) => p.id !== project.id);
  const next = rest[0];
  if (next) await openProject(next.id, { discard: true });
  else {
    const fresh = await createProject("My project");
    autosaver.resume();
    useProjectStore.getState().load(fresh);
    rememberProject(fresh.id);
  }
}

export async function restoreProjectVersion(
  versionId: number,
  options: Options = {},
): Promise<void> {
  await requireSaved(options.discard);
  const { project, revision } = current();
  const restored = await restoreVersion(project.id, versionId, revision);
  autosaver.resume();
  useProjectStore.getState().load(restored); // also resets the undo history
}

/** Download the .vizproj, after saving so it includes the last edits. */
export async function downloadProjectExport(
  includeData: boolean,
  options: Options = {},
): Promise<void> {
  await requireSaved(options.discard); // with discard, the download is the server's copy
  const { project } = current();
  const a = document.createElement("a");
  a.href = exportProjectUrl(project.id, includeData);
  a.download = "";
  document.body.append(a);
  a.click();
  a.remove();
}

// ---- the "unsaved changes" prompt shared by every action above and the project picker ----

interface GuardState {
  retry: (() => Promise<void>) | null;
  ask: (retry: () => Promise<void>) => void;
  clear: () => void;
}

export const useGuardPrompt = create<GuardState>((set) => ({
  retry: null,
  ask: (retry) => set({ retry }),
  clear: () => set({ retry: null }),
}));

/**
 * Run an action; when the current project has changes that could not be saved, show the shared
 * prompt, and run it again with `discard: true` if the user confirms. Other errors are toasted.
 */
export async function runGuarded(
  action: (discard: boolean) => Promise<void>,
  failure: string,
  /** Runs after the action succeeds, including after a confirmed discard-and-retry. */
  onDone?: () => void,
): Promise<boolean> {
  try {
    await action(false);
    onDone?.();
    return true;
  } catch (error) {
    if (error instanceof UnsavedChangesError) {
      useGuardPrompt.getState().ask(async () => {
        await action(true);
        onDone?.();
      });
    } else {
      toast(failure, {
        variant: "error",
        description: error instanceof Error ? error.message : undefined,
      });
    }
    return false;
  }
}
