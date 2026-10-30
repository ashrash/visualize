import { useEffect, useReducer, useRef, useState } from "react";
import type { ColumnarResult } from "../api/arrow";
import { previewImport } from "../api/datasetRows";
import { cancelJob, getDataset, stageFile, startImport } from "../api/datasets";
import { isTerminal } from "../api/jobs";
import type { Dataset, ImportPreviewMeta } from "../api/types";
import { Button } from "../components/ui/button";
import { Dialog, DialogContent } from "../components/ui/dialog";
import { Field, Input } from "../components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { toast } from "../components/ui/toast";
import {
  addStagedJob,
  beginStagedCleanup,
  closeStagedWizard,
  startLocalJob,
  useJobsStore,
} from "../state/jobs";
import { GridView } from "./GridView";
import { ImportOptionsForm } from "./ImportOptionsForm";
import {
  buildImportRequests,
  buildPreviewRequest,
  type ConfigureState,
  canImport,
  initialState,
  type WizardEvent,
  wizardReducer,
} from "./importMachine";
import { describeImportError } from "./requests";

const NO_FILTERS = {};
const NO_SORT: never[] = [];

type Dispatch = React.Dispatch<WizardEvent>;

function SelectStep({
  error,
  dispatch,
  onFile,
}: {
  error: string | null;
  dispatch: Dispatch;
  onFile: (file: File) => void;
}) {
  const [path, setPath] = useState("");
  const [tab, setTab] = useState("upload");
  return (
    <div className="flex flex-col gap-3">
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList aria-label="Import source">
          <TabsTrigger value="upload">Upload a file</TabsTrigger>
          <TabsTrigger value="path">Local path</TabsTrigger>
        </TabsList>
        <TabsContent value="upload">
          <Field
            label="File"
            hint="CSV, TSV, JSON or Excel. You'll see a preview before importing."
          >
            {(p) => (
              <Input
                {...p}
                type="file"
                accept=".csv,.tsv,.tab,.json,.ndjson,.jsonl,.xlsx,.xls,.xlsb,.ods"
                className="h-auto py-1"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) onFile(file);
                }}
              />
            )}
          </Field>
        </TabsContent>
        <TabsContent value="path">
          <form
            className="flex flex-col gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              if (path.trim()) dispatch({ type: "pathChosen", path });
            }}
          >
            <Field
              label="File path"
              hint="Read in place; best for very large files. The path is on the machine running the server."
            >
              {(p) => (
                <Input
                  {...p}
                  value={path}
                  onChange={(e) => setPath(e.target.value)}
                  placeholder="C:\data\sales.csv"
                  spellCheck={false}
                />
              )}
            </Field>
            <Button type="submit" variant="primary" className="self-end" disabled={!path.trim()}>
              Continue
            </Button>
          </form>
        </TabsContent>
      </Tabs>
      {error ? (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/** Live preview: re-runs (debounced) whenever the source, sheet or options change. */
function usePreview(state: ConfigureState, dispatch: Dispatch) {
  // The last good preview stays on screen while the next one loads.
  const [data, setData] = useState<ColumnarResult<ImportPreviewMeta> | null>(null);
  const request = JSON.stringify(buildPreviewRequest(state));
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      dispatch({ type: "previewStarted" });
      previewImport(JSON.parse(request), controller.signal).then(
        (result) => {
          if (controller.signal.aborted) return;
          setData(result);
          dispatch({ type: "previewLoaded", meta: result.meta });
        },
        (error: unknown) => {
          if (controller.signal.aborted) return;
          dispatch({ type: "previewFailed", message: describeImportError(error) });
        },
      );
    }, 300);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [request, dispatch]);
  return data;
}

function ConfigureStep({
  state,
  dispatch,
  onStart,
}: {
  state: ConfigureState;
  dispatch: Dispatch;
  onStart: () => void;
}) {
  const data = usePreview(state, dispatch);
  const meta = state.preview.status === "ready" ? state.preview.meta : null;
  const shownMeta = data?.meta ?? null;
  const source = state.source.kind === "staged" ? state.source.fileName : state.source.path;
  return (
    <div className="flex flex-col gap-3">
      <p className="truncate text-sm text-muted" title={source}>
        {source}
      </p>
      <Field label="Dataset name">
        {(p) => (
          <Input
            {...p}
            value={state.name}
            onChange={(e) => dispatch({ type: "nameChanged", name: e.target.value })}
          />
        )}
      </Field>
      {state.sheets.length > 0 ? (
        <fieldset className="flex flex-col gap-1">
          <legend className="mb-1 text-xs font-medium text-muted">
            Sheets (each selected sheet becomes its own dataset)
          </legend>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {state.sheets.map((sheet) => (
              <label key={sheet} className="flex items-center gap-1.5 text-sm">
                <input
                  type="checkbox"
                  className="accent-accent"
                  checked={state.selectedSheets.includes(sheet)}
                  onChange={() => dispatch({ type: "sheetToggled", sheet })}
                />
                {sheet}
              </label>
            ))}
          </div>
        </fieldset>
      ) : null}
      <ImportOptionsForm
        options={state.options}
        detected={meta?.detected}
        showSheet={false}
        onChange={(options) => dispatch({ type: "optionsChanged", options })}
      />
      <section aria-label="Preview" aria-busy={state.preview.status === "loading"}>
        <div className="mb-1 flex items-baseline justify-between text-xs text-muted">
          <span>
            {state.preview.status === "loading" ? "Loading preview…" : "Preview"}
            {meta
              ? ` · first ${meta.rowsShown.toLocaleString()}${
                  meta.estimatedRows ? ` of about ${meta.estimatedRows.toLocaleString()}` : ""
                } rows`
              : ""}
          </span>
        </div>
        {state.preview.status === "error" ? (
          <p role="alert" className="text-sm text-danger">
            {state.preview.message}
          </p>
        ) : null}
        {meta?.warnings.map((w) => (
          <p key={w} className="text-xs text-warning">
            {w}
          </p>
        ))}
        {data && shownMeta ? (
          <div className="h-56">
            <GridView
              gridKey={`preview:${JSON.stringify(buildPreviewRequest(state))}`}
              label="Import preview"
              columns={shownMeta.columns}
              totalRows={data.rowCount}
              fetchPage={async ({ offset, limit }) => ({
                ...data,
                // The whole preview is already in memory; serve the requested window.
                columns: Object.fromEntries(
                  Object.entries(data.columns).map(([k, v]) => [
                    k,
                    v.slice(offset, offset + limit),
                  ]),
                ),
              })}
              sort={NO_SORT}
              filters={NO_FILTERS}
              controlsEnabled={false}
              interactive={false}
            />
          </div>
        ) : null}
      </section>
      <div className="flex justify-end">
        <Button variant="primary" onClick={onStart} disabled={!canImport(state)}>
          {state.selectedSheets.length > 1
            ? `Import ${state.selectedSheets.length} sheets`
            : "Import"}
        </Button>
      </div>
    </div>
  );
}

export function ImportWizardDialog({
  open,
  onOpenChange,
  onImported,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onImported: (dataset: Dataset) => void;
}) {
  const [state, dispatch] = useReducer(wizardReducer, initialState);
  const storeJobs = useJobsStore((s) => s.jobs);
  const stagedRef = useRef<string | null>(null);
  const finished = useRef(false);
  const closedRef = useRef(false);
  // However the wizard goes away (close button, Escape, parent unmount), release its upload.
  useEffect(
    () => () => {
      if (stagedRef.current) closeStagedWizard(stagedRef.current);
    },
    [],
  );

  // Upload to staging as soon as a file is chosen. The File itself is not serialisable state,
  // so it is parked in a ref and the machine only learns its name.
  const fileRef = useRef<File | null>(null);
  useEffect(() => {
    if (state.phase !== "staging") return;
    const controller = new AbortController();
    const file = fileRef.current;
    if (!file) return;
    stageFile(file, controller.signal).then(
      (staged) => {
        stagedRef.current = staged.stagedId;
        beginStagedCleanup(staged.stagedId);
        dispatch({ type: "staged", file: staged });
      },
      (error: unknown) => {
        if (!controller.signal.aborted) {
          dispatch({ type: "stagingFailed", message: describeImportError(error) });
        }
      },
    );
    return () => controller.abort();
  }, [state.phase]);

  // Mirror job snapshots from the jobs store into the machine.
  useEffect(() => {
    if (state.phase !== "importing") return;
    for (const entry of state.jobs) {
      const latest = storeJobs[entry.jobId];
      if (latest && latest.seq !== entry.job.seq) dispatch({ type: "jobUpdated", job: latest });
    }
  }, [storeJobs, state]);

  // On success, show the (last) new dataset. Attaching every dataset to the project and
  // refreshing the catalog happens in the jobs store, so it also happens if this dialog closes.
  useEffect(() => {
    if (state.phase !== "done" || finished.current) return;
    finished.current = true;
    const last = state.datasetIds.at(-1);
    if (!last) return;
    getDataset(last).then(
      (dataset) => {
        toast(`Imported ${dataset.name}`, { variant: "success" });
        onImported(dataset);
        if (state.warnings.length === 0) {
          // Auto-close goes through the same release as the close button.
          if (stagedRef.current) closeStagedWizard(stagedRef.current);
          stagedRef.current = null;
          onOpenChange(false);
        }
      },
      (error: unknown) =>
        toast("Imported, but couldn't load the dataset", {
          variant: "error",
          description: error instanceof Error ? error.message : undefined,
        }),
    );
  }, [state, onImported, onOpenChange]);

  const close = (next: boolean) => {
    if (!next) {
      // Import requests already sent are NOT aborted: the server may accept them, and the user
      // did press Import. They finish in the background (see `start`).
      closedRef.current = true;
      // The jobs store deletes the staged upload once nothing is reading it any more: after
      // the last import settles, or right now if none was started.
      if (stagedRef.current) closeStagedWizard(stagedRef.current);
      stagedRef.current = null;
      finished.current = false;
      dispatch({ type: "reset" });
    }
    onOpenChange(next);
  };

  const starting = useRef(false);
  const start = async () => {
    if (state.phase !== "configure" || starting.current) return; // ignore double clicks
    starting.current = true;
    // Captured now: the wizard (and its refs) may be gone before the responses arrive.
    const stagedId = stagedRef.current;
    const importName = state.name || "the file";
    try {
      // One staged upload serves every sheet: the imports run in parallel from the same id.
      const results = await Promise.allSettled(
        buildImportRequests(state).map(async ({ sheet, request }) => {
          // Recorded as ours, and registered with the staged upload, the moment the id is known.
          const job = await startLocalJob(
            () => startImport(request),
            (started) => {
              if (stagedId) addStagedJob(stagedId, started);
            },
            stagedId ?? undefined,
          );
          return { sheet, job };
        }),
      );
      const started = results.flatMap((r) => (r.status === "fulfilled" ? [r.value] : []));
      const failures = results.flatMap((r) => (r.status === "rejected" ? [r.reason] : []));
      // Follow every job that did start, even if a sibling request failed.
      for (const { job } of started) useJobsStore.getState().track(job);
      if (closedRef.current) {
        // The wizard was closed while the request was in flight. The import is running anyway,
        // so say so instead of letting a dataset appear out of nowhere.
        if (started.length > 0) {
          toast(`Import of ${importName} continues in the background`, {
            description: "Progress is shown in the header.",
          });
        }
        return;
      }
      if (started.length === 0) {
        dispatch({ type: "importFailedToStart", message: describeImportError(failures[0]) });
        return;
      }
      dispatch({ type: "importStarted", jobs: started });
      if (failures.length > 0) {
        toast("Some sheets could not be started", {
          variant: "error",
          description: describeImportError(failures[0]),
        });
      }
    } finally {
      starting.current = false;
    }
  };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent
        title="Import data"
        className="w-[min(94vw,52rem)]"
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          document
            .querySelector<HTMLElement>(
              "[role=dialog] [role=tab], [role=dialog] input, [role=dialog] select",
            )
            ?.focus();
        }}
        description="Pick a file, check the preview, then import."
      >
        {state.phase === "select" ? (
          <SelectStep
            error={state.error}
            dispatch={dispatch}
            onFile={(file) => {
              fileRef.current = file;
              dispatch({ type: "fileChosen", fileName: file.name });
            }}
          />
        ) : null}
        {state.phase === "staging" ? (
          <p role="status" className="text-sm text-muted">
            Uploading {state.fileName}…
          </p>
        ) : null}
        {state.phase === "configure" ? (
          <ConfigureStep state={state} dispatch={dispatch} onStart={() => void start()} />
        ) : null}
        {state.phase === "importing" ? (
          <ul className="flex flex-col gap-2" aria-label="Import progress">
            {state.jobs.map((entry) => (
              <li key={entry.jobId} className="flex flex-col gap-1 text-sm">
                <div className="flex justify-between">
                  <span>{entry.sheet ?? entry.job.title}</span>
                  <span className="text-muted">{entry.job.message ?? entry.job.status}</span>
                </div>
                <progress
                  className="h-2 w-full"
                  max={1}
                  value={entry.job.progress ?? undefined}
                  aria-label={`Progress of ${entry.sheet ?? entry.job.title}`}
                />
                {!isTerminal(entry.job.status) ? (
                  <Button
                    size="sm"
                    className="self-end"
                    onClick={() => void cancelJob(entry.jobId).catch(() => undefined)}
                  >
                    Cancel
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
        {state.phase === "failed" ? (
          <div className="flex flex-col gap-3">
            {state.cancelled ? (
              <p role="status" className="text-sm text-muted">
                {state.message}
              </p>
            ) : (
              <p role="alert" className="text-sm text-danger">
                {state.message}
              </p>
            )}
            <div className="flex justify-end gap-2">
              <Button onClick={() => close(false)}>Close</Button>
              <Button variant="primary" onClick={() => dispatch({ type: "back" })}>
                Back to options
              </Button>
            </div>
          </div>
        ) : null}
        {state.phase === "done" ? (
          <div className="flex flex-col gap-3 text-sm">
            <p>Imported {state.datasetIds.length} dataset(s).</p>
            {state.warnings.length > 0 ? (
              <ul className="list-disc pl-5 text-warning">
                {state.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            ) : null}
            <Button variant="primary" className="self-end" onClick={() => close(false)}>
              Done
            </Button>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
