import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchArrow } from "../api/arrow";
import { ApiError, api, detailOf, unwrap } from "../api/client";
import type { Dataset, RowsPageMeta } from "../api/types";

export function useDatasets() {
  return useQuery({
    queryKey: ["datasets"],
    queryFn: async () => unwrap(await api.GET("/api/datasets")),
  });
}

export function useUploadDataset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (file: File): Promise<Dataset> => {
      const form = new FormData();
      form.append("file", file);
      const response = await fetch("/api/datasets/upload", { method: "POST", body: form });
      const body: unknown = await response.json().catch(() => undefined);
      if (!response.ok) {
        throw new ApiError(detailOf(body) ?? `Upload failed (${response.status})`, response.status);
      }
      return body as Dataset;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["datasets"] }),
  });
}

export function useImportFromPath() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (path: string) =>
      unwrap(await api.POST("/api/datasets/import-path", { body: { path } })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["datasets"] }),
  });
}

export function fetchRowsPage(
  datasetId: string,
  offset: number,
  limit: number,
  signal?: AbortSignal,
) {
  const query = new URLSearchParams({ offset: String(offset), limit: String(limit) });
  return fetchArrow<RowsPageMeta>(`/api/datasets/${encodeURIComponent(datasetId)}/rows?${query}`, {
    signal,
  });
}
