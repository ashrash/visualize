import { create } from "zustand";
import { followJob, isTerminal } from "../api/jobs";
import type { Job } from "../api/types";

interface JobsState {
  jobs: Record<string, Job>;
  /** Start following a job. `onSettled` runs once with the terminal snapshot (or a synthetic failure). */
  track: (job: Job, onSettled?: (job: Job) => void) => () => void;
  /** Merge a snapshot from elsewhere (e.g. the polled active-jobs list). */
  merge: (job: Job) => void;
  remove: (id: string) => void;
}

const stops = new Map<string, () => void>();

export const useJobsStore = create<JobsState>((set, get) => ({
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
      onSettled?.(job);
      return () => undefined;
    }
    stops.get(job.id)?.();
    const stop = followJob(job.id, {
      onJob: (next) => {
        get().merge(next);
        if (isTerminal(next.status)) onSettled?.(next);
      },
      onError: (message) => {
        const failed: Job = { ...get().jobs[job.id], ...job, status: "failed", error: message };
        get().merge({ ...failed, seq: (get().jobs[job.id]?.seq ?? 0) + 1 });
        onSettled?.(failed);
      },
    });
    stops.set(job.id, stop);
    return stop;
  },
}));
