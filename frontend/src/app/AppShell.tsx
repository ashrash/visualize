import { Upload } from "lucide-react";
import { Suspense, useState } from "react";
import { NavLink, Outlet } from "react-router";
import { Button } from "../components/ui/button";
import { Toaster } from "../components/ui/toast";
import { TooltipProvider } from "../components/ui/tooltip";
import { DatasetList } from "../data/DatasetList";
import { ImportDialog } from "../data/ImportDialog";
import { useActiveDataset } from "../data/useActiveDataset";
import { useSelection } from "../state/selection";
import { ConflictDialog, ProjectMenu, SaveButton, SaveIndicator, UndoRedo } from "./ProjectHeader";
import { ThemeSwitch } from "./ThemeSwitch";
import { useProjectBootstrap, useProjectShortcuts } from "./useProjectSession";

const NAV = [
  { to: "/data", label: "Data" },
  { to: "/pipelines", label: "Pipelines" },
  { to: "/charts", label: "Charts" },
];

export function AppShell() {
  const session = useProjectBootstrap();
  useProjectShortcuts();
  const { dataset, datasets } = useActiveDataset();
  const selectDataset = useSelection((s) => s.selectDataset);
  const [importOpen, setImportOpen] = useState(false);

  return (
    <TooltipProvider>
      <div className="flex h-full flex-col">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:bg-bg focus:p-2"
        >
          Skip to content
        </a>
        <header className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-4 py-2">
          <h1 className="text-base font-semibold">Visualize</h1>
          <ProjectMenu />
          <nav aria-label="Primary" className="flex gap-1">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  `rounded-md px-2.5 py-1 text-sm ${isActive ? "bg-surface-2 font-medium" : "text-muted hover:bg-surface-2 hover:text-fg"}`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <SaveIndicator />
            <UndoRedo />
            <SaveButton />
            <ThemeSwitch />
          </div>
        </header>
        {session.isError ? (
          <p role="alert" className="border-b border-line px-4 py-1 text-sm text-danger">
            Couldn't load the project: {session.error.message}
          </p>
        ) : null}

        <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[260px_minmax(0,1fr)]">
          <aside
            aria-label="Datasets"
            className="flex flex-col gap-4 overflow-y-auto border-b border-line bg-surface p-4 md:border-r md:border-b-0"
          >
            <Button variant="primary" onClick={() => setImportOpen(true)}>
              <Upload size={14} aria-hidden="true" />
              Import data
            </Button>
            <DatasetList
              datasets={datasets}
              selectedId={dataset?.id ?? null}
              onSelect={selectDataset}
              onDeleted={(id) => {
                if (dataset?.id === id) selectDataset(null);
              }}
            />
          </aside>
          <main id="main" className="flex min-h-0 min-w-0 flex-col gap-3 p-4">
            <Suspense fallback={<p className="text-sm text-muted">Loading…</p>}>
              <Outlet />
            </Suspense>
          </main>
        </div>
      </div>
      <ImportDialog
        open={importOpen}
        onOpenChange={setImportOpen}
        onImported={(d) => selectDataset(d.id)}
      />
      <ConflictDialog />
      <Toaster />
    </TooltipProvider>
  );
}
