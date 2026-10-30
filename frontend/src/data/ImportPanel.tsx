import { useId, useState } from "react";
import type { Dataset } from "../api/types";
import { useImportFromPath, useUploadDataset } from "./hooks";

export function ImportPanel({ onImported }: { onImported: (dataset: Dataset) => void }) {
  const upload = useUploadDataset();
  const importPath = useImportFromPath();
  const [path, setPath] = useState("");
  const fileId = useId();
  const pathId = useId();
  const busy = upload.isPending || importPath.isPending;
  const error = upload.error ?? importPath.error;

  return (
    <section className="flex flex-col gap-2" aria-labelledby={`${fileId}-heading`}>
      <h2
        id={`${fileId}-heading`}
        className="text-xs font-semibold uppercase tracking-wide text-muted"
      >
        Import data
      </h2>
      <label
        htmlFor={fileId}
        className="cursor-pointer rounded-md bg-accent px-3 py-1.5 text-center text-sm font-medium text-accent-fg has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-accent"
      >
        Upload CSV, TSV or JSON
        <input
          id={fileId}
          type="file"
          accept=".csv,.tsv,.tab,.json,.ndjson,.jsonl"
          className="sr-only"
          disabled={busy}
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) upload.mutate(file, { onSuccess: onImported });
            event.target.value = "";
          }}
        />
      </label>
      <form
        className="flex flex-col gap-1"
        onSubmit={(event) => {
          event.preventDefault();
          const trimmed = path.trim();
          if (!trimmed) return;
          importPath.mutate(trimmed, {
            onSuccess: (dataset) => {
              setPath("");
              onImported(dataset);
            },
          });
        }}
      >
        <label htmlFor={pathId} className="text-xs text-muted">
          Or import a large file in place, by path
        </label>
        <div className="flex gap-1">
          <input
            id={pathId}
            value={path}
            onChange={(event) => setPath(event.target.value)}
            placeholder="C:\data\sales.csv"
            className="min-w-0 flex-1 rounded-md border border-line bg-bg px-2 py-1 text-sm"
          />
          <button
            type="submit"
            disabled={busy || !path.trim()}
            className="rounded-md border border-line px-2 py-1 text-sm disabled:opacity-50"
          >
            Import
          </button>
        </div>
      </form>
      {busy ? <p className="text-xs text-muted">Importing…</p> : null}
      {error ? (
        <p role="alert" className="text-xs text-danger">
          {error.message}
        </p>
      ) : null}
    </section>
  );
}
