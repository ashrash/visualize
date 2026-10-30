import { useState } from "react";
import type { Dataset, ImportOptions } from "../api/types";
import { Button } from "../components/ui/button";
import { Dialog, DialogContent } from "../components/ui/dialog";
import { Field, Input, Select } from "../components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { toast } from "../components/ui/toast";
import { useImportFromPath, useUploadDataset } from "./hooks";
import { describeImportError, hasOptions } from "./requests";

const DELIMITERS = [
  { value: "", label: "Auto-detect" },
  { value: ",", label: "Comma (,)" },
  { value: "\t", label: "Tab" },
  { value: ";", label: "Semicolon (;)" },
  { value: "|", label: "Pipe (|)" },
];
const ENCODINGS = [
  { value: "", label: "Auto-detect" },
  { value: "utf-8", label: "UTF-8" },
  { value: "utf-16", label: "UTF-16" },
  { value: "latin-1", label: "Latin-1" },
  { value: "cp1252", label: "Windows-1252" },
];
const HEADERS = [
  { value: "", label: "Auto-detect" },
  { value: "yes", label: "First row is a header" },
  { value: "no", label: "No header row" },
];

function OptionsForm({
  options,
  onChange,
}: {
  options: ImportOptions;
  onChange: (next: ImportOptions) => void;
}) {
  return (
    <fieldset className="grid grid-cols-1 gap-3 rounded-md border border-line p-3 sm:grid-cols-2">
      <legend className="px-1 text-xs font-medium text-muted">Options (optional)</legend>
      <Field label="Delimiter">
        {(p) => (
          <Select
            {...p}
            options={DELIMITERS}
            value={options.delimiter ?? ""}
            onChange={(e) => onChange({ ...options, delimiter: e.target.value || null })}
          />
        )}
      </Field>
      <Field label="Encoding">
        {(p) => (
          <Select
            {...p}
            options={ENCODINGS}
            value={options.encoding ?? ""}
            onChange={(e) => onChange({ ...options, encoding: e.target.value || null })}
          />
        )}
      </Field>
      <Field label="Header">
        {(p) => (
          <Select
            {...p}
            options={HEADERS}
            value={options.header === true ? "yes" : options.header === false ? "no" : ""}
            onChange={(e) =>
              onChange({
                ...options,
                header: e.target.value === "" ? null : e.target.value === "yes",
              })
            }
          />
        )}
      </Field>
      <Field label="Header row number" hint="1 = first line of the file">
        {(p) => (
          <Input
            {...p}
            type="number"
            min={1}
            value={options.headerRow ?? ""}
            onChange={(e) =>
              onChange({
                ...options,
                headerRow: e.target.value === "" ? null : Number(e.target.value),
              })
            }
          />
        )}
      </Field>
      <Field
        label="Excel sheet"
        hint="Excel files only; leave empty for the first sheet"
        className="sm:col-span-2"
      >
        {(p) => (
          <Input
            {...p}
            value={options.sheet ?? ""}
            onChange={(e) => onChange({ ...options, sheet: e.target.value || null })}
          />
        )}
      </Field>
    </fieldset>
  );
}

export function ImportDialog({
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
          <OptionsForm options={options} onChange={setOptions} />
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
