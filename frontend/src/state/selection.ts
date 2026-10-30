import { create } from "zustand";

/** UI selection shared by the data, pipeline and chart views. Not part of the saved project. */
interface SelectionState {
  datasetId: string | null;
  pipelineId: string | null;
  chartId: string | null;
  selectDataset: (id: string | null) => void;
  selectPipeline: (id: string | null) => void;
  selectChart: (id: string | null) => void;
}

export const useSelection = create<SelectionState>((set) => ({
  datasetId: null,
  pipelineId: null,
  chartId: null,
  selectDataset: (datasetId) => set({ datasetId }),
  selectPipeline: (pipelineId) => set({ pipelineId }),
  selectChart: (chartId) => set({ chartId }),
}));
