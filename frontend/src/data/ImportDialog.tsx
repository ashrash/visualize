import { useState } from "react";
import { featureOn } from "../api/features";
import type { Dataset, ImportOptions } from "../api/types";
import { Button } from "../components/ui/button";
import { Dialog, DialogContent } from "../components/ui/dialog";
import { Field, Input } from "../components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { toast } from "../components/ui/toast";
import { useImportFromPath, useUploadDataset } from "./hooks";
import { ImportOptionsForm } from "./ImportOptionsForm";
import { ImportWizardDialog } from "./ImportWizard";
import { describeImportError, hasOptions } from "./requests";

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
    if (tab === "upload" && file) upload.mutate({ file, options, name }, { onSuccess: done });
    else if (tab === "path" && path.trim())
      importPath.mutate({ path, options, name }, { onSuccess: done });
  };
  const ready = tab === "upload" ? file !== null : path.trim() !== "";

  return (
    <Dialog open={open} onOpenChange={(next) => (busy ? undefined : onOpenChange(next))}>
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
            <Button onClick={() => onOpenChange(false)} disabled={busy}>
              Cancel
            </Button>
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
  return featureOn("stagedImport") ? (
    <ImportWizardDialog {...props} />
  ) : (
    <LegacyImportDialog {...props} />
  );
}
