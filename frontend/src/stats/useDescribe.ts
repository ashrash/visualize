import { useQuery } from "@tanstack/react-query";
import { describeFields } from "../api/stats";
import type { DescribeRequest } from "../api/types";

/** Exact descriptive statistics for the current field/group selection (MVP §5). */
export function useDescribe(request: DescribeRequest | undefined) {
  return useQuery({
    queryKey: ["stats-describe", request],
    queryFn: ({ signal }) => describeFields(request as DescribeRequest, signal),
    enabled: request !== undefined,
  });
}
