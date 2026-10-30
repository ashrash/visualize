import { useQuery } from "@tanstack/react-query";
import { listDatasets } from "../api/datasets";

export { attachToProject } from "../state/projectStore";

export function useDatasets() {
  return useQuery({
    queryKey: ["datasets"],
    queryFn: ({ signal }) => listDatasets(signal),
  });
}
