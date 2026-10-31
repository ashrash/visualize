// Public surface of the dashboard module. Routes (TODO(ui-data) wires them in App.tsx):
//   /dashboards, /dashboards/:id  -> DashboardPage     (inside AppShell)
//   /share/:token                 -> SharePage          (outside AppShell)
//   /embed/:token                 -> EmbedPage          (outside AppShell)
export { filtersForChart, groupOf } from "./bus";
export { default as DashboardPage } from "./DashboardPage";
export { DashboardView, type DashboardViewProps } from "./DashboardView";
export { createDashboard, duplicateDashboard } from "./model";
export { useDashboardRuntime } from "./runtimeStore";
export { EmbedPage, SharePage } from "./share/SharedViewPage";
