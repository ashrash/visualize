import {
  Copy,
  Eye,
  FileText,
  Pencil,
  Plus,
  Printer,
  Share2,
  SlidersHorizontal,
  Trash2,
} from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router";
import type { Predicate } from "../api/types";
import { Button } from "../components/ui/button";
import { Dialog, DialogClose, DialogContent } from "../components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "../components/ui/dropdown-menu";
import { Field, Input, Select } from "../components/ui/input";
import { useProjectStore } from "../state/projectStore";
import { useSelection } from "../state/selection";
import { DashboardView } from "./DashboardView";
import {
  addDashboardToProject,
  createDashboard,
  duplicateDashboard,
  newId,
  removeDashboardFromProject,
  updateDashboard,
} from "./model";
import "./print.css";
import { useDashboardRuntime } from "./runtimeStore";
import { ShareDialog } from "./share/ShareDialog";
import type { Dashboard, DashboardFilter, DrillTarget } from "./types";
import { useReport } from "./useReport";

function GlobalFilters({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const project = useProjectStore((s) => s.project);
  const update = useProjectStore((s) => s.update);
  const [label, setLabel] = useState("Date");
  const [field, setField] = useState("");
  const filters = project?.globalFilters ?? [];
  const add = () => {
    const filter: DashboardFilter = {
      id: newId(),
      label: label || field,
      field: field.trim(),
      fieldMap: {},
      predicate: null,
      control: "date_range",
    };
    update((p) => ({ ...p, globalFilters: [...(p.globalFilters ?? []), filter] }));
    setField("");
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title="Global filters"
        description="A date range applied to every dashboard. Each chart uses the field named here, or the one mapped for its dataset."
      >
        <ul className="flex flex-col gap-2">
          {filters.map((f) => (
            <li key={f.id} className="flex flex-col gap-2 rounded-md border border-line p-2">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium">{`${f.label} (${f.field})`}</span>
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label={`Remove ${f.label}`}
                  onClick={() =>
                    update((p) => ({
                      ...p,
                      globalFilters: (p.globalFilters ?? []).filter((g) => g.id !== f.id),
                    }))
                  }
                >
                  <Trash2 size={14} aria-hidden="true" />
                </Button>
              </div>
              <MapRow filter={f} />
            </li>
          ))}
        </ul>
        <form
          className="flex items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (field.trim()) add();
          }}
        >
          <Field label="Label" className="w-32">
            {(p) => <Input {...p} value={label} onChange={(e) => setLabel(e.target.value)} />}
          </Field>
          <Field label="Date field" className="flex-1">
            {(p) => <Input {...p} value={field} onChange={(e) => setField(e.target.value)} />}
          </Field>
          <Button type="submit" variant="primary" disabled={!field.trim()}>
            Add
          </Button>
        </form>
        <div className="flex justify-end">
          <DialogClose asChild>
            <Button>Done</Button>
          </DialogClose>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** Per-dataset field names for a global filter whose datasets name the same field differently. */
function MapRow({ filter }: { filter: DashboardFilter }) {
  const project = useProjectStore((s) => s.project);
  const update = useProjectStore((s) => s.update);
  const sources = [...new Set((project?.charts ?? []).map((c) => c.source.id))];
  if (sources.length < 2) return null;
  return (
    <details className="text-xs">
      <summary className="cursor-pointer text-muted">Field names per source</summary>
      <div className="mt-1 grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-1">
        {sources.map((id) => (
          <div key={id} className="contents">
            <span className="truncate self-center text-muted">{id}</span>
            <Input
              aria-label={`Field in ${id}`}
              placeholder={filter.field}
              value={filter.fieldMap?.[id] ?? ""}
              onChange={(e) =>
                update((p) => ({
                  ...p,
                  globalFilters: (p.globalFilters ?? []).map((g) =>
                    g.id === filter.id
                      ? { ...g, fieldMap: { ...g.fieldMap, [id]: e.target.value } }
                      : g,
                  ),
                }))
              }
            />
          </div>
        ))}
      </div>
    </details>
  );
}

/** `/dashboards` and `/dashboards/:id`: build, edit and present the project's dashboards. */
export default function DashboardPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const project = useProjectStore((s) => s.project);
  const update = useProjectStore((s) => s.update);
  const selectDataset = useSelection((s) => s.selectDataset);
  const openDashboard = useDashboardRuntime((s) => s.open);
  const setGridDrill = useDashboardRuntime((s) => s.setGridDrill);

  const [editing, setEditing] = useState(true);
  const [sharing, setSharing] = useState(false);
  const [globals, setGlobals] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const viewRoot = useRef<HTMLDivElement>(null);

  const dashboards = project?.dashboards ?? [];
  const dashboard = dashboards.find((d) => d.id === id) ?? dashboards[0];
  const charts = project?.charts ?? [];
  const pipelines = project?.pipelines ?? [];
  const report = useReport({
    root: viewRoot,
    dashboard,
    projectName: project?.name ?? "",
  });

  const go = useCallback(
    (dashboardId: string) => navigate(`/dashboards/${dashboardId}`),
    [navigate],
  );

  const onChange = useCallback(
    (edit: (d: Dashboard) => Dashboard) => {
      if (!dashboard) return;
      update((p) => updateDashboard(p, dashboard.id, edit));
    },
    [dashboard, update],
  );

  const onDrillThrough = useCallback(
    (target: DrillTarget, predicates: Predicate[]) => {
      if (target.kind === "dashboard") {
        openDashboard(target.dashboardId, { drill: predicates });
        go(target.dashboardId);
      } else {
        // TODO(ui-data): the grid should apply `gridDrill` when it opens.
        setGridDrill({ datasetId: target.datasetId, predicates });
        selectDataset(target.datasetId);
        navigate("/data");
      }
    },
    [openDashboard, go, setGridDrill, selectDataset, navigate],
  );

  const others = useMemo(
    () => dashboards.filter((d) => d.id !== dashboard?.id).map((d) => ({ id: d.id, name: d.name })),
    [dashboards, dashboard],
  );

  if (!project) return <p className="p-4 text-sm text-muted">Loading the project…</p>;

  const create = () => {
    const created = createDashboard(`Dashboard ${dashboards.length + 1}`);
    update((p) => addDashboardToProject(p, created));
    setEditing(true);
    go(created.id);
  };

  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="dashboard-chrome flex flex-wrap items-center gap-2">
        {dashboards.length > 0 ? (
          <Select
            aria-label="Dashboard"
            className="w-56"
            value={dashboard?.id ?? ""}
            onChange={(e) => go(e.target.value)}
            options={dashboards.map((d) => ({ value: d.id, label: d.name }))}
          />
        ) : null}
        {dashboard && editing ? (
          <Input
            aria-label="Dashboard name"
            className="w-56"
            value={dashboard.name}
            onChange={(e) => onChange((d) => ({ ...d, name: e.target.value }))}
          />
        ) : null}
        <Button onClick={create}>
          <Plus size={14} aria-hidden="true" />
          New dashboard
        </Button>
        {dashboard ? (
          <>
            <Button aria-pressed={editing} onClick={() => setEditing((v) => !v)}>
              {editing ? (
                <Eye size={14} aria-hidden="true" />
              ) : (
                <Pencil size={14} aria-hidden="true" />
              )}
              {editing ? "Present" : "Edit"}
            </Button>
            <Button
              onClick={() => {
                const copy = duplicateDashboard(dashboard);
                update((p) => addDashboardToProject(p, copy));
                go(copy.id);
              }}
            >
              <Copy size={14} aria-hidden="true" />
              Duplicate
            </Button>
            <Button onClick={() => setGlobals(true)}>
              <SlidersHorizontal size={14} aria-hidden="true" />
              Global filters
            </Button>
            <div className="ml-auto flex items-center gap-2">
              <Button onClick={() => window.print()}>
                <Printer size={14} aria-hidden="true" />
                Print
              </Button>
              <DropdownMenu>
                <DropdownMenuTrigger
                  disabled={report.busy}
                  className="inline-flex h-8 items-center gap-1.5 rounded-md border border-line px-3 text-sm font-medium hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-40"
                >
                  <FileText size={14} aria-hidden="true" />
                  {report.busy ? "Building report…" : "Report"}
                </DropdownMenuTrigger>
                <DropdownMenuContent>
                  <DropdownMenuItem onSelect={() => void report.run("html")}>
                    HTML report
                  </DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => void report.run("pdf")}>
                    PDF report
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
              <Button variant="primary" onClick={() => setSharing(true)}>
                <Share2 size={14} aria-hidden="true" />
                Share
              </Button>
              <Button
                variant="ghost"
                aria-label="Delete dashboard"
                onClick={() => setDeleting(true)}
              >
                <Trash2 size={14} aria-hidden="true" />
              </Button>
            </div>
          </>
        ) : null}
      </div>
      {report.error ? (
        <p role="alert" className="text-sm text-danger">
          {report.error}
        </p>
      ) : null}

      {dashboard ? (
        <div ref={viewRoot}>
          <DashboardView
            dashboard={dashboard}
            charts={charts}
            pipelines={pipelines}
            globalFilters={project.globalFilters ?? []}
            mode={editing ? "edit" : "view"}
            onChange={onChange}
            onDrillThrough={onDrillThrough}
            otherDashboards={others}
          />
        </div>
      ) : (
        <div className="flex flex-col items-center gap-2 p-10 text-center">
          <p className="text-sm text-muted">This project has no dashboards yet.</p>
          <Button variant="primary" onClick={create}>
            Create a dashboard
          </Button>
        </div>
      )}

      {dashboard ? (
        <>
          <ShareDialog
            open={sharing}
            onOpenChange={setSharing}
            projectId={project.id}
            dashboardId={dashboard.id}
            dashboardName={dashboard.name}
          />
          <Dialog open={deleting} onOpenChange={setDeleting}>
            <DialogContent
              title={`Delete "${dashboard.name}"?`}
              description="The charts it shows stay in the project."
            >
              <div className="flex justify-end gap-2">
                <DialogClose asChild>
                  <Button>Cancel</Button>
                </DialogClose>
                <Button
                  variant="danger"
                  onClick={() => {
                    update((p) => removeDashboardFromProject(p, dashboard.id));
                    setDeleting(false);
                    navigate("/dashboards");
                  }}
                >
                  Delete
                </Button>
              </div>
            </DialogContent>
          </Dialog>
        </>
      ) : null}
      <GlobalFilters open={globals} onOpenChange={setGlobals} />
    </div>
  );
}
