import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { importFromPath, listDatasets, uploadDataset } from "../api/datasets";
import type { Dataset, ImportOptions, SourceKind } from "../api/types";
import { useProjectStore } from "../state/projectStore";
import { buildImportPathBody, buildUploadForm } from "./requests";

export function useDatasets() {
  return useQuery({
    queryKey: ["datasets"],
    queryFn: ({ signal }) => listDatasets(signal),
  });
}

/** Record a freshly imported dataset in the open project (this is one undoable edit). */
function attachToProject(dataset: Dataset): void {
  useProjectStore.getState().update((p) => ({
    ...p,
    datasetIds: [...new Set([...(p.datasetIds ?? []), dataset.id])],
  }));
}

export function useUploadDataset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ file, options, name }: { file: File; options: ImportOptions; name?: string }) =>
      uploadDataset(buildUploadForm(file, options, name)),
    onSuccess: (dataset) => {
      attachToProject(dataset);
      return queryClient.invalidateQueries({ queryKey: ["datasets"] });
    },
  });
}

export function useImportFromPath() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      path: string;
      options: ImportOptions;
      name?: string;
      kind?: SourceKind;
    }) => importFromPath(buildImportPathBody(input.path, input.options, input)),
    onSuccess: (dataset) => {
      attachToProject(dataset);
      return queryClient.invalidateQueries({ queryKey: ["datasets"] });
    },
  });
}
