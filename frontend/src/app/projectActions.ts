import {
  createProject,
  deleteProject,
  duplicateProject,
  listProjects,
  renameProject,
  restoreVersion,
} from "../api/projects";
import { autosaver } from "../state/autosave";
import { useProjectStore } from "../state/projectStore";
import { openProject, rememberProject } from "./useProjectSession";

// Actions behind the project menu (flag `platformProjects`). Each one saves pending edits first
// so the server's copy is current, then replaces the open project with the server's answer.
// `load` also resets the undo history, as it must after a restore.

function current() {
  const { project, revision } = useProjectStore.getState();
  if (!project) throw new Error("No project is open");
  return { project, revision };
}

export async function renameCurrentProject(name: string): Promise<void> {
  await autosaver.flush(true);
  const { project, revision } = current();
  const saved = await renameProject(project.id, name.trim(), revision);
  useProjectStore.getState().load(saved);
}

export async function duplicateCurrentProject(name: string | null): Promise<void> {
  await autosaver.flush(true);
  const copy = await duplicateProject(current().project.id, name?.trim() || null);
  useProjectStore.getState().load(copy);
  rememberProject(copy.id);
  autosaver.resume();
}

/** Delete the open project, then open another (or a fresh one when none is left). */
export async function deleteCurrentProject(): Promise<void> {
  const { project } = current();
  await deleteProject(project.id);
  const rest = (await listProjects()).filter((p) => p.id !== project.id);
  const next = rest[0];
  if (next) await openProject(next.id);
  else {
    const fresh = await createProject("My project");
    useProjectStore.getState().load(fresh);
    rememberProject(fresh.id);
    autosaver.resume();
  }
}

export async function restoreProjectVersion(versionId: number): Promise<void> {
  await autosaver.flush(true);
  const { project, revision } = current();
  const restored = await restoreVersion(project.id, versionId, revision);
  useProjectStore.getState().load(restored);
  autosaver.resume();
}
