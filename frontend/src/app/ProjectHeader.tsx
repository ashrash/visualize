import { Check, ChevronDown, CircleAlert, Loader2, Redo2, Save, Undo2 } from "lucide-react";
import { useState } from "react";
import { useStore } from "zustand";
import { featureOn } from "../api/features";
import { exportProjectUrl } from "../api/projects";
import { Button } from "../components/ui/button";
import { Dialog, DialogContent } from "../components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../components/ui/dropdown-menu";
import { Field, Input } from "../components/ui/input";
import { toast } from "../components/ui/toast";
import { Tooltip } from "../components/ui/tooltip";
import { autosaver, type SaveStatus, useSaveState } from "../state/autosave";
import { redo, undo, useProjectStore } from "../state/projectStore";
import {
  DeleteProjectDialog,
  ImportProjectDialog,
  RenameDialog,
  VersionsDialog,
} from "./ProjectDialogs";
import { duplicateCurrentProject } from "./projectActions";
import {
  createAndOpenProject,
  openProject,
  reloadCurrentProject,
  UnsavedChangesError,
  useInvalidateProjects,
  useProjectList,
} from "./useProjectSession";

const STATUS_TEXT: Record<SaveStatus, string> = {
  saved: "Saved",
  saving: "Saving…",
  unsaved: "Unsaved changes",
  error: "Save failed",
  conflict: "Out of date",
};

export function SaveIndicator() {
  const { status, message } = useSaveState();
  const setConflictOpen = useSaveState((s) => s.setConflictOpen);
  const Icon =
    status === "saved"
      ? Check
      : status === "saving"
        ? Loader2
        : status === "unsaved"
          ? Save
          : CircleAlert;
  const tone =
    status === "error" || status === "conflict"
      ? "text-danger"
      : status === "saved"
        ? "text-success"
        : "text-muted";
  const body = (
    <>
      <Icon
        size={14}
        aria-hidden="true"
        className={status === "saving" ? "animate-spin" : undefined}
      />
      {STATUS_TEXT[status]}
    </>
  );
  const className = `flex items-center gap-1 text-sm ${tone}`;
  return (
    <>
      {status === "conflict" ? (
        <button
          type="button"
          data-status={status}
          className={`${className} underline`}
          title="Show options"
          onClick={() => setConflictOpen(true)}
        >
          {body}
        </button>
      ) : (
        <span data-status={status} title={message ?? undefined} className={className}>
          {body}
        </span>
      )}
      {/* Only failures are announced; routine saves stay silent. */}
      <span role="alert" className="sr-only">
        {status === "error" ? `Save failed. ${message ?? ""}` : null}
        {status === "conflict" ? "This project changed elsewhere and was not saved." : null}
      </span>
    </>
  );
}

export function UndoRedo() {
  const canUndo = useStore(useProjectStore.temporal, (s) => s.pastStates.length > 0);
  const canRedo = useStore(useProjectStore.temporal, (s) => s.futureStates.length > 0);
  // aria-disabled (not disabled) keeps the buttons focusable so the tooltip can say why.
  return (
    <div className="flex gap-1">
      <Tooltip content={canUndo ? "Undo (Ctrl+Z)" : "Nothing to undo"}>
        <Button
          size="icon"
          variant="ghost"
          aria-label="Undo"
          aria-disabled={!canUndo}
          onClick={canUndo ? undo : undefined}
        >
          <Undo2 size={15} aria-hidden="true" />
        </Button>
      </Tooltip>
      <Tooltip content={canRedo ? "Redo (Ctrl+Shift+Z)" : "Nothing to redo"}>
        <Button
          size="icon"
          variant="ghost"
          aria-label="Redo"
          aria-disabled={!canRedo}
          onClick={canRedo ? redo : undefined}
        >
          <Redo2 size={15} aria-hidden="true" />
        </Button>
      </Tooltip>
    </div>
  );
}

type MenuDialog = "switch" | "versions" | "rename" | "delete" | "import" | null;

export function ProjectMenu() {
  const project = useProjectStore((s) => s.project);
  const [dialog, setDialog] = useState<MenuDialog>(null);
  const platform = featureOn("platformProjects");
  const close = (which: Exclude<MenuDialog, null>) => (open: boolean) =>
    setDialog(open ? which : null);

  const run = async (action: () => Promise<void>, failure: string) => {
    try {
      await action();
    } catch (error) {
      toast(failure, {
        variant: "error",
        description: error instanceof Error ? error.message : undefined,
      });
    }
  };

  // Platform items stay visible but disabled until the endpoints exist.
  const soon = platform ? null : <span className="ml-auto text-xs text-muted">soon</span>;
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" className="max-w-64" aria-label="Project menu">
            <span className="truncate font-medium">{project?.name ?? "Loading…"}</span>
            <ChevronDown size={14} aria-hidden="true" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          <DropdownMenuItem onSelect={() => setDialog("switch")}>
            Open or create project…
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => setDialog("versions")} disabled={!project}>
            Version history…
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem disabled={!platform} onSelect={() => setDialog("rename")}>
            Rename…{soon}
          </DropdownMenuItem>
          <DropdownMenuItem
            disabled={!platform}
            onSelect={() => void run(() => duplicateCurrentProject(null), "Couldn't duplicate")}
          >
            Duplicate{soon}
          </DropdownMenuItem>
          <DropdownMenuItem disabled={!platform} onSelect={() => setDialog("delete")}>
            Delete…{soon}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem disabled={!platform || !project} asChild>
            <a href={project ? exportProjectUrl(project.id, false) : undefined} download>
              Export .vizproj{soon}
            </a>
          </DropdownMenuItem>
          <DropdownMenuItem disabled={!platform || !project} asChild>
            <a href={project ? exportProjectUrl(project.id, true) : undefined} download>
              Export with data{soon}
            </a>
          </DropdownMenuItem>
          <DropdownMenuItem disabled={!platform} onSelect={() => setDialog("import")}>
            Import .vizproj…{soon}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <ProjectPicker open={dialog === "switch"} onOpenChange={close("switch")} />
      <VersionsDialog open={dialog === "versions"} onOpenChange={close("versions")} />
      <RenameDialog open={dialog === "rename"} onOpenChange={close("rename")} />
      <DeleteProjectDialog open={dialog === "delete"} onOpenChange={close("delete")} />
      <ImportProjectDialog open={dialog === "import"} onOpenChange={close("import")} />
    </>
  );
}

function ProjectPicker({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const projects = useProjectList(open);
  const currentId = useProjectStore((s) => s.project?.id);
  const invalidate = useInvalidateProjects();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  // Set when the current project has unsaved changes that could not be saved.
  const [blocked, setBlocked] = useState<((discard: boolean) => Promise<void>) | null>(null);

  const run = async (action: (discard: boolean) => Promise<void>, discard = false) => {
    setBusy(true);
    try {
      await action(discard);
      await invalidate();
      setBlocked(null);
      onOpenChange(false);
    } catch (error) {
      if (error instanceof UnsavedChangesError) setBlocked(() => action);
      else {
        toast("Couldn't open project", {
          variant: "error",
          description: error instanceof Error ? error.message : undefined,
        });
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setBlocked(null);
        onOpenChange(next);
      }}
    >
      <DialogContent title="Projects" description="Open an existing project or start a new one.">
        {projects.isError ? (
          <p role="alert" className="text-sm text-danger">
            {projects.error.message}
          </p>
        ) : null}
        {blocked ? (
          <div
            role="alert"
            className="flex flex-col gap-2 rounded-md border border-danger p-2 text-sm"
          >
            <p>
              The current project has changes that could not be saved. Opening another project now
              will lose them.
            </p>
            <div className="flex justify-end gap-2">
              <Button size="sm" onClick={() => setBlocked(null)}>
                Stay on this project
              </Button>
              <Button size="sm" variant="danger" onClick={() => void run(blocked, true)}>
                Discard changes and continue
              </Button>
            </div>
          </div>
        ) : null}
        <ul className="flex max-h-60 flex-col gap-0.5 overflow-y-auto">
          {(projects.data ?? []).map((p) => (
            <li key={p.id}>
              <button
                type="button"
                disabled={busy}
                aria-current={p.id === currentId ? "true" : undefined}
                onClick={() => void run((discard) => openProject(p.id, { discard }))}
                className="flex w-full items-baseline justify-between gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-surface-2 aria-[current=true]:bg-surface-2 aria-[current=true]:font-medium"
              >
                <span className="truncate">{p.name}</span>
                <span className="shrink-0 text-xs text-muted">
                  {new Date(p.updatedAt).toLocaleString()}
                </span>
              </button>
            </li>
          ))}
        </ul>
        <form
          className="flex items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void run(async (discard) => {
              await createAndOpenProject(name, { discard });
              setName("");
            });
          }}
        >
          <Field label="New project name" className="flex-1">
            {(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} />}
          </Field>
          <Button type="submit" variant="primary" disabled={busy}>
            Create
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Shown when the server rejects a save because another copy of the project moved on. */
export function ConflictDialog() {
  const status = useSaveState((s) => s.status);
  const open = useSaveState((s) => s.conflictOpen);
  const setOpen = useSaveState((s) => s.setConflictOpen);
  return (
    <Dialog open={open && status === "conflict"} onOpenChange={setOpen}>
      <DialogContent
        title="This project changed elsewhere"
        description="Another tab or session saved a newer revision, so your latest changes were not saved. Autosave is paused."
        onInteractOutside={(e) => e.preventDefault()}
      >
        <p className="text-sm">
          Reload to get the latest version. Your unsaved edits in this tab will be discarded.
        </p>
        <div className="flex justify-end gap-2">
          <Button onClick={() => setOpen(false)}>Keep editing (not saved)</Button>
          <Button
            variant="primary"
            onClick={() => {
              reloadCurrentProject().then(
                () => setOpen(false),
                (error: unknown) =>
                  toast("Couldn't reload the project", {
                    variant: "error",
                    description: error instanceof Error ? error.message : undefined,
                  }),
              );
            }}
          >
            Reload latest
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function SaveButton() {
  const status = useSaveState((s) => s.status);
  return (
    <Button onClick={() => void autosaver.flush(false)} disabled={status === "saving"}>
      Save
    </Button>
  );
}
