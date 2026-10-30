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
  return (
    <span
      role="status"
      aria-live="polite"
      data-status={status}
      title={message ?? undefined}
      className={`flex items-center gap-1 text-sm ${tone}`}
    >
      <Icon
        size={14}
        aria-hidden="true"
        className={status === "saving" ? "animate-spin" : undefined}
      />
      {STATUS_TEXT[status]}
    </span>
  );
}

export function UndoRedo() {
  const canUndo = useStore(useProjectStore.temporal, (s) => s.pastStates.length > 0);
  const canRedo = useStore(useProjectStore.temporal, (s) => s.futureStates.length > 0);
  return (
    <div className="flex gap-1">
      <Tooltip content="Undo (Ctrl+Z)">
        <Button size="icon" variant="ghost" aria-label="Undo" onClick={undo} disabled={!canUndo}>
          <Undo2 size={15} aria-hidden="true" />
        </Button>
      </Tooltip>
      <Tooltip content="Redo (Ctrl+Shift+Z)">
        <Button size="icon" variant="ghost" aria-label="Redo" onClick={redo} disabled={!canRedo}>
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

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    try {
      await action();
      await invalidate();
      onOpenChange(false);
    } catch (error) {
      toast("Couldn't open project", {
        variant: "error",
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Projects" description="Open an existing project or start a new one.">
        {projects.isError ? (
          <p role="alert" className="text-sm text-danger">
            {projects.error.message}
          </p>
        ) : null}
        <ul className="flex max-h-60 flex-col gap-0.5 overflow-y-auto">
          {(projects.data ?? []).map((p) => (
            <li key={p.id}>
              <button
                type="button"
                disabled={busy}
                aria-current={p.id === currentId ? "true" : undefined}
                onClick={() => void run(() => openProject(p.id))}
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
            void run(async () => {
              await createAndOpenProject(name);
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
  const [dismissed, setDismissed] = useState(false);
  const open = status === "conflict" && !dismissed;
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setDismissed(true);
      }}
    >
      <DialogContent
        title="This project changed elsewhere"
        description="Another tab or session saved a newer revision, so your latest changes were not saved. Autosave is paused."
        onInteractOutside={(e) => e.preventDefault()}
      >
        <p className="text-sm">
          Reload to get the latest version. Your unsaved edits in this tab will be discarded.
        </p>
        <div className="flex justify-end gap-2">
          <Button onClick={() => setDismissed(true)}>Keep editing (not saved)</Button>
          <Button
            variant="primary"
            onClick={() => {
              void reloadCurrentProject().then(() => setDismissed(false));
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
