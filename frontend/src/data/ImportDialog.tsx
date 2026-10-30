import { lazy, Suspense, useRef, useState } from "react";
import { featureOn } from "../api/features";
import type { Dataset, ImportOptions } from "../api/types";
import { Button } from "../components/ui/button";
import { Dialog, DialogContent } from "../components/ui/dialog";
import { Field, Input } from "../components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { toast } from "../components/ui/toast";
import { useImportFromPath, useUploadDataset } from "./hooks";
import { ImportOptionsForm } from "./ImportOptionsForm";
import { describeImportError, hasOptions } from "./requests";

// The wizard pulls in the grid and Arrow reader; load it only when it is used.
const ImportWizardDialog = lazy(() =>
  import("./ImportWizard").then((m) => ({ default: m.ImportWizardDialog })),
);

function LegacyImportDialog({
  open,
  onOpenChange,
  onImported,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onImported: (dataset: Dataset) => void;
}) {
  const upload = useUploadDataset();
  const importPath = useImportFromPath();
  const [tab, setTab] = useState("upload");
  const [file, setFile] = useState<File | null>(null);
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [options, setOptions] = useState<ImportOptions>({});
  const controller = useRef<AbortController | null>(null);
  const busy = upload.isPending || importPath.isPending;
  const error = tab === "upload" ? upload.error : importPath.error;

  const done = (dataset: Dataset) => {
    toast(`Imported ${dataset.name}`, { variant: "success" });
    setFile(null);
    setPath("");
    setName("");
    onImported(dataset);
    onOpenChange(false);
  };

  const submit = () => {
    controller.current = new AbortController();
    const signal = controller.current.signal;
    if (tab === "upload" && file)
      upload.mutate({ file, options, name, signal }, { onSuccess: done });
    else if (tab === "path" && path.trim())
      importPath.mutate({ path, options, name, signal }, { onSuccess: done });
  };

  /** Close the dialog, abandoning any import in flight and clearing the previous result. */
  const close = () => {
    controller.current?.abort();
    controller.current = null;
    upload.reset();
    importPath.reset();
    onOpenChange(false);
  };
  const ready = tab === "upload" ? file !== null : path.trim() !== "";

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? onOpenChange(true) : close())}>
      <DialogContent
        title="Import data"
        description="CSV, TSV, JSON or Excel. Settings are detected automatically; override them below if needed."
      >
        <form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList aria-label="Import source">
              <TabsTrigger value="upload">Upload a file</TabsTrigger>
              <TabsTrigger value="path">Local path</TabsTrigger>
            </TabsList>
            <TabsContent value="upload">
              <Field label="File">
                {(p) => (
                  <Input
                    {...p}
                    type="file"
                    accept=".csv,.tsv,.tab,.json,.ndjson,.jsonl,.xlsx,.xls,.xlsb,.ods"
                    className="h-auto py-1"
                    onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                  />
                )}
              </Field>
              {hasOptions(options) ? (
                <p className="text-xs text-warning">
                  Uploads don't apply these options yet; use a local path to apply them.
                </p>
              ) : null}
            </TabsContent>
            <TabsContent value="path">
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
            </TabsContent>
          </Tabs>
          <Field label="Dataset name (optional)">
            {(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} />}
          </Field>
          <ImportOptionsForm options={options} onChange={setOptions} />
          {error ? (
            <p role="alert" className="text-sm text-danger">
              {describeImportError(error)}
            </p>
          ) : null}
          <div className="flex justify-end gap-2">
            <Button onClick={close}>{busy ? "Cancel import" : "Cancel"}</Button>
            <Button type="submit" variant="primary" disabled={busy || !ready}>
              {busy ? "Importing…" : "Import"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

interface ImportDialogProps {
  open: boolean;

  onOpenChange: (open: boolean) => void;

  onImported: (dataset: Dataset) => void;
}

/** The staged wizard (preview, jobs) when data-core is merged; the one-shot form until then. */

export function ImportDialog(props: ImportDialogProps) {
  if (!featureOn("stagedImport")) return <LegacyImportDialog {...props} />;
  return (
    <Suspense fallback={null}>
      <ImportWizardDialog {...props} />
    </Suspense>
  );
}
