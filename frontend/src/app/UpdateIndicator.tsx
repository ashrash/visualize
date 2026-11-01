import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CircleAlert, Download, Loader2 } from "lucide-react";
import { applyUpdate, getUpdateStatus } from "../api/update";
import { Button } from "../components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "../components/ui/popover";

// While applying, poll often enough to show progress without the icon feeling stuck. Otherwise
// defer to the backend's own cache (checked once per load, and again on this interval): there is
// nothing to gain by polling faster than a GitHub check could ever actually change.
const APPLYING_POLL_MS = 1000;
const IDLE_POLL_MS = 6 * 60 * 60 * 1000;

const QUERY_KEY = ["update", "status"] as const;

/** Header indicator for the self-update check (local mode only; see api/update.py). Hidden
 * entirely when the app is already up to date or self-update is disabled on the server. */
export function UpdateIndicator() {
  const queryClient = useQueryClient();
  const status = useQuery({
    queryKey: QUERY_KEY,
    queryFn: ({ signal }) => getUpdateStatus(signal),
    refetchInterval: (query) =>
      query.state.data?.status === "applying" ? APPLYING_POLL_MS : IDLE_POLL_MS,
  });

  const apply = useMutation({
    mutationFn: () => applyUpdate(),
    onSuccess: (info) => queryClient.setQueryData(QUERY_KEY, info),
  });

  const info = status.data;
  if (!info || info.status === "idle") return null;

  const applying = info.status === "applying";
  const done = info.status === "done";
  const failed = info.status === "failed";

  const label = applying
    ? "Updating…"
    : done
      ? "Update installed"
      : failed
        ? "Update failed"
        : `Update to ${info.latestVersion}`;

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button size="sm" aria-label={label}>
          {applying ? (
            <Loader2 size={13} className="animate-spin" aria-hidden="true" />
          ) : done ? (
            <Check size={13} aria-hidden="true" />
          ) : failed ? (
            <CircleAlert size={13} aria-hidden="true" />
          ) : (
            <Download size={13} aria-hidden="true" />
          )}
          {label}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-72" align="end">
        <div className="flex flex-col gap-2 text-sm">
          <span className="font-medium">
            {done
              ? "Update installed"
              : failed
                ? "Update failed"
                : `Version ${info.latestVersion} is available`}
          </span>
          {applying ? (
            <progress
              className="h-1.5 w-full"
              max={1}
              value={info.progress ?? undefined}
              aria-label="Update progress"
            />
          ) : null}
          {info.message ? <p className="text-xs text-muted">{info.message}</p> : null}
          {failed && info.error ? <p className="text-xs text-danger">{info.error}</p> : null}
          {info.status === "available" && info.canApply ? (
            <Button
              size="sm"
              variant="primary"
              className="self-end"
              onClick={() => apply.mutate()}
              disabled={apply.isPending}
            >
              Update now
            </Button>
          ) : null}
          {info.status === "available" && !info.canApply ? (
            <p className="text-xs text-muted">
              This install can't update itself (it's a dev checkout) — use <code>git pull</code>
              {info.releaseUrl ? (
                <>
                  {" "}
                  or see the{" "}
                  <a
                    className="text-accent underline underline-offset-2"
                    href={info.releaseUrl}
                    target="_blank"
                    rel="noreferrer"
                  >
                    release notes
                  </a>
                </>
              ) : null}
              .
            </p>
          ) : null}
          {done ? <p className="text-xs text-muted">Restart the app to finish.</p> : null}
        </div>
      </PopoverContent>
    </Popover>
  );
}
