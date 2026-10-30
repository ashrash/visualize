import { createContext, type ReactNode, useContext } from "react";
import { browserSink, type ExportSink } from "./saveChart";

const ExportSinkContext = createContext<ExportSink>(browserSink);

/**
 * Where exports go for everything below it. A desktop shell wraps the app once with its own
 * sink (native save dialog, native clipboard); every chart then exports through it.
 */
export function ExportSinkProvider({ sink, children }: { sink: ExportSink; children: ReactNode }) {
  return <ExportSinkContext.Provider value={sink}>{children}</ExportSinkContext.Provider>;
}

export function useExportSink(): ExportSink {
  return useContext(ExportSinkContext);
}
