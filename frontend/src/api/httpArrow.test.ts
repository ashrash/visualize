import { tableFromArrays, tableToIPC } from "apache-arrow";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./client";
import { isAbortError, postArrowOrJson } from "./httpArrow";

afterEach(() => vi.unstubAllGlobals());

const stub = (fn: (url: string, init?: RequestInit) => Promise<Response>) =>
  vi.stubGlobal("fetch", vi.fn(fn));

describe("postArrowOrJson", () => {
  it("returns an Arrow table when the response is an Arrow stream", async () => {
    const bytes = tableToIPC(tableFromArrays({ x: [1, 2, 3], y: ["a", "b", "c"] }), "stream");
    stub(
      async () =>
        new Response(bytes as unknown as BodyInit, {
          headers: { "Content-Type": "application/vnd.apache.arrow.stream" },
        }),
    );
    const result = await postArrowOrJson("/api/charts/data", { a: 1 });
    expect(result.kind).toBe("arrow");
    if (result.kind !== "arrow") return;
    expect(result.table.numRows).toBe(3);
    expect(result.table.getChild("y")?.toArray()).toEqual(["a", "b", "c"]);
    expect(result.meta).toBeUndefined(); // no viz.meta in this table
  });

  it("returns the parsed body for a JSON response", async () => {
    stub(
      async () =>
        new Response(JSON.stringify({ values: { n: 3 }, method: "box" }), {
          headers: { "Content-Type": "application/json" },
        }),
    );
    const result = await postArrowOrJson<{ method: string }>("/api/charts/data", {});
    expect(result).toEqual({ kind: "json", body: { values: { n: 3 }, method: "box" } });
  });

  it("sends the body as JSON with the given signal", async () => {
    const seen: { init?: RequestInit } = {};
    stub(async (_url, init) => {
      seen.init = init;
      return new Response("{}", { headers: { "Content-Type": "application/json" } });
    });
    const controller = new AbortController();
    await postArrowOrJson("/x", { spec: 1 }, controller.signal);
    expect(seen.init?.method).toBe("POST");
    expect(JSON.parse(String(seen.init?.body))).toEqual({ spec: 1 });
    expect(seen.init?.signal).toBe(controller.signal);
  });

  it("turns an error response into an ApiError carrying the detail", async () => {
    stub(
      async () =>
        new Response(JSON.stringify({ detail: "unknown column" }), {
          status: 422,
          headers: { "Content-Type": "application/json" },
        }),
    );
    const error = await postArrowOrJson("/x", {}).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 422, message: "unknown column" });
  });

  it("falls back to the status when the error body is not JSON", async () => {
    stub(async () => new Response("boom", { status: 500 }));
    await expect(postArrowOrJson("/x", {})).rejects.toMatchObject({
      status: 500,
      message: "Request failed (500)",
    });
  });

  it("reports an aborted request as an abort, not an ApiError", async () => {
    stub(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        }),
    );
    const controller = new AbortController();
    const pending = postArrowOrJson("/x", {}, controller.signal).catch((e: unknown) => e);
    controller.abort();
    const error = await pending;
    expect(isAbortError(error)).toBe(true);
    expect(error).not.toBeInstanceOf(ApiError);
  });
});
