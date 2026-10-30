import { useQuery } from "@tanstack/react-query";
import { Check, ChevronDown, CircleAlert, Loader2, Redo2, Save, Undo2 } from "lucide-react";
import { useState } from "react";
import { useStore } from "zustand";
import { listVersions, PLATFORM_PROJECT_ENDPOINTS } from "../api/projects";
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

function PlatformItem({ label, onSelect }: { label: string; onSelect?: () => void }) {
  // TODO(platform): enabled once rename/duplicate/delete/restore endpoints are merged.
  return (
    <DropdownMenuItem disabled={!PLATFORM_PROJECT_ENDPOINTS} onSelect={onSelect}>
      {label}
      {PLATFORM_PROJECT_ENDPOINTS ? null : <span className="ml-auto text-xs text-muted">soon</span>}
    </DropdownMenuItem>
  );
}

export function ProjectMenu() {
  const project = useProjectStore((s) => s.project);
  const [dialog, setDialog] = useState<"switch" | "versions" | null>(null);
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
          <PlatformItem label="Rename…" />
          <PlatformItem label="Duplicate" />
          <PlatformItem label="Delete…" />
        </DropdownMenuContent>
      </DropdownMenu>
      <ProjectPicker
        open={dialog === "switch"}
        onOpenChange={(o) => setDialog(o ? "switch" : null)}
      />
      <VersionsDialog
        open={dialog === "versions"}
        onOpenChange={(o) => setDialog(o ? "versions" : null)}
      />
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

function VersionsDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const id = useProjectStore((s) => s.project?.id);
  const versions = useQuery({
    queryKey: ["versions", id],
    queryFn: ({ signal }) => listVersions(id as string, signal),
    enabled: open && id !== undefined,
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Version history" description="Snapshots stored on save and autosave.">
        {versions.isPending ? <p className="text-sm text-muted">Loading…</p> : null}
        {versions.isError ? (
          <p role="alert" className="text-sm text-danger">
            {versions.error.message}
          </p>
        ) : null}
        {versions.data?.length === 0 ? (
          <p className="text-sm text-muted">No versions yet.</p>
        ) : null}
        <ul className="flex max-h-72 flex-col divide-y divide-line overflow-y-auto">
          {[...(versions.data ?? [])].reverse().map((v) => (
            <li key={v.id} className="flex items-center gap-3 py-1.5 text-sm">
              <span className="w-16 shrink-0 font-medium">Rev {v.revision}</span>
              <span className="w-20 shrink-0 text-muted">{v.kind}</span>
              <span className="min-w-0 flex-1 truncate text-muted">
                {new Date(v.createdAt).toLocaleString()}
                {v.label ? ` · ${v.label}` : ""}
              </span>
              {/* TODO(platform): POST /api/projects/{id}/versions/{versionId}/restore {revision} */}
              <Button size="sm" disabled={!PLATFORM_PROJECT_ENDPOINTS}>
                Restore
              </Button>
            </li>
          ))}
        </ul>
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
