import { describe, expect, it } from "vitest";
import type { ImportPreviewMeta, Job, StagedFile } from "../api/types";
import {
  buildImportRequests,
  buildPreviewRequest,
  type ConfigureState,
  canImport,
  initialState,
  type WizardEvent,
  type WizardState,
  wizardReducer,
} from "./importMachine";

const run = (events: WizardEvent[], from: WizardState = initialState) =>
  events.reduce(wizardReducer, from);

const staged = (over: Partial<StagedFile> = {}): StagedFile => ({
  stagedId: "s1",
  fileName: "Quarterly Report.xlsx",
  kind: "excel",
  sizeBytes: 100,
  sheets: ["Q1", "Q2", "Q3"],
  ...over,
});
const meta = (over: Partial<ImportPreviewMeta> = {}): ImportPreviewMeta => ({
  kind: "csv",
  detected: { delimiter: ",", encoding: "utf-8", header: true },
  sheets: [],
  columns: [],
  rowsShown: 10,
  estimatedRows: 10,
  warnings: [],
  ...over,
});
const job = (id: string, status: Job["status"], seq: number, extra: Partial<Job> = {}): Job => ({
  id,
  kind: "import",
  title: id,
  status,
  progress: null,
  message: null,
  seq,
  createdAt: "2026-01-01T00:00:00Z",
  startedAt: null,
  finishedAt: null,
  error: null,
  result: null,
  ...extra,
});
const result = (datasetId: string, warnings: string[] = []) => ({
  datasetId,
  version: 1,
  pipelineId: null,
  export: null,
  warnings,
});
const configure = (state: WizardState) => {
  if (state.phase !== "configure") throw new Error(`expected configure, got ${state.phase}`);
  return state;
};

describe("import wizard state machine", () => {
  it("select -> staging -> configure with the dataset name taken from the file", () => {
    let s = run([{ type: "fileChosen", fileName: "Quarterly Report.xlsx" }]);
    expect(s).toEqual({ phase: "staging", fileName: "Quarterly Report.xlsx" });
    s = run([{ type: "staged", file: staged() }], s);
    const c = configure(s);
    expect(c.name).toBe("Quarterly Report");
    expect(c.sheets).toEqual(["Q1", "Q2", "Q3"]);
    expect(c.selectedSheets).toEqual(["Q1"]);
    expect(c.preview).toEqual({ status: "idle" });
  });

  it("returns to select with a message when staging fails", () => {
    const s = run([
      { type: "fileChosen", fileName: "a.csv" },
      { type: "stagingFailed", message: "too large" },
    ]);
    expect(s).toEqual({ phase: "select", error: "too large" });
  });

  it("a path source starts without sheets and learns them from the first preview", () => {
    let s = run([{ type: "pathChosen", path: " C:\\data\\book.xlsx " }]);
    expect(configure(s).source).toEqual({ kind: "path", path: "C:\\data\\book.xlsx" });
    s = run(
      [
        { type: "previewStarted" },
        { type: "previewLoaded", meta: meta({ kind: "excel", sheets: ["A", "B"] }) },
      ],
      s,
    );
    const c = configure(s);
    expect(c.preview.status).toBe("ready");
    expect(c.sheets).toEqual(["A", "B"]);
    expect(c.selectedSheets).toEqual(["A"]);
    expect(c.fileKind).toBe("excel");
  });

  it("tracks preview loading, failure and recovery as options change", () => {
    let s = run([{ type: "staged", file: staged({ sheets: [], kind: "csv" }) }]);
    s = run([{ type: "previewStarted" }], s);
    expect(configure(s).preview.status).toBe("loading");
    s = run([{ type: "previewFailed", message: "bad delimiter" }], s);
    expect(canImport(s)).toBe(false);
    s = run(
      [
        { type: "optionsChanged", options: { delimiter: ";" } },
        { type: "previewStarted" },
        { type: "previewLoaded", meta: meta() },
      ],
      s,
    );
    expect(canImport(s)).toBe(true);
  });

  it("toggles sheets in file order and requires at least one", () => {
    let s = run([
      { type: "staged", file: staged() },
      { type: "sheetToggled", sheet: "Q3" },
      { type: "sheetToggled", sheet: "Q2" },
    ]);
    expect(configure(s).selectedSheets).toEqual(["Q1", "Q2", "Q3"]);
    s = run(
      [
        { type: "sheetToggled", sheet: "Q1" },
        { type: "sheetToggled", sheet: "Q2" },
        { type: "sheetToggled", sheet: "Q3" },
      ],
      s,
    );
    expect(configure(s).selectedSheets).toEqual([]);
    expect(canImport(s)).toBe(false);
  });

  it("previews the first selected sheet with the current options", () => {
    const s = configure(
      run([
        { type: "staged", file: staged() },
        { type: "optionsChanged", options: { delimiter: "", headerRow: 2 } },
        { type: "sheetToggled", sheet: "Q1" },
        { type: "sheetToggled", sheet: "Q3" },
      ]),
    );
    expect(buildPreviewRequest(s)).toEqual({
      stagedId: "s1",
      path: null,
      kind: null,
      options: { headerRow: 2, sheet: "Q3" },
      limit: 1000,
    });
  });

  it("submits one import per selected sheet, each its own dataset", () => {
    const s = configure(
      run([
        { type: "staged", file: staged() },
        { type: "sheetToggled", sheet: "Q2" },
        { type: "nameChanged", name: "Sales" },
      ]),
    );
    const requests = buildImportRequests(s);
    expect(requests.map((r) => r.sheet)).toEqual(["Q1", "Q2"]);
    expect(requests.map((r) => r.request.name)).toEqual(["Sales - Q1", "Sales - Q2"]);
    expect(requests.every((r) => r.request.stagedId === "s1" && r.request.path === null)).toBe(
      true,
    );
    expect(requests[1]?.request.options).toEqual({ sheet: "Q2" });
  });

  it("a single-source import sends the name and cleaned options", () => {
    const s = configure(
      run([
        { type: "pathChosen", path: "/x/a.csv" },
        { type: "optionsChanged", options: { delimiter: ";", encoding: "" } },
      ]),
    );
    expect(buildImportRequests(s)).toEqual([
      {
        sheet: null,
        request: {
          stagedId: null,
          path: "/x/a.csv",
          name: "a",
          kind: null,
          options: { delimiter: ";" },
        },
      },
    ]);
  });

  it("configure -> importing -> done, collecting dataset ids and warnings", () => {
    let s = run([
      { type: "staged", file: staged() },
      { type: "sheetToggled", sheet: "Q2" },
    ]);
    s = run(
      [
        {
          type: "importStarted",
          jobs: [
            { sheet: "Q1", job: job("j1", "queued", 1) },
            { sheet: "Q2", job: job("j2", "queued", 1) },
          ],
        },
      ],
      s,
    );
    expect(s.phase).toBe("importing");
    s = run([{ type: "jobUpdated", job: job("j1", "running", 2, { progress: 0.5 }) }], s);
    expect(s.phase).toBe("importing");
    s = run(
      [
        {
          type: "jobUpdated",
          job: job("j1", "succeeded", 3, { result: result("d1", ["2 columns renamed"]) }),
        },
        { type: "jobUpdated", job: job("j2", "succeeded", 2, { result: result("d2") }) },
      ],
      s,
    );
    expect(s).toEqual({ phase: "done", datasetIds: ["d1", "d2"], warnings: ["2 columns renamed"] });
  });

  it("ignores older snapshots of a job", () => {
    let s = run([
      { type: "pathChosen", path: "/a.csv" },
      { type: "importStarted", jobs: [{ sheet: null, job: job("j1", "running", 5) }] },
    ]);
    s = run([{ type: "jobUpdated", job: job("j1", "queued", 2) }], s);
    if (s.phase !== "importing") throw new Error("phase");
    expect(s.jobs[0]?.job.status).toBe("running");
  });

  it("a failed job ends in `failed`, keeps the options to go back to, and reports the finished ones", () => {
    let s = run([
      { type: "staged", file: staged() },
      { type: "sheetToggled", sheet: "Q2" },
    ]);
    s = run(
      [
        {
          type: "importStarted",
          jobs: [
            { sheet: "Q1", job: job("j1", "queued", 1) },
            { sheet: "Q2", job: job("j2", "queued", 1) },
          ],
        },
        { type: "jobUpdated", job: job("j1", "succeeded", 2, { result: result("d1") }) },
        { type: "jobUpdated", job: job("j2", "failed", 2, { error: "bad header" }) },
      ],
      s,
    );
    expect(s).toMatchObject({
      phase: "failed",
      message: 'Sheet "Q2": bad header',
      datasetIds: ["d1"],
    });
    const back = run([{ type: "back" }], s);
    expect(back.phase).toBe("configure");
    expect(configure(back).selectedSheets).toEqual(["Q1", "Q2"]);
  });

  it("a cancelled job is reported as cancelled", () => {
    const s = run([
      { type: "pathChosen", path: "/a.csv" },
      { type: "importStarted", jobs: [{ sheet: null, job: job("j1", "running", 1) }] },
      { type: "jobUpdated", job: job("j1", "cancelled", 2) },
    ]);
    expect(s).toMatchObject({
      phase: "failed",
      message: "Import of a was cancelled",
      cancelled: true,
    });
  });

  it("failing to start an import returns to options with the message", () => {
    const s = run([
      { type: "pathChosen", path: "/a.csv" },
      { type: "importFailedToStart", message: "409 conflict" },
    ]);
    expect(s).toMatchObject({ phase: "failed", message: "409 conflict" });
    expect((s as { config: ConfigureState }).config.phase).toBe("configure");
  });

  it("events that do not apply to the current phase are ignored", () => {
    expect(run([{ type: "previewStarted" }])).toBe(initialState);
    expect(run([{ type: "jobUpdated", job: job("x", "running", 1) }])).toBe(initialState);
  });

  it("reset returns to select from anywhere", () => {
    expect(run([{ type: "pathChosen", path: "/a.csv" }, { type: "reset" }])).toEqual(initialState);
  });
});
