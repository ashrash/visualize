import { useState } from "react";
import { startExport } from "../api/datasets";
import type { ExportFormat, ExportRequest, Job } from "../api/types";
import { toast } from "../components/ui/toast";
import { useJobsStore } from "../state/jobs";

function download(url: string, fileName: string): void {
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName;
  document.body.append(a);
  a.click();
  a.remove();
}

/** Called when an export job reaches a terminal status: download on success, toast otherwise. */
export function handleExportSettled(job: Job): void {
  const file = job.result?.export;
  if (job.status === "succeeded" && file) {
    download(file.downloadUrl, file.fileName);
    const warnings = job.result?.warnings ?? [];
    toast(`Exported ${file.fileName}`, {
      variant: "success",
      description:
        warnings.length > 0 ? warnings.join(" ") : `${file.rowCount.toLocaleString()} rows`,
    });
  } else if (job.status === "cancelled") {
    toast("Export cancelled");
  } else {
    toast("Export failed", { variant: "error", description: job.error ?? undefined });
  }
}

/** Start an export job for the grid's current view and follow it to completion. */
export function useExport() {
  const [busy, setBusy] = useState(false);
  const run = async (request: ExportRequest, format: ExportFormat) => {
    setBusy(true);
    try {
      const job = await startExport({ ...request, format });
      useJobsStore.getState().track(job, (done) => {
        setBusy(false);
        handleExportSettled(done);
      });
    } catch (error) {
      setBusy(false);
      toast("Couldn't start export", {
        variant: "error",
        description: error instanceof Error ? error.message : undefined,
      });
    }
  };
  return { run, busy };
}
