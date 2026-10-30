import { create } from "zustand";
import { ApiError } from "../api/client";
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
const RETRY_DELETE_MS = 3000;

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
  startsByStaged.clear();
  for (const poll of polls.values()) clearInterval(poll.timer);
  polls.clear();
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
/** The same, per staged upload: closing its wizard must wait for them. */
const startsByStaged = new Map<string, Set<Promise<unknown>>>();

/**
 * Start a job from this tab. The project is captured now and the job is recorded as local the
 * moment its id is known. Settlement waits for in-flight starts, so a job that finishes before the
 * POST response is processed (or that the active-jobs poll finds first) is still known as ours.
 */
export function startLocalJob(
  start: () => Promise<Job>,
  onStarted?: (job: Job) => void,
  stagedId?: string,
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
  if (stagedId)
    startsByStaged.set(stagedId, (startsByStaged.get(stagedId) ?? new Set()).add(marker));
  void marker.then(() => {
    inFlightStarts.delete(marker);
    if (stagedId) startsByStaged.get(stagedId)?.delete(marker);
  });
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
  // An import request that is still in flight may be accepted by the server after the wizard
  // closed (the user pressed Import): wait for it, register its job, then decide.
  const starts = [...(startsByStaged.get(stagedId) ?? [])];
  if (starts.length > 0) {
    void Promise.allSettled(starts).then(() => closeStagedNow(stagedId));
    return;
  }
  closeStagedNow(stagedId);
}

function closeStagedNow(stagedId: string): void {
  const entry = stagedCleanup.get(stagedId);
  if (!entry) return void deleteStagedQuietly(stagedId); // nothing was started from it
  entry.wizardOpen = false;
  maybeDeleteStaged(stagedId, entry);
}

/** Best effort: a busy upload (409) is retried once after a few seconds, then left to the sweep. */
export function deleteStagedQuietly(stagedId: string): void {
  deleteStaged(stagedId).catch((error: unknown) => {
    if (error instanceof ApiError && error.status === 409) {
      setTimeout(() => void deleteStaged(stagedId).catch(() => undefined), RETRY_DELETE_MS);
    }
  });
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
      // Decided: no reload should reconsider it.
      localJobs().delete(job.id);
      persistLocalJobs();
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

const settle = (job: Job, onSettled?: (job: Job) => void) => {
  if (!settled.has(job.id)) {
    settled.add(job.id);
    void settleDataJob(job);
    jobSettledForCleanup(job);
  }
  onSettled?.(job);
};

interface SharedPoll {
  timer: ReturnType<typeof setInterval> | undefined;
  listeners: Set<(job: Job) => void>;
  misses: number;
}
/** One poll per job id, however many followers lost their stream. */
const polls = new Map<string, SharedPoll>();

/** The stream is gone: ask for the job until it is terminal (or cannot be found any more). */
function joinPoll(jobId: string, fallback: Job | null, listener: (job: Job) => void): () => void {
  let poll = polls.get(jobId);
  if (!poll) {
    const created: SharedPoll = { timer: undefined, listeners: new Set(), misses: 0 };
    poll = created;
    polls.set(jobId, created);
    const finish = (job: Job) => {
      clearInterval(created.timer);
      polls.delete(jobId);
      for (const l of [...created.listeners]) l(job);
    };
    created.timer = setInterval(async () => {
      const store = useJobsStore.getState();
      try {
        const job = await getJob(jobId);
        if (polls.get(jobId) !== created) return;
        created.misses = 0;
        store.merge(job);
        if (isTerminal(job.status)) finish(job);
      } catch {
        created.misses += 1;
        if (polls.get(jobId) !== created || created.misses < GIVE_UP_AFTER) return;
        const known = store.jobs[jobId] ?? fallback;
        const failed: Job = known
          ? { ...known, status: "failed", error: "Lost connection to the job", seq: known.seq + 1 }
          : failedStub(jobId, "Lost connection to the job");
        store.merge(failed);
        finish(failed);
      }
    }, POLL_MS);
  }
  poll.listeners.add(listener);
  return () => {
    const current = polls.get(jobId);
    if (!current) return;
    current.listeners.delete(listener);
    if (current.listeners.size === 0) {
      clearInterval(current.timer);
      polls.delete(jobId);
    }
  };
}

export const useJobsStore = create<JobsState>((set, get) => {
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
        stopPolling = joinPoll(jobId, fallback, (job) => settle(job, onSettled));
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
