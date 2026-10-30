import { useEffect, useState } from "react";
import { followJob } from "../api/jobs";
import type { Job } from "../api/types";

/** Follow one job over SSE. `job` is the latest full snapshot; `error` means the stream failed. */
export function useJob(jobId: string | null): { job: Job | null; error: string | null } {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setJob(null);
    setError(null);
    if (!jobId) return;
    return followJob(jobId, { onJob: setJob, onError: setError });
  }, [jobId]);
  return { job, error };
}
