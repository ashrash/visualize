import type { components } from "../api/schema";

type Schemas = components["schemas"];

export type Dashboard = Schemas["Dashboard"];
export type DashboardTab = Schemas["DashboardTab"];
export type DashboardFilter = Schemas["DashboardFilter"];
export type LayoutItem = Schemas["LayoutItem"];
export type LinkGroup = Schemas["LinkGroup"];
export type Widget = LayoutItem["widget"];
export type WidgetKind = Widget["kind"];
export type FilterControl = NonNullable<DashboardFilter["control"]>;

/** Where a drill-through goes: another dashboard, or the data grid of a dataset. */
export type DrillTarget =
  | { kind: "dashboard"; dashboardId: string }
  | { kind: "grid"; datasetId: string };
