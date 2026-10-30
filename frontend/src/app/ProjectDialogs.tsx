import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { featureOn } from "../api/features";
import { getVersionSnapshot, importProjectFile, listVersions } from "../api/projects";
import type { ProjectImportResult, ProjectVersion } from "../api/types";
import { Button } from "../components/ui/button";
import { Dialog, DialogContent } from "../components/ui/dialog";
import { Field, Input } from "../components/ui/input";
import { toast } from "../components/ui/toast";
import { Tooltip } from "../components/ui/tooltip";
import { useProjectStore } from "../state/projectStore";
import {
  deleteCurrentProject,
  renameCurrentProject,
  restoreProjectVersion,
  runGuarded,
} from "./projectActions";
import { openProject, useInvalidateProjects } from "./useProjectSession";

const unavailable = "Needs project version support (not available yet)";
const message = (error: unknown) =>
  error instanceof Error ? error.message : "Something went wrong";

export function RenameDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const name = useProjectStore((s) => s.project?.name ?? "");
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const value = draft ?? name;
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          setDraft(null);
          setError(null);
        }
        onOpenChange(next);
      }}
    >
      <DialogContent title="Rename project">
        <form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (!value.trim()) return;
            // Unsaved-changes prompts and errors are handled by the shared guard.
            void runGuarded(
              (discard) => renameCurrentProject(value, { discard }),
              "Couldn't rename",
              () => onOpenChange(false),
            );
          }}
        >
          <Field label="Name">
            {(p) => <Input {...p} value={value} onChange={(e) => setDraft(e.target.value)} />}
          </Field>
          {error ? (
            <p role="alert" className="text-sm text-danger">
              {error}
            </p>
          ) : null}
          <div className="flex justify-end gap-2">
            <Button onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" variant="primary" disabled={!value.trim()}>
              Rename
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function DeleteProjectDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const name = useProjectStore((s) => s.project?.name);
  const [error, setError] = useState<string | null>(null);
  const invalidate = useInvalidateProjects();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={`Delete "${name ?? ""}"?`}
        description="The project and its saved versions are removed. Datasets stay in the catalog."
      >
        {error ? (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        ) : null}
        <div className="flex justify-end gap-2">
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            variant="danger"
            onClick={() =>
              deleteCurrentProject().then(
                () => {
                  void invalidate();
                  onOpenChange(false);
                },
                (e: unknown) => setError(message(e)),
              )
            }
          >
            Delete project
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function ImportProjectDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ProjectImportResult | null>(null);
  const invalidate = useInvalidateProjects();

  const reset = () => {
    setFile(null);
    setError(null);
    setResult(null);
  };
  const submit = async () => {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await importProjectFile(file));
      void invalidate();
    } catch (e) {
      setError(message(e)); // 422 bodies carry a message to show verbatim
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        onOpenChange(next);
      }}
    >
      <DialogContent title="Import project" description="Open a .vizproj file as a new project.">
        {result ? (
          <div className="flex flex-col gap-2 text-sm">
            <p>
              Imported <strong>{result.project.name}</strong>.
            </p>
            {result.datasetsImported.length > 0 ? (
              <p>{result.datasetsImported.length} dataset(s) imported with the project.</p>
            ) : null}
            {result.datasetsMissing.length > 0 ? (
              <div role="alert" className="rounded-md border border-warning p-2">
                <p className="font-medium text-warning">
                  Datasets that need re-importing ({result.datasetsMissing.length})
                </p>
                <ul className="list-disc pl-5">
                  {result.datasetsMissing.map((id) => (
                    <li key={id}>{id}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {result.warnings.length > 0 ? (
              <ul className="list-disc pl-5 text-muted">
                {result.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            ) : null}
            <div className="flex justify-end gap-2">
              <Button onClick={() => onOpenChange(false)}>Stay here</Button>
              <Button
                variant="primary"
                onClick={() =>
                  void runGuarded(
                    (discard) => openProject(result.project.id, { discard }),
                    "Couldn't open project",
                    () => onOpenChange(false),
                  )
                }
              >
                Open imported project
              </Button>
            </div>
          </div>
        ) : (
          <form
            className="flex flex-col gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              void submit();
            }}
          >
            <Field label="Project file (.vizproj)">
              {(p) => (
                <Input
                  {...p}
                  type="file"
                  accept=".vizproj,.zip"
                  className="h-auto py-1"
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                />
              )}
            </Field>
            {error ? (
              <p role="alert" className="text-sm text-danger">
                {error}
              </p>
            ) : null}
            <div className="flex justify-end gap-2">
              <Button onClick={() => onOpenChange(false)}>Cancel</Button>
              <Button type="submit" variant="primary" disabled={!file || busy}>
                {busy ? "Importing…" : "Import"}
              </Button>
            </div>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

function VersionPreview({ projectId, version }: { projectId: string; version: ProjectVersion }) {
  const snapshot = useQuery({
    queryKey: ["version", projectId, version.id],
    queryFn: ({ signal }) => getVersionSnapshot(projectId, version.id, signal),
  });
  if (snapshot.isPending) return <p className="text-xs text-muted">Loading preview…</p>;
  if (snapshot.isError) return <p className="text-xs text-danger">{snapshot.error.message}</p>;
  const p = snapshot.data;
  return (
    <p className="text-xs text-muted">
      {p.name}: {p.datasetIds?.length ?? 0} datasets, {p.pipelines?.length ?? 0} pipelines,{" "}
      {p.charts?.length ?? 0} charts, {p.dashboards?.length ?? 0} dashboards
    </p>
  );
}

export function VersionsDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const id = useProjectStore((s) => s.project?.id);
  const platform = featureOn("platformProjects");
  const [previewId, setPreviewId] = useState<number | null>(null);
  const [confirmId, setConfirmId] = useState<number | null>(null);
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
            <li key={v.id} className="flex flex-col gap-1 py-1.5 text-sm">
              <div className="flex items-center gap-3">
                <span className="w-16 shrink-0 font-medium">Rev {v.revision}</span>
                <span className="w-20 shrink-0 text-muted">{v.kind}</span>
                <span className="min-w-0 flex-1 truncate text-muted">
                  {new Date(v.createdAt).toLocaleString()}
                  {v.label ? ` · ${v.label}` : ""}
                </span>
                <Tooltip content={platform ? "Show what this version contains" : unavailable}>
                  <Button
                    size="sm"
                    aria-disabled={!platform}
                    aria-expanded={previewId === v.id}
                    onClick={() => platform && setPreviewId(previewId === v.id ? null : v.id)}
                  >
                    Preview
                  </Button>
                </Tooltip>
                {confirmId === v.id ? (
                  <Button
                    size="sm"
                    variant="danger"
                    onClick={() =>
                      void runGuarded(
                        (discard) => restoreProjectVersion(v.id, { discard }),
                        "Couldn't restore",
                        () => {
                          toast("Version restored", { variant: "success" });
                          onOpenChange(false);
                        },
                      )
                    }
                  >
                    Confirm restore
                  </Button>
                ) : (
                  <Tooltip content={platform ? "Restore this version" : unavailable}>
                    <Button
                      size="sm"
                      aria-disabled={!platform}
                      onClick={() => platform && setConfirmId(v.id)}
                    >
                      Restore
                    </Button>
                  </Tooltip>
                )}
              </div>
              {previewId === v.id && id ? <VersionPreview projectId={id} version={v} /> : null}
            </li>
          ))}
        </ul>
      </DialogContent>
    </Dialog>
  );
}
