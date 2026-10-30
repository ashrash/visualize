import { create } from "zustand";
import { getDataset } from "../api/datasets";
import { followJob, isTerminal } from "../api/jobs";
import type { Job } from "../api/types";
import { queryClient } from "../app/queryClient";
import { toast } from "../components/ui/toast";
import { attachToProject, useProjectStore } from "./projectStore";

interface JobsState {
  jobs: Record<string, Job>;
  /**
   * Follow a job (shared SSE stream) until it settles. Safe to call twice for the same job.
   * `onSettled` runs once with the terminal snapshot, or a synthetic failed snapshot when the
   * stream is lost. Returns a function that stops this caller's interest.
   */
  track: (job: Job, onSettled?: (job: Job) => void) => () => void;
  /** Like `track`, for a job known only by id (e.g. from POST /api/pipelines/materialize). */
  trackId: (jobId: string, onSettled?: (job: Job) => void) => () => void;
  /** Merge a snapshot from elsewhere (e.g. the polled active-jobs list). */
  merge: (job: Job) => void;
  remove: (id: string) => void;
}

const settled = new Set<string>();
/** Jobs this tab started, with the project that was open at the time. */
const startedHere = new Map<string, string | undefined>();

/** Call right after this tab starts an import: only such jobs may attach to a project. */
export function markLocalJob(jobId: string): void {
  startedHere.set(jobId, useProjectStore.getState().project?.id);
}

/**
 * What happens when any import or refresh job finishes, wherever it was started from and whether
 * or not its dialog is still open: the catalog is refreshed and a new dataset joins the project.
 */
async function settleDataJob(job: Job): Promise<void> {
  if (job.status !== "succeeded" || (job.kind !== "import" && job.kind !== "refresh")) return;
  const id = job.result?.datasetId;
  if (!id) return;
  try {
    await queryClient.invalidateQueries({ queryKey: ["datasets"] });
    const dataset = await getDataset(id);
    if (job.kind === "import") {
      // Only imports this tab started, from the project still open, join that project. Imports
      // started in another tab (or before switching projects) just land in the catalog.
      const owner = startedHere.get(job.id);
      if (owner !== undefined && owner === useProjectStore.getState().project?.id) {
        attachToProject({ id });
      } else {
        toast(`Imported "${dataset.name}" into the catalog`);
      }
    }
  } catch (error) {
    toast("Couldn't load the imported dataset", {
      variant: "error",
      description: error instanceof Error ? error.message : undefined,
    });
  }
}

export const useJobsStore = create<JobsState>((set, get) => {
  const settle = (job: Job, onSettled?: (job: Job) => void) => {
    if (!settled.has(job.id)) {
      settled.add(job.id);
      void settleDataJob(job);
    }
    onSettled?.(job);
  };

  const follow = (
    jobId: string,
    fallback: Job | null,
    onSettled?: (job: Job) => void,
  ): (() => void) =>
    followJob(jobId, {
      onJob: (next) => {
        get().merge(next);
        if (isTerminal(next.status)) settle(next, onSettled);
      },
      onError: (message) => {
        const known = get().jobs[jobId] ?? fallback;
        if (!known) return onSettled?.(failedStub(jobId, message));
        const failed: Job = { ...known, status: "failed", error: message, seq: known.seq + 1 };
        get().merge(failed);
        onSettled?.(failed);
      },
    });

  return {
    jobs: {},
    merge: (job) =>
      set((state) => {
        const known = state.jobs[job.id];
        // Never let an older snapshot overwrite a newer one.
        return known && known.seq > job.seq ? state : { jobs: { ...state.jobs, [job.id]: job } };
      }),
    remove: (id) =>
      set((state) => {
        const { [id]: _gone, ...rest } = state.jobs;
        return { jobs: rest };
      }),
    track: (job, onSettled) => {
      get().merge(job);
      if (isTerminal(job.status)) {
        settle(job, onSettled);
        return () => undefined;
      }
      return follow(job.id, job, onSettled);
    },
    trackId: (jobId, onSettled) => follow(jobId, null, onSettled),
  };
});

function failedStub(id: string, message: string): Job {
  return {
    id,
    kind: "materialize",
    title: "Job",
    status: "failed",
    progress: null,
    message: null,
    seq: 0,
    createdAt: new Date().toISOString(),
    startedAt: null,
    finishedAt: null,
    error: message,
    result: null,
  };
}
