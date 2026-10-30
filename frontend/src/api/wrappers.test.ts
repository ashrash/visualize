import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./client";
import { postRows } from "./datasetRows";
import {
  cancelJob,
  deleteDataset,
  deleteStaged,
  fetchColumnValues,
  listActiveJobs,
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
  const fn = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => response.clone());
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

/** The typed client calls fetch(Request); plain helpers call fetch(url, init). Read both. */
async function call(fn: ReturnType<typeof mockFetch>) {
  const [input, init] = fn.mock.calls.at(-1) ?? [];
  if (input instanceof Request) {
    const url = new URL(input.url);
    const text = await input.clone().text();
    return { url: url.pathname + url.search, method: input.method, text, request: input };
  }
  return {
    url: String(input),
    method: (init as RequestInit | undefined)?.method ?? "GET",
    text: typeof init?.body === "string" ? init.body : "",
    request: undefined,
  };
}

describe("data-core wrappers", () => {
  it("stages a file as multipart `file`", async () => {
    const fn = mockFetch(
      json({ stagedId: "s1", fileName: "a.csv", kind: "csv", sizeBytes: 3, sheets: [] }, 201),
    );
    const staged = await stageFile(new File(["a,b"], "a.csv"));
    expect(staged.stagedId).toBe("s1");
    const sent = await call(fn);
    expect(sent.url).toBe("/api/datasets/staged");
    expect(sent.method).toBe("POST");
    expect(sent.request?.headers.get("content-type")).toMatch(/multipart\/form-data/);
    expect(sent.text).toContain('name="file"');
  });

  it("deletes a staged upload", async () => {
    const fn = mockFetch(json(null, 204));
    await deleteStaged("s1");
    expect(await call(fn)).toMatchObject({ url: "/api/datasets/staged/s1", method: "DELETE" });
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
    const sent = await call(fn);
    expect(sent.url).toBe("/api/datasets/import");
    expect(JSON.parse(sent.text)).toEqual({
      stagedId: "s1",
      path: null,
      name: "Sales",
      kind: "csv",
      options: { delimiter: ";" },
    });
  });

  it("starts a refresh, renames, and deletes with the force flag", async () => {
    const refresh = mockFetch(json({ id: "j" }, 202));
    await startRefresh("d1", { stagedId: null, options: null });
    expect((await call(refresh)).url).toBe("/api/datasets/d1/refresh");

    const renamed = mockFetch(json({ id: "d1", name: "New" }));
    await renameDataset("d1", "New");
    const r = await call(renamed);
    expect(r).toMatchObject({ url: "/api/datasets/d1", method: "PATCH" });
    expect(JSON.parse(r.text)).toEqual({ name: "New" });

    const del = mockFetch(json(null, 204));
    await deleteDataset("d1", true);
    expect((await call(del)).url).toBe("/api/datasets/d1?force=true");
  });

  it("surfaces a 409 from delete with the server's detail", async () => {
    mockFetch(json({ detail: "latin is used by: uses data" }, 409));
    await expect(deleteDataset("d1", false)).rejects.toMatchObject({
      status: 409,
      message: "latin is used by: uses data",
    });
  });

  it("posts column values and exports", async () => {
    const values = mockFetch(json({ field: "region", values: [], truncated: false }));
    await fetchColumnValues("d1", { field: "region", search: null, filters: [], limit: 100 });
    expect((await call(values)).url).toBe("/api/datasets/d1/values");
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
    expect((await call(exp)).url).toBe("/api/exports");
  });

  it("lists active jobs and cancels one", async () => {
    const list = mockFetch(json([]));
    await listActiveJobs();
    expect((await call(list)).url).toBe("/api/jobs?active=true");
    const cancel = mockFetch(json({ id: "j1", status: "cancelled" }));
    await cancelJob("j1");
    expect(await call(cancel)).toMatchObject({ url: "/api/jobs/j1/cancel", method: "POST" });
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

  it("sends a None inside an in-predicate as JSON null (matches NULL on the server)", async () => {
    const fn = mockFetch(json({ detail: "stop" }, 422));
    await postRows("d1", {
      offset: 0,
      limit: 10,
      sort: [],
      filters: [{ op: "in", field: "amount", values: [10, null] }],
      search: "x",
      columns: null,
      version: null,
    }).catch(() => undefined);
    const sent = await call(fn);
    expect(JSON.parse(sent.text).filters[0].values).toEqual([10, null]);
    expect(JSON.parse(sent.text).search).toBe("x");
  });
});

describe("transform wrappers", () => {
  const pipeline = { id: "p", name: "P", source: { kind: "dataset" as const, id: "d" }, steps: [] };

  it("inspects, checks formulas, lists functions and materializes", async () => {
    const inspect = mockFetch(
      json({ ok: true, sourceColumns: [], steps: [], columns: [], sql: null }),
    );
    await inspectPipeline({ pipeline, pipelines: [], includeSql: false });
    const sentInspect = await call(inspect);
    expect(sentInspect).toMatchObject({ url: "/api/pipelines/inspect", method: "POST" });
    expect(JSON.parse(sentInspect.text).pipeline.id).toBe("p");

    const check = mockFetch(json({ ok: true, error: null, outputType: "DOUBLE", fields: [] }));
    await checkFormula({ expression: "a+1", pipeline, pipelines: [], beforeStepId: null });
    expect((await call(check)).url).toBe("/api/pipelines/formula/check");

    const fns = mockFetch(
      json([{ name: "abs", category: "math", signature: "abs(x)", description: "" }]),
    );
    expect((await listFormulaFunctions())[0]?.name).toBe("abs");
    expect(await call(fns)).toMatchObject({
      url: "/api/pipelines/formula/functions",
      method: "GET",
    });

    const mat = mockFetch(json({ jobs: { s1: "j1" }, ready: [] }));
    expect((await materializePipeline({ pipeline, pipelines: [] })).jobs).toEqual({ s1: "j1" });
    expect((await call(mat)).url).toBe("/api/pipelines/materialize");
  });

  it("shows calc-guard refusals as ordinary errors with the server's message", async () => {
    mockFetch(
      json({
        ok: false,
        error: { message: "an expression can't produce a list or struct", start: 0, end: 10 },
        outputType: null,
        fields: [],
      }),
    );
    const result = await checkFormula({
      expression: "[1, 2]",
      pipeline,
      pipelines: [],
      beforeStepId: null,
    });
    expect(result.error?.message).toMatch(/can't produce a list or struct/);
  });
});
