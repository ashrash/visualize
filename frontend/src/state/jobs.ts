import { create } from "zustand";
import { deleteStaged, getDataset, getJob } from "../api/datasets";
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
   * job can no longer be reached. Returns a function that stops this caller's interest.
   */
  track: (job: Job, onSettled?: (job: Job) => void) => () => void;
  /** Like `track`, for a job known only by id (e.g. from POST /api/pipelines/materialize). */
  trackId: (jobId: string, onSettled?: (job: Job) => void) => () => void;
  /** Merge a snapshot from elsewhere (e.g. the polled active-jobs list). */
  merge: (job: Job) => void;
  remove: (id: string) => void;
}

const settled = new Set<string>();

// ---- which jobs this tab started -------------------------------------------------------------

const STORAGE_KEY = "viz.localJobs";
let startedHere: Map<string, string> | null = null;

/** Job id -> project id for imports this tab started. Survives a reload (sessionStorage). */
function localJobs(): Map<string, string> {
  if (startedHere) return startedHere;
  startedHere = new Map();
  try {
    const raw: unknown = JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? "{}");
    if (raw && typeof raw === "object") {
      for (const [id, project] of Object.entries(raw)) {
        if (typeof project === "string") startedHere.set(id, project);
      }
    }
  } catch {
    // No sessionStorage or unreadable value: just start empty.
  }
  return startedHere;
}

function persistLocalJobs(): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(Object.fromEntries(localJobs())));
  } catch {
    // Private mode or quota: the attach then only works until the next reload.
  }
}

/** Test hook: forget what was started here (and re-read sessionStorage on next use). */
export function resetLocalJobs(): void {
  startedHere = null;
  inFlightStarts.clear();
  stagedCleanup.clear();
  settled.clear();
}

/** Record that this tab started `jobId` from the project open right now. */
export function markLocalJob(jobId: string, projectId?: string): void {
  const project = projectId ?? useProjectStore.getState().project?.id;
  if (project === undefined) return;
  localJobs().set(jobId, project);
  persistLocalJobs();
}

/** Starts that have been sent but whose job id is not known yet. */
const inFlightStarts = new Set<Promise<unknown>>();

/**
 * Start a job from this tab. The project is captured now and the job is recorded as local the
 * moment its id is known. Settlement waits for in-flight starts, so a job that finishes before the
 * POST response is processed (or that the active-jobs poll finds first) is still known as ours.
 */
export function startLocalJob(
  start: () => Promise<Job>,
  onStarted?: (job: Job) => void,
): Promise<Job> {
  const project = useProjectStore.getState().project?.id;
  const run = start().then((job) => {
    markLocalJob(job.id, project);
    onStarted?.(job);
    return job;
  });
  const marker = run.then(
    () => undefined,
    () => undefined,
  );
  inFlightStarts.add(marker);
  void marker.then(() => inFlightStarts.delete(marker));
  return run;
}

// ---- staged upload cleanup -------------------------------------------------------------------

interface StagedEntry {
  /** Jobs started from this upload that have not settled yet. */
  pending: Set<string>;
  /** Jobs that settled before they were registered or while registered. */
  finished: Map<string, boolean>;
  /** The import wizard that owns the upload is still open (and may go "Back to options"). */
  wizardOpen: boolean;
  allSucceeded: boolean;
}

const stagedCleanup = new Map<string, StagedEntry>();

/**
 * Deleting the staged upload while an import still reads it can fail with 409, so deletion waits
 * for every import started from it. While the wizard is open, only a fully successful run deletes
 * it: after a failure or cancel the user can go back and retry with the same upload. Once the
 * wizard is closed, any terminal status deletes it. A missing (404) or busy (409) upload is not
 * an error: the server expires staged files anyway.
 */
export function beginStagedCleanup(stagedId: string): void {
  if (!stagedCleanup.has(stagedId)) {
    stagedCleanup.set(stagedId, {
      pending: new Set(),
      finished: new Map(),
      wizardOpen: true,
      allSucceeded: true,
    });
  }
}

/** Register one job started from the staged upload (call as soon as its id is known). */
export function addStagedJob(stagedId: string, job: Job): void {
  beginStagedCleanup(stagedId);
  const entry = stagedCleanup.get(stagedId);
  if (!entry) return;
  if (isTerminal(job.status) || settled.has(job.id)) {
    recordStagedOutcome(stagedId, entry, job.id, job.status === "succeeded");
  } else {
    entry.pending.add(job.id);
  }
}

/** The wizard closed: from now on any terminal status may delete the upload. */
export function closeStagedWizard(stagedId: string): void {
  const entry = stagedCleanup.get(stagedId);
  if (!entry) return void deleteStagedQuietly(stagedId); // nothing was started from it
  entry.wizardOpen = false;
  maybeDeleteStaged(stagedId, entry);
}

export function deleteStagedQuietly(stagedId: string): void {
  void deleteStaged(stagedId).catch(() => undefined);
}

function recordStagedOutcome(
  stagedId: string,
  entry: StagedEntry,
  jobId: string,
  succeeded: boolean,
): void {
  entry.pending.delete(jobId);
  entry.finished.set(jobId, succeeded);
  if (!succeeded) entry.allSucceeded = false;
  maybeDeleteStaged(stagedId, entry);
}

function maybeDeleteStaged(stagedId: string, entry: StagedEntry): void {
  if (entry.pending.size > 0) return;
  if (entry.wizardOpen && !entry.allSucceeded) return; // keep it for "Back to options"
  if (entry.wizardOpen && entry.finished.size === 0) return; // nothing has run yet
  stagedCleanup.delete(stagedId);
  deleteStagedQuietly(stagedId);
}

function jobSettledForCleanup(job: Job): void {
  for (const [stagedId, entry] of stagedCleanup) {
    if (entry.pending.has(job.id)) {
      recordStagedOutcome(stagedId, entry, job.id, job.status === "succeeded");
    }
  }
}

// ---- settlement ------------------------------------------------------------------------------

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
      // A start that is still waiting for its response may be this very job.
      if (!localJobs().has(job.id)) await Promise.all([...inFlightStarts]);
      // Only imports this tab started, from the project still open, join that project. Imports
      // started in another tab (or before switching projects) just land in the catalog.
      const owner = localJobs().get(job.id);
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

const POLL_MS = 2000;
const GIVE_UP_AFTER = 3;

export const useJobsStore = create<JobsState>((set, get) => {
  const settle = (job: Job, onSettled?: (job: Job) => void) => {
    if (!settled.has(job.id)) {
      settled.add(job.id);
      void settleDataJob(job);
      jobSettledForCleanup(job);
    }
    onSettled?.(job);
  };

  /** The stream is gone: ask for the job until it is terminal (or cannot be found any more). */
  const poll = (jobId: string, fallback: Job | null, onSettled?: (job: Job) => void) => {
    let misses = 0;
    let stopped = false;
    const timer = setInterval(async () => {
      try {
        const job = await getJob(jobId);
        if (stopped) return;
        misses = 0;
        get().merge(job);
        if (isTerminal(job.status)) {
          clearInterval(timer);
          settle(job, onSettled);
        }
      } catch {
        misses += 1;
        if (stopped || misses < GIVE_UP_AFTER) return;
        clearInterval(timer);
        const known = get().jobs[jobId] ?? fallback;
        const failed: Job = known
          ? { ...known, status: "failed", error: "Lost connection to the job", seq: known.seq + 1 }
          : failedStub(jobId, "Lost connection to the job");
        get().merge(failed);
        settle(failed, onSettled);
      }
    }, POLL_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  };

  const follow = (
    jobId: string,
    fallback: Job | null,
    onSettled?: (job: Job) => void,
  ): (() => void) => {
    let stopPolling: (() => void) | null = null;
    const stopStream = followJob(jobId, {
      onJob: (next) => {
        get().merge(next);
        if (isTerminal(next.status)) settle(next, onSettled);
      },
      onError: () => {
        stopPolling = poll(jobId, fallback, onSettled);
      },
    });
    return () => {
      stopStream();
      stopPolling?.();
    };
  };

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
