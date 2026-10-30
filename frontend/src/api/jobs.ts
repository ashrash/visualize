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
 * Browsers allow about 6 HTTP/1.1 connections per origin and every SSE stream holds one for its
 * whole life, so only a few jobs are streamed at once; the rest wait their turn (a stream sends
 * the current snapshot on connect, so a job that finished while waiting is still reported).
 */
export const MAX_STREAMS = 3;

interface Entry {
  jobId: string;
  ctor: EventSourceCtor;
  listeners: Set<FollowHandlers>;
  source: EventSource | null;
  last: Job | null;
}

const entries = new Map<string, Entry>();
let waiting: Entry[] = [];
let open = 0;

/** Test hook: forget every stream and listener. */
export function resetJobStreams(): void {
  for (const entry of entries.values()) entry.source?.close();
  entries.clear();
  waiting = [];
  open = 0;
}

function release(entry: Entry): void {
  if (entry.source) {
    entry.source.close();
    entry.source = null;
    open -= 1;
  }
  waiting = waiting.filter((e) => e !== entry);
  entries.delete(entry.jobId);
  pump();
}

function pump(): void {
  while (open < MAX_STREAMS && waiting.length > 0) {
    const next = waiting.shift();
    if (next) connect(next);
  }
}

const emit = (entry: Entry, job: Job) => {
  entry.last = job;
  for (const l of [...entry.listeners]) l.onJob(job);
};

/** The stream is gone for good: look the job up once, since it may have finished and been evicted. */
async function lostConnection(entry: Entry): Promise<void> {
  const listeners = [...entry.listeners];
  release(entry);
  try {
    const response = await fetch(`/api/jobs/${enc(entry.jobId)}`);
    if (response.ok) {
      const job = (await response.json()) as Job;
      for (const l of listeners) l.onJob(job);
      if (isTerminal(job.status)) return;
      // Still running: the stream just dropped. Reconnect rather than calling the job lost.
      if (listeners.length > 0 && !entries.has(entry.jobId)) {
        const again: Entry = { ...entry, listeners: new Set(listeners), source: null, last: job };
        entries.set(entry.jobId, again);
        if (open < MAX_STREAMS) connect(again);
        else waiting.push(again);
        return;
      }
    }
  } catch {
    // fall through to the error below
  }
  for (const l of listeners) l.onError?.("Lost connection to the job");
}

function connect(entry: Entry): void {
  const source = new entry.ctor(`/api/jobs/${enc(entry.jobId)}/events`);
  entry.source = source;
  open += 1;
  source.addEventListener("job", (event) => {
    if (entries.get(entry.jobId) !== entry) return;
    let job: Job;
    try {
      job = JSON.parse((event as MessageEvent<string>).data) as Job;
    } catch {
      const listeners = [...entry.listeners];
      release(entry);
      for (const l of listeners) l.onError?.("Received an unreadable job update");
      return;
    }
    emit(entry, job);
    if (isTerminal(job.status)) release(entry);
  });
  source.addEventListener("error", () => {
    // EventSource retries by itself; only a CLOSED source is a real failure.
    if (entries.get(entry.jobId) !== entry || source.readyState !== 2) return;
    void lostConnection(entry);
  });
}

/**
 * Follow `GET /api/jobs/{id}/events` (SSE; `event: job`, each data a full Job snapshot). All
 * followers of one job share a single EventSource, and the number of open streams is capped.
 * The stream closes at a terminal snapshot or when the last follower stops. Returns a function
 * that stops this follower. The EventSource constructor is injectable for tests.
 */
export function followJob(
  jobId: string,
  handlers: FollowHandlers,
  EventSourceImpl: EventSourceCtor = EventSource,
): () => void {
  let entry = entries.get(jobId);
  if (!entry) {
    entry = { jobId, ctor: EventSourceImpl, listeners: new Set(), source: null, last: null };
    entries.set(jobId, entry);
    if (open < MAX_STREAMS) connect(entry);
    else waiting.push(entry);
  } else if (entry.last) {
    handlers.onJob(entry.last); // late joiner: replay the latest snapshot
  }
  entry.listeners.add(handlers);
  const joined = entry;
  return () => {
    joined.listeners.delete(handlers);
    if (joined.listeners.size === 0 && entries.get(jobId) === joined) release(joined);
  };
}
