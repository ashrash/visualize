import { lazy } from "react";
import { Navigate, Route, Routes } from "react-router";
import { AppShell } from "./AppShell";

// Each screen is its own chunk, so the chart and editor code loads only when it's visited.
const DataScreen = lazy(() => import("./DataScreen"));
const PipelinesView = lazy(() => import("./PipelinesView"));
const ChartsView = lazy(() => import("./ChartsView"));
const StatsView = lazy(() => import("./StatsView"));

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/data" replace />} />
        <Route path="data" element={<DataScreen />} />
        <Route path="pipelines" element={<PipelinesView />} />
        <Route path="charts" element={<ChartsView />} />
        <Route path="stats" element={<StatsView />} />
        <Route path="*" element={<Navigate to="/data" replace />} />
      </Route>
    </Routes>
  );
}
