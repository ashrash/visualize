import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useEffect } from "react";
import { cancelJob, listActiveJobs } from "../api/datasets";
import { isTerminal } from "../api/jobs";
import { Button } from "../components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "../components/ui/popover";
import { useJobsStore } from "../state/jobs";

/** Header indicator for running jobs (imports, refreshes, exports, materializations). */
export function JobsIndicator() {
  const jobs = useJobsStore((s) => s.jobs);
  const merge = useJobsStore((s) => s.merge);
  const track = useJobsStore((s) => s.track);
  const active = useQuery({
    queryKey: ["jobs", "active"],
    queryFn: ({ signal }) => listActiveJobs(signal),
    refetchInterval: 3000,
  });

  // Jobs started elsewhere (another tab, a refresh) join the store and get followed.
  useEffect(() => {
    for (const job of active.data ?? []) {
      if (!useJobsStore.getState().jobs[job.id]) track(job);
      else merge(job);
    }
  }, [active.data, merge, track]);

  const running = Object.values(jobs).filter((j) => !isTerminal(j.status));
  if (running.length === 0) return null;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button size="sm" aria-label={`${running.length} running jobs`}>
          <Loader2 size={13} className="animate-spin" aria-hidden="true" />
          {running.length} running
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80" align="end">
        <ul className="flex flex-col gap-3" aria-label="Running jobs">
          {running.map((job) => (
            <li key={job.id} className="flex flex-col gap-1 text-sm">
              <div className="flex justify-between gap-2">
                <span className="truncate font-medium">{job.title}</span>
                <span className="shrink-0 text-xs text-muted">{job.status}</span>
              </div>
              <progress
                className="h-1.5 w-full"
                max={1}
                value={job.progress ?? undefined}
                aria-label={`Progress of ${job.title}`}
              />
              {job.message ? <span className="text-xs text-muted">{job.message}</span> : null}
              <Button
                size="sm"
                className="self-end"
                onClick={() => void cancelJob(job.id).catch(() => undefined)}
              >
                Cancel
              </Button>
            </li>
          ))}
        </ul>
      </PopoverContent>
    </Popover>
  );
}
