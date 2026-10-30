import { useQueryClient } from "@tanstack/react-query";
import { MoreHorizontal } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { cancelJob, deleteDataset, renameDataset, stageFile, startRefresh } from "../api/datasets";
import { isTerminal } from "../api/jobs";
import type { Dataset } from "../api/types";
import { Button } from "../components/ui/button";
import { Dialog, DialogContent } from "../components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "../components/ui/dropdown-menu";
import { Field, Input } from "../components/ui/input";
import { toast } from "../components/ui/toast";
import { useJobsStore } from "../state/jobs";
import { useProjectStore } from "../state/projectStore";
import { useJob } from "./useJob";

type Which = "rename" | "refresh" | "delete" | null;

const message = (e: unknown) => (e instanceof Error ? e.message : "Something went wrong");

/** Per-dataset menu: rename, refresh (re-read the source as a new version) and delete. */
export function DatasetActions({
  dataset,
  onDeleted,
}: {
  dataset: Dataset;
  onDeleted: (id: string) => void;
}) {
  const [which, setWhich] = useState<Which>(null);
  const close = (open: boolean) => !open && setWhich(null);
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button size="icon" variant="ghost" aria-label={`Actions for ${dataset.name}`}>
            <MoreHorizontal size={14} aria-hidden="true" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem onSelect={() => setWhich("rename")}>Rename…</DropdownMenuItem>
          <DropdownMenuItem onSelect={() => setWhich("refresh")}>Refresh data…</DropdownMenuItem>
          <DropdownMenuItem onSelect={() => setWhich("delete")}>Delete…</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {which === "rename" ? <RenameDialog dataset={dataset} onOpenChange={close} /> : null}
      {which === "refresh" ? <RefreshDialog dataset={dataset} onOpenChange={close} /> : null}
      {which === "delete" ? (
        <DeleteDialog dataset={dataset} onOpenChange={close} onDeleted={onDeleted} />
      ) : null}
    </>
  );
}

function RenameDialog({
  dataset,
  onOpenChange,
}: {
  dataset: Dataset;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(dataset.name);
  const [error, setError] = useState<string | null>(null);
  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent title="Rename dataset">
        <form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            renameDataset(dataset.id, name.trim()).then(
              async () => {
                await queryClient.invalidateQueries({ queryKey: ["datasets"] });
                onOpenChange(false);
              },
              (e: unknown) => setError(message(e)),
            );
          }}
        >
          <Field label="Name">
            {(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} />}
          </Field>
          {error ? (
            <p role="alert" className="text-sm text-danger">
              {error}
            </p>
          ) : null}
          <div className="flex justify-end gap-2">
            <Button onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" variant="primary" disabled={!name.trim()}>
              Rename
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DeleteDialog({
  dataset,
  onOpenChange,
  onDeleted,
}: {
  dataset: Dataset;
  onOpenChange: (open: boolean) => void;
  onDeleted: (id: string) => void;
}) {
  const queryClient = useQueryClient();
  // Set when a project uses the dataset (409); a second confirmation retries with force.
  const [inUse, setInUse] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const remove = (force: boolean) =>
    deleteDataset(dataset.id, force).then(
      async () => {
        useProjectStore.getState().update((p) => ({
          ...p,
          datasetIds: (p.datasetIds ?? []).filter((id) => id !== dataset.id),
        }));
        await queryClient.invalidateQueries({ queryKey: ["datasets"] });
        onDeleted(dataset.id);
        onOpenChange(false);
      },
      (e: unknown) => {
        if (e instanceof ApiError && e.status === 409 && !force) setInUse(e.message);
        else setError(message(e));
      },
    );

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent
        title={`Delete "${dataset.name}"?`}
        description="The imported data and all its versions are removed."
      >
        {inUse ? (
          <p role="alert" className="rounded-md border border-warning p-2 text-sm">
            {inUse} Deleting it anyway will break the charts and pipelines that use it.
          </p>
        ) : null}
        {error ? (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        ) : null}
        <div className="flex justify-end gap-2">
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="danger" onClick={() => void remove(inUse !== null)}>
            {inUse ? "Delete anyway" : "Delete dataset"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function RefreshDialog({
  dataset,
  onOpenChange,
}: {
  dataset: Dataset;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const file = useRef<File | null>(null);
  const [hasFile, setHasFile] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const { job, error: streamError } = useJob(jobId);
  const origin = dataset.source.originalPath;
  const done = job !== null && isTerminal(job.status);

  useEffect(() => {
    if (job?.status === "succeeded") void queryClient.invalidateQueries({ queryKey: ["datasets"] });
  }, [job?.status, queryClient]);

  const start = async () => {
    controller.current = new AbortController();
    const { signal } = controller.current;
    setBusy(true);
    setError(null);
    try {
      // With no staged file the server re-reads the dataset's original path.
      const staged = file.current ? await stageFile(file.current, signal) : null;
      const started = await startRefresh(
        dataset.id,
        { stagedId: staged?.stagedId ?? null, options: null },
        signal,
      );
      // Settlement (refreshing the catalog) is the jobs store's job, so it survives closing.
      useJobsStore.getState().track(started);
      setJobId(started.id);
    } catch (e) {
      if (!signal.aborted) setError(message(e));
    } finally {
      setBusy(false);
    }
  };

  const close = (open: boolean) => {
    if (!open) controller.current?.abort();
    onOpenChange(open);
  };

  return (
    <Dialog open onOpenChange={close}>
      <DialogContent
        title={`Refresh "${dataset.name}"`}
        description="Re-import the data as a new version. Charts and pipelines pick it up automatically."
      >
        {!jobId ? (
          <div className="flex flex-col gap-3">
            <p className="text-sm">
              {origin
                ? `Without a file, this re-reads ${origin}.`
                : "This dataset has no original path, so choose the updated file."}
            </p>
            <Field label="Updated file (optional)">
              {(p) => (
                <Input
                  {...p}
                  type="file"
                  className="h-auto py-1"
                  onChange={(e) => {
                    file.current = e.target.files?.[0] ?? null;
                    setHasFile(file.current !== null);
                  }}
                />
              )}
            </Field>
            {error ? (
              <p role="alert" className="text-sm text-danger">
                {error}
              </p>
            ) : null}
            <div className="flex justify-end gap-2">
              <Button onClick={() => close(false)}>Cancel</Button>
              <Button
                variant="primary"
                onClick={() => void start()}
                disabled={busy || (!origin && !hasFile)}
              >
                {busy ? "Starting…" : "Refresh"}
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-2 text-sm" aria-live="polite">
            <p>{job?.message ?? job?.status ?? "Starting…"}</p>
            {!done ? (
              <>
                <progress
                  className="h-2 w-full"
                  max={1}
                  value={job?.progress ?? undefined}
                  aria-label="Refresh progress"
                />
                <Button
                  size="sm"
                  className="self-end"
                  onClick={() => jobId && void cancelJob(jobId).catch(() => undefined)}
                >
                  Cancel refresh
                </Button>
              </>
            ) : null}
            {streamError ? (
              <p role="alert" className="text-danger">
                {streamError}
              </p>
            ) : null}
            {job?.status === "failed" ? (
              <p role="alert" className="text-danger">
                {job.error ?? "The refresh failed."}
              </p>
            ) : null}
            {job?.status === "succeeded" ? (
              <>
                <p className="text-success">Refreshed to version {job.result?.version ?? "?"}.</p>
                {(job.result?.warnings ?? []).length > 0 ? (
                  <ul className="list-disc pl-5 text-warning">
                    {job.result?.warnings.map((w) => (
                      <li key={w}>{w}</li>
                    ))}
                  </ul>
                ) : null}
              </>
            ) : null}
            {done ? (
              <Button
                variant="primary"
                className="self-end"
                onClick={() => {
                  if (job?.status === "succeeded")
                    toast(`Refreshed ${dataset.name}`, { variant: "success" });
                  close(false);
                }}
              >
                Close
              </Button>
            ) : null}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
