import { lazy, Suspense } from "react";
import type { Dataset } from "../api/types";

// The wizard pulls in the grid and the Arrow reader; load it only when the dialog is used.
const ImportWizardDialog = lazy(() =>
  import("./ImportWizard").then((m) => ({ default: m.ImportWizardDialog })),
);

interface ImportDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onImported: (dataset: Dataset) => void;
}

export function ImportDialog(props: ImportDialogProps) {
  // Mounted only while open, so each opening starts from a clean wizard.
  if (!props.open) return null;
  return (
    <Suspense fallback={null}>
      <ImportWizardDialog {...props} />
    </Suspense>
  );
}
