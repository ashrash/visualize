import { isTerminal } from "../api/jobs";
import type {
  ImportOptions,
  ImportPreviewMeta,
  ImportPreviewRequest,
  ImportRequest,
  Job,
  SourceKind,
  StagedFile,
} from "../api/types";
import { cleanOptions } from "./requests";

/**
 * Import wizard state machine: select -> (staging) -> configure (live preview) -> importing
 * (one job per sheet) -> done | failed. Pure; the component feeds it events from the network.
 */

export type Source =
  | { kind: "staged"; stagedId: string; fileName: string }
  | { kind: "path"; path: string };

export type PreviewState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; meta: ImportPreviewMeta }
  | { status: "error"; message: string };

export interface ConfigureState {
  phase: "configure";
  source: Source;
  fileKind: SourceKind | null;
  options: ImportOptions;
  name: string;
  /** Sheets offered by the file (Excel); empty for other kinds. */
  sheets: string[];
  selectedSheets: string[];
  preview: PreviewState;
}

export interface JobEntry {
  jobId: string;
  sheet: string | null;
  job: Job;
}

export type WizardState =
  | { phase: "select"; error: string | null }
  | { phase: "staging"; fileName: string }
  | ConfigureState
  | { phase: "importing"; config: ConfigureState; jobs: JobEntry[] }
  | { phase: "done"; datasetIds: string[]; warnings: string[] }
  | { phase: "failed"; message: string; config: ConfigureState; datasetIds: string[] };

export type WizardEvent =
  | { type: "fileChosen"; fileName: string }
  | { type: "staged"; file: StagedFile }
  | { type: "stagingFailed"; message: string }
  | { type: "pathChosen"; path: string }
  | { type: "optionsChanged"; options: ImportOptions }
  | { type: "nameChanged"; name: string }
  | { type: "sheetToggled"; sheet: string }
  | { type: "previewStarted" }
  | { type: "previewLoaded"; meta: ImportPreviewMeta }
  | { type: "previewFailed"; message: string }
  | { type: "importStarted"; jobs: { sheet: string | null; job: Job }[] }
  | { type: "jobUpdated"; job: Job }
  | { type: "importFailedToStart"; message: string }
  | { type: "back" }
  | { type: "reset" };

export const initialState: WizardState = { phase: "select", error: null };

const stem = (fileName: string) => fileName.replace(/^.*[\\/]/, "").replace(/\.[^.]+$/, "");

function configure(
  source: Source,
  name: string,
  fileKind: SourceKind | null,
  sheets: string[],
): ConfigureState {
  return {
    phase: "configure",
    source,
    fileKind,
    options: {},
    name,
    sheets,
    selectedSheets: sheets.slice(0, 1),
    preview: { status: "idle" },
  };
}

export function wizardReducer(state: WizardState, event: WizardEvent): WizardState {
  switch (event.type) {
    case "reset":
      return initialState;
    case "fileChosen":
      return { phase: "staging", fileName: event.fileName };
    case "stagingFailed":
      return { phase: "select", error: event.message };
    case "staged":
      return configure(
        { kind: "staged", stagedId: event.file.stagedId, fileName: event.file.fileName },
        stem(event.file.fileName),
        event.file.kind,
        event.file.sheets,
      );
    case "pathChosen":
      return configure({ kind: "path", path: event.path.trim() }, stem(event.path), null, []);
    case "back":
      if (state.phase === "failed") return state.config;
      return state;
    case "importFailedToStart":
      if (state.phase === "configure") {
        return { phase: "failed", message: event.message, config: state, datasetIds: [] };
      }
      return state;
    case "importStarted":
      if (state.phase !== "configure") return state;
      return {
        phase: "importing",
        config: state,
        jobs: event.jobs.map(({ sheet, job }) => ({ jobId: job.id, sheet, job })),
      };
    case "jobUpdated": {
      if (state.phase !== "importing") return state;
      const jobs = state.jobs.map((entry) =>
        entry.jobId === event.job.id && event.job.seq >= entry.job.seq
          ? { ...entry, job: event.job }
          : entry,
      );
      if (!jobs.every((entry) => isTerminal(entry.job.status))) return { ...state, jobs };
      const datasetIds = jobs.flatMap((e) =>
        e.job.status === "succeeded" && e.job.result?.datasetId ? [e.job.result.datasetId] : [],
      );
      const problem = jobs.find((e) => e.job.status !== "succeeded");
      if (problem) {
        const where =
          problem.sheet && problem.job.status !== "cancelled" ? `Sheet "${problem.sheet}": ` : "";
        const why =
          problem.job.status === "cancelled"
            ? `Import of ${problem.sheet ?? state.config.name} was cancelled`
            : (problem.job.error ?? "the import failed");
        return {
          phase: "failed",
          message: `${where}${why}`,
          config: state.config,
          datasetIds,
        };
      }
      return {
        phase: "done",
        datasetIds,
        warnings: jobs.flatMap((e) => e.job.result?.warnings ?? []),
      };
    }
    default:
      break;
  }
  if (state.phase !== "configure") return state;
  switch (event.type) {
    case "optionsChanged":
      return { ...state, options: event.options };
    case "nameChanged":
      return { ...state, name: event.name };
    case "sheetToggled": {
      const has = state.selectedSheets.includes(event.sheet);
      const selected = has
        ? state.selectedSheets.filter((s) => s !== event.sheet)
        : state.sheets.filter((s) => s === event.sheet || state.selectedSheets.includes(s));
      return { ...state, selectedSheets: selected };
    }
    case "previewStarted":
      return { ...state, preview: { status: "loading" } };
    case "previewFailed":
      return { ...state, preview: { status: "error", message: event.message } };
    case "previewLoaded": {
      // A path import learns its sheets (and kind) from the first preview.
      const learnSheets = state.sheets.length === 0 && event.meta.sheets.length > 0;
      return {
        ...state,
        fileKind: state.fileKind ?? event.meta.kind,
        sheets: learnSheets ? event.meta.sheets : state.sheets,
        selectedSheets: learnSheets ? event.meta.sheets.slice(0, 1) : state.selectedSheets,
        preview: { status: "ready", meta: event.meta },
      };
    }
    default:
      return state;
  }
}

function sourceFields(source: Source): { stagedId: string | null; path: string | null } {
  return source.kind === "staged"
    ? { stagedId: source.stagedId, path: null }
    : { stagedId: null, path: source.path };
}

/** The preview request for the current options; the first selected sheet is previewed. */
export function buildPreviewRequest(state: ConfigureState, limit = 1000): ImportPreviewRequest {
  const options = cleanOptions({
    ...state.options,
    sheet: state.selectedSheets[0] ?? state.options.sheet,
  });
  // `kind` is left to the server's detection; there is no way for the user to set it yet.
  return { ...sourceFields(state.source), kind: null, options, limit };
}

/** One import request per selected sheet (each sheet becomes its own dataset), else one. */
export function buildImportRequests(
  state: ConfigureState,
): { sheet: string | null; request: ImportRequest }[] {
  const name = state.name.trim() || null;
  const make = (sheet: string | null, label: string | null): ImportRequest => ({
    ...sourceFields(state.source),
    name: label,
    kind: null,
    options: cleanOptions({ ...state.options, sheet: sheet ?? state.options.sheet }),
  });
  if (state.selectedSheets.length <= 1) {
    return [
      {
        sheet: state.selectedSheets[0] ?? null,
        request: make(state.selectedSheets[0] ?? null, name),
      },
    ];
  }
  return state.selectedSheets.map((sheet) => ({
    sheet,
    request: make(sheet, name ? `${name} - ${sheet}` : sheet),
  }));
}

export function canImport(state: WizardState): boolean {
  if (state.phase !== "configure") return false;
  if (state.sheets.length > 0 && state.selectedSheets.length === 0) return false;
  return state.preview.status !== "error";
}
