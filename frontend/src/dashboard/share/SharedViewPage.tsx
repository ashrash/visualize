import { useQuery } from "@tanstack/react-query";
import { Printer } from "lucide-react";
import { useEffect } from "react";
import { useParams } from "react-router";
import { ApiError } from "../../api/client";
import { ChartView } from "../../charts";
import { Button } from "../../components/ui/button";
import { DashboardView } from "../DashboardView";
import "../print.css";
import { useDashboardRuntime } from "../runtimeStore";
import { getSharedView } from "./api";

/**
 * Read-only `/share/:token` and `/embed/:token`. No project bootstrap, no sign-in, no editing;
 * the page only needs the specs the token resolves to. The embed variant drops the page chrome
 * so it fits an `<iframe>`.
 *
 * TODO(ui-data): route both outside `AppShell` (it loads the user's project list):
 *   <Route path="/share/:token" element={<SharePage />} />
 *   <Route path="/embed/:token" element={<EmbedPage />} />
 */
function SharedViewPage({ via }: { via: "share" | "embed" }) {
  const { token = "" } = useParams();
  const embed = via === "embed";
  const view = useQuery({
    queryKey: ["shared-view", via, token],
    queryFn: ({ signal }) => getSharedView(token, via, signal),
    retry: false,
    staleTime: 60_000,
  });
  const reset = useDashboardRuntime((s) => s.resetAll);
  // Each visit starts from the saved filters.
  useEffect(() => reset(), [reset]);

  if (view.isPending) {
    return <p className="p-6 text-sm text-muted">Loading…</p>;
  }
  if (view.isError) {
    const gone = view.error instanceof ApiError && view.error.status === 404;
    return (
      <main className="mx-auto max-w-md p-8 text-center">
        <h1 className="text-lg font-semibold">
          {gone ? "This link isn't available" : "Couldn't open this link"}
        </h1>
        <p className="mt-2 text-sm text-muted">
          {gone
            ? "It may have expired, been revoked, or the item was deleted."
            : view.error.message}
        </p>
      </main>
    );
  }

  const { dashboard, charts, pipelines, projectName, globalFilters } = view.data;
  const single = dashboard === null ? charts[0] : undefined;
  return (
    <main className={embed ? "shared-embed h-full p-2" : "shared-page mx-auto max-w-[1400px] p-4"}>
      {!embed ? (
        <header className="shared-header mb-3 flex flex-wrap items-center gap-3">
          <div className="min-w-0">
            <h1 className="truncate text-lg font-semibold">
              {dashboard?.name ?? single?.name ?? "Shared chart"}
            </h1>
            <p className="text-xs text-muted">{projectName}</p>
          </div>
          <Button className="ml-auto print:hidden" onClick={() => window.print()}>
            <Printer size={14} aria-hidden="true" />
            Print
          </Button>
        </header>
      ) : null}
      {dashboard ? (
        <DashboardView
          dashboard={dashboard}
          charts={charts}
          pipelines={pipelines}
          globalFilters={globalFilters}
          mode={embed ? "embed" : "view"}
        />
      ) : single ? (
        <div className="h-[70vh] min-h-72">
          <ChartView spec={single} pipelines={pipelines} />
        </div>
      ) : (
        <p className="text-sm text-muted">Nothing to show.</p>
      )}
    </main>
  );
}

export function SharePage() {
  return <SharedViewPage via="share" />;
}

export function EmbedPage() {
  return <SharedViewPage via="embed" />;
}

export default SharePage;
