import { enc } from "./http";
import type { Job, JobStatus } from "./types";

export const TERMINAL: readonly JobStatus[] = ["succeeded", "failed", "cancelled"];

export function isTerminal(status: JobStatus): boolean {
  return TERMINAL.includes(status);
}

export interface FollowHandlers {
  /** Called with every full Job snapshot (`event: job`). */
  onJob: (job: Job) => void;
  /** The connection failed before the job reached a terminal status. */
  onError?: (message: string) => void;
}

type EventSourceCtor = new (url: string) => EventSource;

/**
 * Follow `GET /api/jobs/{id}/events` (SSE; `event: job`, each data a full Job snapshot). The
 * stream is closed once a terminal snapshot arrives. Returns a function that stops following.
 * The EventSource constructor is injectable for tests.
 */
export function followJob(
  jobId: string,
  handlers: FollowHandlers,
  EventSourceImpl: EventSourceCtor = EventSource,
): () => void {
  const source = new EventSourceImpl(`/api/jobs/${enc(jobId)}/events`);
  let finished = false;
  const stop = () => {
    finished = true;
    source.close();
  };
  source.addEventListener("job", (event) => {
    if (finished) return;
    let job: Job;
    try {
      job = JSON.parse((event as MessageEvent<string>).data) as Job;
    } catch {
      handlers.onError?.("Received an unreadable job update");
      return stop();
    }
    handlers.onJob(job);
    if (isTerminal(job.status)) stop();
  });
  source.addEventListener("error", () => {
    // EventSource retries by itself; only a CLOSED source is a real failure.
    if (finished || source.readyState !== 2) return;
    finished = true;
    handlers.onError?.("Lost connection to the job");
  });
  return stop;
}
