import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./client";
import { postRows } from "./datasetRows";
import {
  deleteDataset,
  deleteStaged,
  fetchColumnValues,
  renameDataset,
  stageFile,
  startExport,
  startImport,
  startRefresh,
} from "./datasets";
import {
  checkFormula,
  inspectPipeline,
  listFormulaFunctions,
  materializePipeline,
} from "./pipelines";

const json = (body: unknown, status = 200) =>
  new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

function mockFetch(response: Response) {
  const fn = vi.fn(async (_url: string, _init?: RequestInit) => response);
  vi.stubGlobal("fetch", fn);
  return fn;
}
const lastCall = (fn: ReturnType<typeof mockFetch>) => {
  const [url, init] = fn.mock.calls.at(-1) ?? [];
  return { url: String(url), init: init as RequestInit };
};

afterEach(() => vi.unstubAllGlobals());

describe("data-core wrappers", () => {
  it("stages a file as multipart `file`", async () => {
    const fn = mockFetch(
      json({ stagedId: "s1", fileName: "a.csv", kind: "csv", sizeBytes: 3, sheets: [] }),
    );
    const staged = await stageFile(new File(["a,b"], "a.csv"));
    expect(staged.stagedId).toBe("s1");
    const { url, init } = lastCall(fn);
    expect(url).toBe("/api/datasets/staged");
    expect(init.method).toBe("POST");
    expect((init.body as FormData).get("file")).toBeInstanceOf(File);
  });

  it("deletes a staged upload", async () => {
    const fn = mockFetch(json(null, 204));
    await deleteStaged("s 1");
    expect(lastCall(fn)).toMatchObject({
      url: "/api/datasets/staged/s%201",
      init: { method: "DELETE" },
    });
  });

  it("starts an import and sends the camelCase request as JSON", async () => {
    const fn = mockFetch(json({ id: "j1", status: "queued" }, 202));
    await startImport({
      stagedId: "s1",
      path: null,
      name: "Sales",
      kind: "csv",
      options: { delimiter: ";" },
    });
    const { url, init } = lastCall(fn);
    expect(url).toBe("/api/datasets/import");
    expect(JSON.parse(String(init.body))).toEqual({
      stagedId: "s1",
      path: null,
      name: "Sales",
      kind: "csv",
      options: { delimiter: ";" },
    });
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("starts a refresh, renames, and deletes with the force flag", async () => {
    const fn = mockFetch(json({ id: "j" }, 202));
    await startRefresh("d1", { stagedId: null, options: null });
    expect(lastCall(fn).url).toBe("/api/datasets/d1/refresh");

    mockFetch(json({ id: "d1", name: "New" }));
    const renamed = mockFetch(json({ id: "d1", name: "New" }));
    await renameDataset("d1", "New");
    expect(lastCall(renamed)).toMatchObject({ url: "/api/datasets/d1", init: { method: "PATCH" } });
    expect(JSON.parse(String(lastCall(renamed).init.body))).toEqual({ name: "New" });

    const del = mockFetch(json(null, 204));
    await deleteDataset("d1", true);
    expect(lastCall(del).url).toBe("/api/datasets/d1?force=true");
  });

  it("surfaces a 409 from delete with the server's detail", async () => {
    mockFetch(json({ detail: "used by project X" }, 409));
    await expect(deleteDataset("d1", false)).rejects.toMatchObject({
      status: 409,
      message: "used by project X",
    });
  });

  it("posts column values and exports", async () => {
    const values = mockFetch(json({ field: "region", values: [], truncated: false }));
    await fetchColumnValues("d1", { field: "region", search: null, filters: [], limit: 100 });
    expect(lastCall(values).url).toBe("/api/datasets/d1/values");
    const exp = mockFetch(json({ id: "j" }, 202));
    await startExport({
      source: { kind: "dataset", id: "d1" },
      format: "csv",
      pipelines: [],
      version: null,
      filters: [],
      sort: [],
      columns: null,
      fileName: null,
      delimiter: null,
    });
    expect(lastCall(exp).url).toBe("/api/exports");
  });

  it("turns an error response from an Arrow endpoint into an ApiError", async () => {
    mockFetch(json({ detail: "bad sort field" }, 422));
    const error = await postRows("d1", {
      offset: 0,
      limit: 10,
      sort: [],
      filters: [],
      search: null,
      columns: null,
      version: null,
    }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 422, message: "bad sort field" });
  });
});

describe("transform wrappers", () => {
  const pipeline = { id: "p", name: "P", source: { kind: "dataset" as const, id: "d" }, steps: [] };

  it("inspects, checks formulas, lists functions and materializes", async () => {
    const inspect = mockFetch(
      json({ ok: true, sourceColumns: [], steps: [], columns: [], sql: null }),
    );
    await inspectPipeline({ pipeline, pipelines: [], includeSql: false });
    expect(lastCall(inspect).url).toBe("/api/pipelines/inspect");
    expect(JSON.parse(String(lastCall(inspect).init.body)).pipeline.id).toBe("p");

    const check = mockFetch(json({ ok: true, error: null, outputType: "DOUBLE", fields: [] }));
    await checkFormula({ expression: "a+1", pipeline, pipelines: [], beforeStepId: null });
    expect(lastCall(check).url).toBe("/api/pipelines/formula/check");

    const fns = mockFetch(
      json([{ name: "abs", category: "math", signature: "abs(x)", description: "" }]),
    );
    expect((await listFormulaFunctions())[0]?.name).toBe("abs");
    expect(lastCall(fns).url).toBe("/api/pipelines/formula/functions");

    const mat = mockFetch(json({ jobs: { s1: "j1" }, ready: [] }));
    expect((await materializePipeline({ pipeline, pipelines: [] })).jobs).toEqual({ s1: "j1" });
    expect(lastCall(mat).url).toBe("/api/pipelines/materialize");
  });
});
